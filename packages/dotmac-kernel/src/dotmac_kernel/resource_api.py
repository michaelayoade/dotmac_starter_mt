"""Resource API kit: declare a resource, get the publishing mechanics (ADR-0079).

An app that owns records publishes them through ``/api/v1/<resource>``. The
plumbing every such resource needs is the same, and this module is that
plumbing so an adopter DECLARES a ``ResourceDeclaration`` instead of writing it:

* an opaque, tamper-evident **cursor** (``encode_cursor`` / ``decode_cursor``);
* a **changes feed** (``changes_page``) that pages by keyset on
  ``(updated_at, id)``, never by offset, and never serves a row newer than the
  **settle horizon**;
* the **tombstone** shape for a delete or a void (``Tombstone``);
* a **completeness summary** (``summarize``): a count and a digest over
  ``(id, version)`` for a window;
* **exact-scope read keys** built on the kernel machine keys
  (``ResourceScopes``): read, contact-read and import-write are separate exact
  scopes, none of them a wildcard.

## Why a settle horizon (the late-commit rule)

The feed orders by ``updated_at``. PostgreSQL ``now()`` is the TRANSACTION START
time, so a transaction that starts at 10:00:00 and commits at 10:00:40 stamps its
rows 10:00:00 yet becomes visible at 10:00:40. A reader that polled at 10:00:20
and advanced its cursor past 10:00:10 would never see that row. The helper
therefore serves only rows with ``updated_at < now - settle_horizon``. The
horizon MUST exceed the longest write transaction on the resource, and the
column MUST be written from one clock (the database's ``now()``); skew between
writer clocks counts against the horizon. The consumer additionally treats the
feed as at-least-once and de-duplicates on ``(id, version)``, so an overlapping
re-read is always safe. A commit-ordered sequence would remove the assumption;
the horizon needs no schema beyond the columns a versioned resource already has,
which is why it is the standard method.

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
allowed to read, because every page re-checks the key's scope.

Receives a ``Session``; issues SELECTs only; never commits.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dotmac_kernel.exceptions import BadRequestError, ForbiddenError
from dotmac_kernel.machine_auth import MachinePrincipal, require_machine_scope

#: Default hold-back. Longer than any write transaction a resource should have.
DEFAULT_SETTLE_HORIZON = timedelta(seconds=60)
#: A horizon below this is refused: it would reintroduce the skipped-commit hole.
MIN_SETTLE_HORIZON = timedelta(seconds=1)
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000
CURSOR_VERSION = 1


class InvalidCursorError(BadRequestError):
    """The cursor is not one this resource issued (corrupt, edited or foreign)."""


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
    three must be distinct, so no key reads by virtue of being allowed to write.
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
    in ``contact_fields`` are removed from it unless the caller holds
    ``scopes.contact_read``.
    """

    name: str
    model: Any
    scopes: ResourceScopes
    serialize: Callable[[Any], Mapping[str, Any]]
    id_column: str = "id"
    version_column: str = "version"
    updated_at_column: str = "updated_at"
    deleted_at_column: str | None = "deleted_at"
    contact_fields: frozenset[str] = field(default_factory=frozenset)
    settle_horizon: timedelta = DEFAULT_SETTLE_HORIZON
    api_version: str = "v1"

    def __post_init__(self) -> None:
        if not self.name or not all(c.isalnum() or c in "-_" for c in self.name):
            raise ValueError(f"resource name {self.name!r} must be [A-Za-z0-9_-]+")
        if self.settle_horizon < MIN_SETTLE_HORIZON:
            raise ValueError(
                f"settle_horizon {self.settle_horizon} is below the minimum "
                f"{MIN_SETTLE_HORIZON}; a shorter hold-back can skip a late commit"
            )
        for label, attribute in (
            ("id_column", self.id_column),
            ("version_column", self.version_column),
            ("updated_at_column", self.updated_at_column),
            ("deleted_at_column", self.deleted_at_column),
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
    """Refuse a key that does not hold the resource's exact read scope.

    Enforced inside the helpers as well as by the route dependency, so a route
    that forgets the dependency still cannot publish to a key outside its scope.
    """
    if not principal.has_scope(declaration.scopes.read):
        raise ForbiddenError(
            f"machine credential {principal.label!r} does not carry scope "
            f"{declaration.scopes.read!r}"
        )


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [item.as_dict() for item in self.items],
            "next_cursor": self.next_cursor,
            "has_more": self.has_more,
            "horizon": _iso(self.horizon),
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
        if not hmac_equal(_unb64(check_text), hashlib.sha256(body).digest()[:8]):
            raise ValueError("checksum")
        payload = json.loads(body)
        if payload["v"] != CURSOR_VERSION or payload["r"] != resource:
            raise ValueError("version or resource")
        return _utc(datetime.fromisoformat(payload["t"])), str(payload["i"])
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError):
        raise InvalidCursorError("cursor is not valid for this resource") from None


def hmac_equal(left: bytes, right: bytes) -> bool:
    import hmac

    return hmac.compare_digest(left, right)


# --- changes feed -------------------------------------------------------------


def _horizon(declaration: ResourceDeclaration, now: datetime | None) -> datetime:
    return _utc(now or datetime.now(UTC)) - declaration.settle_horizon


def _coerce_id(declaration: ResourceDeclaration, text: str) -> Any:
    python_type = declaration.column(declaration.id_column).type.python_type
    try:
        return python_type(text)
    except (ValueError, TypeError):
        raise InvalidCursorError("cursor is not valid for this resource") from None


def _shape(
    declaration: ResourceDeclaration, principal: MachinePrincipal, row: Any
) -> Mapping[str, Any]:
    record = dict(declaration.serialize(row))
    contact = declaration.contact_fields
    scope = declaration.scopes.contact_read
    if contact and not (scope is not None and principal.has_scope(scope)):
        for name in contact:
            record.pop(name, None)
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


def changes_page(
    db: Session,
    declaration: ResourceDeclaration,
    *,
    principal: MachinePrincipal,
    after: str | None = None,
    limit: int = DEFAULT_PAGE_SIZE,
    now: datetime | None = None,
) -> ChangesPage:
    """One page of changes strictly after ``after``, settled rows only.

    ``after=None`` starts from the beginning. Keyset on ``(updated_at, id)`` makes
    the position stable under inserts and ties; rows newer than the settle
    horizon are withheld until they are older than it.
    """
    require_read(principal, declaration)
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise BadRequestError(f"limit must be between 1 and {MAX_PAGE_SIZE}")

    updated = declaration.column(declaration.updated_at_column)
    ident = declaration.column(declaration.id_column)
    horizon = _horizon(declaration, now)

    stmt = sa.select(declaration.model).where(updated < horizon)
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
        items=items, next_cursor=next_cursor, has_more=has_more, horizon=horizon
    )


# --- completeness summary -----------------------------------------------------


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "resource": self.resource,
            "from": _iso(self.window_from),
            "to": _iso(self.window_to),
            "count": self.count,
            "digest": self.digest,
            "settled": self.settled,
        }


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

    Tombstones are rows and are counted, so a void changes the digest.
    """
    require_read(principal, declaration)
    start, end = _utc(window_from), _utc(window_to)
    if end <= start:
        raise BadRequestError("window_to must be after window_from")
    horizon = _horizon(declaration, now)
    effective_end = min(end, horizon)

    updated = declaration.column(declaration.updated_at_column)
    ident = declaration.column(declaration.id_column)
    version = declaration.column(declaration.version_column)
    pairs = db.execute(
        sa.select(ident, version).where(updated >= start, updated < effective_end)
    ).all()
    lines = sorted(f"{row[0]}:{int(row[1])}" for row in pairs)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
    return ResourceSummary(
        resource=declaration.name,
        window_from=start,
        window_to=effective_end,
        count=len(lines),
        digest=digest,
        settled=effective_end >= end,
    )


__all__ = [
    "CURSOR_VERSION",
    "ChangeItem",
    "ChangesPage",
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_SETTLE_HORIZON",
    "InvalidCursorError",
    "MAX_PAGE_SIZE",
    "MIN_SETTLE_HORIZON",
    "ResourceDeclaration",
    "ResourceScopes",
    "ResourceSummary",
    "Tombstone",
    "changes_page",
    "decode_cursor",
    "encode_cursor",
    "require_read",
    "summarize",
]
