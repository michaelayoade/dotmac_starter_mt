"""Resource API kit: declare a resource, get the publishing mechanics (ADR-0079).

An app that owns records publishes them through ``/api/v1/<resource>``. The
plumbing every such resource needs is the same, and this module is that
plumbing so an adopter DECLARES a ``ResourceDeclaration`` instead of writing it:

* an opaque, tamper-evident **cursor** (``encode_cursor`` / ``decode_cursor``);
* a **changes feed** (``changes_page``) that pages by keyset on
  ``(updated_at, id)``, never by offset, and never serves a row that is not yet
  **settled**;
* the **tombstone** shape for a delete or a void (``Tombstone``);
* a **completeness summary** (``summarize``): a count and a digest over
  ``(id, version)`` for a window, plus the means to re-read that window;
* **exact-scope read keys** built on the kernel machine keys
  (``ResourceScopes``): read, contact-read and import-write are separate exact
  scopes, none of them a wildcard, and read and import-write never share a key.

## What "settled" means, and what is guaranteed

The feed orders by ``updated_at``. A transaction that stamps a row and commits
later makes it visible AFTER a reader may have advanced past its stamp, so a
bare ``updated_at`` cursor can skip it. Three controls bound that, none of them
absolute:

1. **Database clock (B1).** The horizon is ``clock_timestamp() - settle_horizon``
   computed IN the database, never from the reader's Python clock.
2. **Open-transaction fence (B2, PostgreSQL).** The effective horizon is the
   LEAST of that and the start time of the oldest transaction open in this
   database (``pg_stat_activity.xact_start``, excluding the reader's own
   backend) and of any prepared transaction. A transaction of any length is
   therefore held back until it ends. The fence needs no privilege beyond what
   every role has, but ``pg_stat_activity`` shows other roles' sessions only as
   ``<insufficient privilege>`` rows with every other column NULL. When such
   sessions exist the reader cannot see their transactions; the declaration's
   ``unseen_sessions`` policy decides: ``"refuse"`` (default, fail-safe: the
   call raises ``ResourceUnavailableError`` rather than claim completeness it
   cannot know) or ``"horizon_only"`` (an explicit choice to rely on the
   hold-back alone; the page and summary say so in ``fence``). The kit does not
   require, and nothing should grant, ``pg_read_all_stats``, and the reader must
   NOT be made a member of the writer roles (that hands it every privilege the
   writers hold). The ways to make sessions visible, each needing Michael's
   approval, are: (a) the reader and the writers are the same runtime role;
   (b) a narrow SECURITY DEFINER fence function (client backends only, no query
   text); or (c) ``horizon_only`` declared per resource. Not
   PostgreSQL (the unit-test SQLite): hold-back only, ``fence`` says so.
3. **Trigger stamping (B3).** ``updated_at`` and ``version`` are stamped by a
   database trigger (``stamping_trigger_ddl``), not by application code. The
   kernel ``TimestampMixin`` is NOT enough: its ``onupdate`` uses the writer's
   application clock at flush time while inserts use the database ``now()``,
   which breaks the single-clock precondition. A declaration must attest
   ``stamped_by_trigger=True`` and adopters verify it with
   ``stamping_trigger_installed``.

Residuals, stated plainly: writers that cannot be seen (policy
``"horizon_only"``) are covered only by the hold-back, so a transaction longer
than the horizon can still commit behind a cursor; batch jobs must commit in
batches shorter than the horizon; a logical restore or clock step can move
stamps backwards. The consumer treats the feed as at-least-once and
de-duplicates on ``(id, version)``, and a period is closed only on a summary
computed at close time (``verify_period``) with ``changes_page(since=...)`` as
the re-read path when it does not match.

## What a published resource must be

* ``version``: an integer that increases on EVERY change. ``updated_at`` is
  metadata and is not the identity of a change.
* A delete is a SOFT delete: the row stays, ``deleted_at`` is set, ``version``
  and ``updated_at`` advance, and the feed emits a ``Tombstone``. A hard-deleted
  row cannot be told about, so a resource that is published must not be hard
  deleted.

## Not a secret

The cursor is opaque so that callers do not build on its structure. Its checksum
detects corruption and accidental edits; it is not authentication. A forged
cursor can only move a caller to another position inside data its key is already
allowed to read, because every page re-checks the key's scope and tenant.

Receives a ``Session``; issues SELECTs only; never commits.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dotmac_kernel.exceptions import BadRequestError, DomainError, ForbiddenError
from dotmac_kernel.machine_auth import MachinePrincipal, require_machine_scope

#: Default hold-back. Longer than any write transaction a resource should have.
DEFAULT_SETTLE_HORIZON = timedelta(seconds=60)
#: A horizon below this is refused: it would reintroduce the skipped-commit hole.
MIN_SETTLE_HORIZON = timedelta(seconds=1)
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000
#: Widest window a single summary may cover (B12). Close long periods in slices.
DEFAULT_MAX_SUMMARY_WINDOW = timedelta(days=93)
CURSOR_VERSION = 1

UnseenSessionsPolicy = Literal["refuse", "horizon_only"]

_IDENT = re.compile(r"[a-z_][a-z0-9_]*")


class InvalidCursorError(BadRequestError):
    """The cursor is not one this resource issued (corrupt, edited or foreign)."""


class ResourceUnavailableError(DomainError):
    """The settle fence cannot be established, so completeness cannot be claimed."""


# --- scope declaration --------------------------------------------------------


def _check_scope(label: str, scope: str) -> str:
    if not scope or scope.strip() != scope or any(c.isspace() for c in scope):
        raise ValueError(f"{label} scope {scope!r} must be a non-empty, trimmed key")
    if "*" in scope:
        raise ValueError(
            f"{label} scope {scope!r} contains a wildcard; machine scopes are exact"
        )
    return scope


@dataclass(frozen=True, slots=True)
class ResourceScopes:
    """The exact machine scopes a resource is published under.

    ``read`` opens the resource without contact details. ``contact_read`` is a
    SEPARATE scope for email, phone and service address; a resource that declares
    ``contact_fields`` but no ``contact_read`` never exposes them at all.
    ``import_write`` admits the idempotent import API and nothing else. The
    three must be distinct, and a key holding ``import_write`` is refused by the
    feed and summary while a key holding ``read`` or ``contact_read`` is refused
    by the import API (B9): no key both reads and writes a resource.
    """

    read: str
    contact_read: str | None = None
    import_write: str | None = None

    def __post_init__(self) -> None:
        declared = {"read": self.read}
        if self.contact_read is not None:
            declared["contact_read"] = self.contact_read
        if self.import_write is not None:
            declared["import_write"] = self.import_write
        for label, scope in declared.items():
            _check_scope(label, scope)
        if len(set(declared.values())) != len(declared):
            raise ValueError("read, contact_read and import_write must be distinct")


@dataclass(frozen=True, slots=True)
class ResourceDeclaration:
    """Everything the kit needs to publish one resource.

    ``model`` is a mapped class; the ``*_column`` fields name its attributes.
    ``serialize`` returns the Starter module's own schema for a live row (the
    canonical shape is the owning module's, not the kit's). Contact fields named
    in ``contact_fields`` are removed at ANY depth unless the caller holds
    ``scopes.contact_read``.

    ``stamped_by_trigger`` is an attestation with no default: the published
    table's ``updated_at`` and ``version`` are stamped by the database trigger
    from ``stamping_trigger_ddl``, not by application code. ``False`` is refused.
    ``tenant_id_column`` (default ``tenant_id``; ``None`` only for a table with
    no tenant column) scopes every read to the key's tenant.
    """

    name: str
    model: Any
    scopes: ResourceScopes
    serialize: Callable[[Any], Mapping[str, Any]]
    stamped_by_trigger: bool
    id_column: str = "id"
    version_column: str = "version"
    updated_at_column: str = "updated_at"
    deleted_at_column: str | None = "deleted_at"
    tenant_id_column: str | None = "tenant_id"
    contact_fields: frozenset[str] = field(default_factory=frozenset)
    settle_horizon: timedelta = DEFAULT_SETTLE_HORIZON
    max_summary_window: timedelta = DEFAULT_MAX_SUMMARY_WINDOW
    unseen_sessions: UnseenSessionsPolicy = "refuse"
    api_version: str = "v1"

    def __post_init__(self) -> None:
        if not self.name or not all(c.isalnum() or c in "-_" for c in self.name):
            raise ValueError(f"resource name {self.name!r} must be [A-Za-z0-9_-]+")
        if self.stamped_by_trigger is not True:
            raise ValueError(
                "a published resource must stamp updated_at and version by a "
                "database trigger (stamping_trigger_ddl); application-clock "
                "stamping breaks the single-clock precondition"
            )
        if self.settle_horizon < MIN_SETTLE_HORIZON:
            raise ValueError(
                f"settle_horizon {self.settle_horizon} is below the minimum "
                f"{MIN_SETTLE_HORIZON}; a shorter hold-back can skip a late commit"
            )
        if self.max_summary_window <= timedelta(0):
            raise ValueError("max_summary_window must be positive")
        if self.unseen_sessions not in ("refuse", "horizon_only"):
            raise ValueError("unseen_sessions must be 'refuse' or 'horizon_only'")
        for label, attribute in (
            ("id_column", self.id_column),
            ("version_column", self.version_column),
            ("updated_at_column", self.updated_at_column),
            ("deleted_at_column", self.deleted_at_column),
            ("tenant_id_column", self.tenant_id_column),
        ):
            if attribute is not None and not hasattr(self.model, attribute):
                raise ValueError(f"{label} {attribute!r} is not on the model")

    def column(self, attribute: str) -> Any:
        return getattr(self.model, attribute)

    def read_dependency(self) -> Any:
        """FastAPI dependency admitting exactly the keys holding ``scopes.read``."""
        return require_machine_scope(self.scopes.read)

    def import_dependency(self) -> Any:
        if self.scopes.import_write is None:
            raise ValueError(f"resource {self.name!r} declares no import scope")
        return require_machine_scope(self.scopes.import_write)


def require_read(principal: MachinePrincipal, declaration: ResourceDeclaration) -> None:
    """Refuse a key that lacks the exact read scope, or that also holds import-write.

    Enforced inside the helpers as well as by the route dependency, so a route
    that forgets the dependency still cannot publish to a key outside its scope.
    """
    if not principal.has_scope(declaration.scopes.read):
        raise ForbiddenError(
            f"machine credential {principal.label!r} does not carry scope "
            f"{declaration.scopes.read!r}"
        )
    write = declaration.scopes.import_write
    if write is not None and principal.has_scope(write):
        raise ForbiddenError(
            f"machine credential {principal.label!r} holds the import scope for "
            f"{declaration.name!r}; a key may read or write a resource, not both"
        )


def check_session_tenant(db: Session, principal: MachinePrincipal) -> None:
    """Refuse a key whose tenant is not the tenant the session is scoped to (B11).

    On PostgreSQL the session tenant is ``app.current_tenant`` (what RLS keys on).
    Unset is refused: a reader with no established tenant must not be served.
    Other dialects (the unit-test SQLite) have no session tenant; the per-row
    ``tenant_id`` filter still applies.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    current = db.execute(
        sa.text("SELECT current_setting('app.current_tenant', true)")
    ).scalar_one()
    if not current:
        raise ForbiddenError("no tenant is established for this session")
    if str(current).lower() != str(principal.tenant_id).lower():
        raise ForbiddenError("the key's tenant is not the session tenant")


# --- trigger stamping ---------------------------------------------------------


def _ident(value: str) -> str:
    if not _IDENT.fullmatch(value):
        raise ValueError(f"{value!r} is not a plain lower-case SQL identifier")
    return value


def _trigger_names(table: str, schema: str | None) -> tuple[str, str, str]:
    table = _ident(table)
    qualified_table = f"{_ident(schema)}.{table}" if schema else table
    prefix = f"{_ident(schema)}." if schema else ""
    return qualified_table, f"{prefix}dotmac_stamp_{table}", f"dotmac_stamp_{table}"


def stamping_trigger_ddl(
    table: str,
    *,
    schema: str | None = None,
    updated_at_column: str = "updated_at",
    version_column: str = "version",
) -> tuple[str, ...]:
    """PostgreSQL DDL that stamps ``updated_at`` and ``version`` in the database.

    INSERT sets ``version = 1`` and ``updated_at = clock_timestamp()``; an UPDATE
    that changes any column sets ``version = OLD.version + 1`` and a fresh
    ``updated_at``, whatever the statement supplied, so application code cannot
    forge or forget either. Run each statement in the owning module's migration.
    """
    qualified_table, function, trigger = _trigger_names(table, schema)
    updated, version = _ident(updated_at_column), _ident(version_column)
    return (
        f"CREATE OR REPLACE FUNCTION {function}() RETURNS trigger "
        "LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$ BEGIN "
        "IF TG_OP = 'INSERT' THEN "
        f"NEW.{version} := 1; NEW.{updated} := clock_timestamp(); "
        f"ELSIF NEW IS DISTINCT FROM OLD THEN "
        f"NEW.{version} := OLD.{version} + 1; NEW.{updated} := clock_timestamp(); "
        "END IF; RETURN NEW; END $$",
        f"CREATE TRIGGER {trigger} BEFORE INSERT OR UPDATE ON {qualified_table} "
        f"FOR EACH ROW EXECUTE FUNCTION {function}()",
    )


def drop_stamping_trigger_ddl(
    table: str, *, schema: str | None = None
) -> tuple[str, ...]:
    qualified_table, function, trigger = _trigger_names(table, schema)
    return (
        f"DROP TRIGGER IF EXISTS {trigger} ON {qualified_table}",
        f"DROP FUNCTION IF EXISTS {function}()",
    )


def stamping_trigger_installed(
    db: Session, table: str, *, schema: str | None = None
) -> bool | None:
    """True if the trigger is installed and enabled; ``None`` off PostgreSQL."""
    if db.get_bind().dialect.name != "postgresql":
        return None
    qualified_table, _function, trigger = _trigger_names(table, schema)
    found = db.execute(
        sa.text(
            "SELECT 1 FROM pg_trigger WHERE tgrelid = CAST(:rel AS regclass) "
            "AND tgname = :name AND NOT tgisinternal AND tgenabled <> 'D'"
        ),
        {"rel": qualified_table, "name": trigger},
    ).first()
    return found is not None


# --- tombstone and change envelope -------------------------------------------


@dataclass(frozen=True, slots=True)
class Tombstone:
    """A delete or void, published as a record rather than as a missing row."""

    id: str
    version: int
    deleted_at: datetime | None
    updated_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "version": self.version,
            "deleted": True,
            "deleted_at": _iso(self.deleted_at) if self.deleted_at else None,
            "updated_at": _iso(self.updated_at),
        }


@dataclass(frozen=True, slots=True)
class ChangeItem:
    id: str
    version: int
    updated_at: datetime
    record: Mapping[str, Any] | None
    tombstone: Tombstone | None

    @property
    def deleted(self) -> bool:
        return self.tombstone is not None

    def as_dict(self) -> dict[str, Any]:
        if self.tombstone is not None:
            return self.tombstone.as_dict()
        return {
            "id": self.id,
            "version": self.version,
            "deleted": False,
            "updated_at": _iso(self.updated_at),
            "record": dict(self.record or {}),
        }


@dataclass(frozen=True, slots=True)
class ChangesPage:
    items: tuple[ChangeItem, ...]
    #: Pass back as ``after``. Unchanged from the input when the page is empty.
    next_cursor: str | None
    has_more: bool
    #: Rows at or after this instant were withheld as not yet settled.
    horizon: datetime
    #: How the horizon was fenced: ``pg_stat_activity``,
    #: ``horizon_only_unseen_sessions``
    #: or ``horizon_only_non_postgres``.
    fence: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "horizon": _iso(self.horizon),
            "fence": self.fence,
        }


# --- cursor -------------------------------------------------------------------


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode_cursor(resource: str, updated_at: datetime, record_id: object) -> str:
    """The opaque position just AFTER ``(updated_at, record_id)`` on ``resource``."""
    body = json.dumps(
        {
            "v": CURSOR_VERSION,
            "r": resource,
            "t": _iso(updated_at),
            "i": str(record_id),
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"{_b64(body)}.{_b64(hashlib.sha256(body).digest()[:8])}"


def decode_cursor(resource: str, cursor: str) -> tuple[datetime, str]:
    """Return ``(updated_at, id)``; raise ``InvalidCursorError`` on anything else.

    A cursor issued for another resource is refused, so a position cannot be
    carried from one feed to another.
    """
    try:
        body_text, check_text = cursor.split(".")
        body = _unb64(body_text)
        if not hmac.compare_digest(
            _unb64(check_text), hashlib.sha256(body).digest()[:8]
        ):
            raise ValueError("checksum")
        payload = json.loads(body)
        if payload["v"] != CURSOR_VERSION or payload["r"] != resource:
            raise ValueError("version or resource")
        return _utc(datetime.fromisoformat(payload["t"])), str(payload["i"])
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError):
        raise InvalidCursorError("cursor is not valid for this resource") from None


# --- settle bound (B1, B2) ----------------------------------------------------


def require_read_committed(db: Session) -> None:
    """Refuse unless the transaction is READ COMMITTED (PostgreSQL only).

    The settle fence and the import lock are correct only when each statement
    sees a fresh snapshot: under REPEATABLE READ an earlier SELECT fixes the
    snapshot before the bound is computed or the lock is taken.
    """
    level = db.execute(
        sa.text("SELECT current_setting('transaction_isolation')")
    ).scalar_one()
    if level != "read committed":
        raise ResourceUnavailableError(
            f"transaction isolation is {level!r}; the settle fence and import "
            "lock require 'read committed'"
        )


@dataclass(frozen=True, slots=True)
class SettleBound:
    horizon: datetime
    fence: str
    #: The database clock when the bound was computed.
    computed_at: datetime


_PG_BOUND = sa.text(
    """
    SELECT clock_timestamp() AS computed_at,
           LEAST(
             clock_timestamp() - make_interval(secs => :secs),
             (SELECT min(xact_start) FROM pg_stat_activity
               WHERE datname = current_database() AND pid <> pg_backend_pid()
                 AND xact_start IS NOT NULL),
             (SELECT min(prepared) FROM pg_prepared_xacts
               WHERE database = current_database())
           ) AS horizon,
           (SELECT count(*) FROM pg_stat_activity
             WHERE datname = current_database() AND pid <> pg_backend_pid()
               AND backend_type IS NULL) AS unseen
    """
)


def settle_bound(
    db: Session, declaration: ResourceDeclaration, *, now: datetime | None = None
) -> SettleBound:
    """The instant before which rows are settled, from the DATABASE clock.

    ``now`` is a test seam honoured only off PostgreSQL; on PostgreSQL the
    database clock and open-transaction fence decide.
    """
    seconds = declaration.settle_horizon.total_seconds()
    if db.get_bind().dialect.name == "postgresql":
        require_read_committed(db)
        # pg_stat_activity is snapshotted once per transaction; a reader paging
        # twice in one transaction would otherwise fence on a stale view.
        db.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
        row = db.execute(_PG_BOUND, {"secs": seconds}).one()
        fence = "pg_stat_activity"
        if row.unseen:
            if declaration.unseen_sessions == "refuse":
                raise ResourceUnavailableError(
                    f"{row.unseen} session(s) in this database are not visible to "
                    "the reader role, so open transactions cannot be fenced. Options, "
                    "each needing approval: reader and writers share one runtime role; "
                    "a narrow SECURITY DEFINER fence function; or "
                    "unseen_sessions='horizon_only' on the resource"
                )
            fence = "horizon_only_unseen_sessions"
        return SettleBound(
            horizon=_utc(row.horizon), fence=fence, computed_at=_utc(row.computed_at)
        )
    clock = _utc(
        now if now is not None else db.execute(sa.select(sa.func.now())).scalar_one()
    )
    return SettleBound(
        horizon=clock - declaration.settle_horizon,
        fence="horizon_only_non_postgres",
        computed_at=clock,
    )


# --- shaping -------------------------------------------------------------------


def _strip_contact(value: Any, names: frozenset[str]) -> Any:
    """Remove contact fields at ANY depth, in mappings inside lists inside mappings."""
    if isinstance(value, Mapping):
        return {
            key: _strip_contact(item, names)
            for key, item in value.items()
            if key not in names
        }
    if isinstance(value, list | tuple):
        return [_strip_contact(item, names) for item in value]
    return value


def _shape(
    declaration: ResourceDeclaration, principal: MachinePrincipal, row: Any
) -> Mapping[str, Any]:
    record = dict(declaration.serialize(row))
    contact = declaration.contact_fields
    scope = declaration.scopes.contact_read
    if contact and not (scope is not None and principal.has_scope(scope)):
        record = _strip_contact(record, contact)
    return record


def _item(
    declaration: ResourceDeclaration, principal: MachinePrincipal, row: Any
) -> ChangeItem:
    record_id = str(getattr(row, declaration.id_column))
    version = int(getattr(row, declaration.version_column))
    updated_at = _utc(getattr(row, declaration.updated_at_column))
    deleted_at = (
        getattr(row, declaration.deleted_at_column)
        if declaration.deleted_at_column
        else None
    )
    if deleted_at is not None:
        return ChangeItem(
            id=record_id,
            version=version,
            updated_at=updated_at,
            record=None,
            tombstone=Tombstone(
                id=record_id,
                version=version,
                deleted_at=_utc(deleted_at),
                updated_at=updated_at,
            ),
        )
    return ChangeItem(
        id=record_id,
        version=version,
        updated_at=updated_at,
        record=_shape(declaration, principal, row),
        tombstone=None,
    )


def _coerce_id(declaration: ResourceDeclaration, text: str) -> Any:
    python_type = declaration.column(declaration.id_column).type.python_type
    try:
        return python_type(text)
    except (ValueError, TypeError):
        raise InvalidCursorError("cursor is not valid for this resource") from None


def _tenant_filter(
    declaration: ResourceDeclaration, principal: MachinePrincipal
) -> Any:
    if declaration.tenant_id_column is None:
        return sa.true()
    return declaration.column(declaration.tenant_id_column) == principal.tenant_id


# --- changes feed -------------------------------------------------------------


def changes_page(
    db: Session,
    declaration: ResourceDeclaration,
    *,
    principal: MachinePrincipal,
    after: str | None = None,
    since: datetime | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    now: datetime | None = None,
) -> ChangesPage:
    """One page of changes strictly after ``after``, settled rows only.

    ``after=None`` starts from the beginning. ``since`` starts at an instant
    instead (``updated_at >= since``) and is the RE-READ path (B4): a receiver
    whose period summary does not match re-reads the window from its start with
    ``since=window_from`` and de-duplicates on ``(id, version)``. ``after`` and
    ``since`` are mutually exclusive.
    """
    require_read(principal, declaration)
    check_session_tenant(db, principal)
    if after is not None and since is not None:
        raise BadRequestError("after and since are mutually exclusive")
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise BadRequestError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

    updated = declaration.column(declaration.updated_at_column)
    ident = declaration.column(declaration.id_column)
    bound = settle_bound(db, declaration, now=now)

    stmt = sa.select(declaration.model).where(
        updated < bound.horizon, _tenant_filter(declaration, principal)
    )
    if since is not None:
        stmt = stmt.where(updated >= _utc(since))
    if after is not None:
        position, raw_id = decode_cursor(declaration.name, after)
        record_id = _coerce_id(declaration, raw_id)
        stmt = stmt.where(
            sa.or_(updated > position, sa.and_(updated == position, ident > record_id))
        )
    rows = list(
        db.execute(stmt.order_by(updated, ident).limit(limit + 1)).scalars().all()
    )
    has_more = len(rows) > limit
    items = tuple(_item(declaration, principal, row) for row in rows[:limit])
    next_cursor = (
        encode_cursor(declaration.name, items[-1].updated_at, items[-1].id)
        if items
        else after
    )
    return ChangesPage(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
        horizon=bound.horizon,
        fence=bound.fence,
    )


# --- completeness summary -----------------------------------------------------


def digest_pairs(pairs: Iterable[tuple[object, int]]) -> tuple[int, str]:
    """``(count, digest)`` over ``(id, version)`` pairs, order independent."""
    lines = sorted(f"{record_id}:{int(version)}" for record_id, version in pairs)
    return len(lines), hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ResourceSummary:
    resource: str
    window_from: datetime
    #: The window end actually summarised: the requested end, or the horizon.
    window_to: datetime
    count: int
    #: sha256 over the sorted ``id:version`` lines in the window.
    digest: str
    #: False when the window was cut at the horizon; a receiver must not close
    #: the period on an unsettled summary.
    settled: bool
    #: The database clock when the summary was computed. A period closes only on
    #: a summary computed at close time, never a cached one.
    computed_at: datetime
    fence: str

    @property
    def reread_since(self) -> datetime:
        """Pass to ``changes_page(since=...)`` to re-read this window on a mismatch."""
        return self.window_from

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "from": _iso(self.window_from),
            "to": _iso(self.window_to),
            "count": self.count,
            "digest": self.digest,
            "settled": self.settled,
            "computed_at": _iso(self.computed_at),
            "fence": self.fence,
        }


def verify_period(
    summary: ResourceSummary, local_pairs: Iterable[tuple[object, int]]
) -> bool:
    """True only if the summary is settled and matches the receiver's own pairs.

    The receiver builds ``local_pairs`` from what it holds for the same window at
    the moment it asks to close; on ``False`` it re-reads the window with
    ``changes_page(since=summary.reread_since)`` and checks again.
    """
    if not summary.settled:
        return False
    count, digest = digest_pairs(local_pairs)
    return count == summary.count and hmac.compare_digest(digest, summary.digest)


def summarize(
    db: Session,
    declaration: ResourceDeclaration,
    *,
    principal: MachinePrincipal,
    window_from: datetime,
    window_to: datetime,
    now: datetime | None = None,
) -> ResourceSummary:
    """Count and digest ``(id, version)`` for ``[window_from, window_to)``.

    Tombstones are rows and are counted, so a void changes the digest. The window
    is bounded by ``declaration.max_summary_window`` (B12).
    """
    require_read(principal, declaration)
    check_session_tenant(db, principal)
    start, end = _utc(window_from), _utc(window_to)
    if end <= start:
        raise BadRequestError("window_to must be after window_from")
    if end - start > declaration.max_summary_window:
        raise BadRequestError(
            f"window exceeds the maximum of {declaration.max_summary_window}"
        )
    bound = settle_bound(db, declaration, now=now)
    effective_end = min(end, bound.horizon)

    updated = declaration.column(declaration.updated_at_column)
    ident = declaration.column(declaration.id_column)
    version = declaration.column(declaration.version_column)
    pairs = db.execute(
        sa.select(ident, version).where(
            updated >= start,
            updated < effective_end,
            _tenant_filter(declaration, principal),
        )
    ).all()
    count, digest = digest_pairs((row[0], row[1]) for row in pairs)
    return ResourceSummary(
        resource=declaration.name,
        window_from=start,
        window_to=effective_end,
        count=count,
        digest=digest,
        settled=effective_end >= end,
        computed_at=bound.computed_at,
        fence=bound.fence,
    )


__all__ = [
    "CURSOR_VERSION",
    "ChangeItem",
    "ChangesPage",
    "DEFAULT_MAX_SUMMARY_WINDOW",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_SETTLE_HORIZON",
    "InvalidCursorError",
    "MAX_PAGE_SIZE",
    "MIN_SETTLE_HORIZON",
    "ResourceDeclaration",
    "ResourceScopes",
    "ResourceSummary",
    "ResourceUnavailableError",
    "SettleBound",
    "Tombstone",
    "UnseenSessionsPolicy",
    "changes_page",
    "check_session_tenant",
    "decode_cursor",
    "digest_pairs",
    "drop_stamping_trigger_ddl",
    "encode_cursor",
    "require_read",
    "require_read_committed",
    "settle_bound",
    "stamping_trigger_ddl",
    "stamping_trigger_installed",
    "summarize",
    "verify_period",
]
