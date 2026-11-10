"""Idempotent import by source reference (ADR-0079).

A receiving app accepts a copy of a record another app owns through
``PUT /api/v1/imports/<resource>/{external_ref}``. This module is the verdict
logic for that endpoint, built on the kernel idempotency ledger
(``dotmac_kernel.idempotency``); it adds no table.

The ledger key is ``(source system, record type, external reference, version)``
with a content digest:

* the SOURCE SYSTEM is the authenticated key's ``source_application``, never a
  field of the body (a body cannot claim to be another system);
* the same key and the same digest is a NO-OP (``unchanged``) and the effect does
  not run again;
* the same key with a DIFFERENT digest is ``conflict``: refused, nothing written,
  and the caller is expected to pause the binding rather than retry;
* a LOWER version than one already imported is ``stale`` and writes nothing;
* otherwise the effect runs once and the verdict is ``created`` (first version
  seen for the reference) or ``updated``.

Mapping onto ledger columns: ``scope = "import:<source>:<record_type>"``,
``key = "<external_ref>@<version>"``, ``fingerprint = digest``. Rows are written
with no ``expires_at``: integrity of a replay must not depend on a retention
window, so a replay long after any receipt has been redacted still answers
``unchanged``. Nothing is written before the effect (the effect and the ledger
row commit together), so a crash leaves no row and a retry re-drives cleanly.

Concurrency: two requests for the SAME key are also serialised by the ledger's
unique constraint.

Concurrency (B6): ``_lock_reference`` takes a transaction-scoped advisory lock on
(tenant, scope, reference) before the prior-version read, so importers of one
reference are serialised and a racing v3 and v5 end with one applied and one
``stale`` or ``updated`` decided on the true prior state. The stored result is a
small flat id map only (B7, ``flat_id_map``): the ledger is permanent and must
never hold payload or personal data. Read and import-write scopes are mutually
exclusive on a key (B9) and the key's tenant must be the session tenant (B11).

``blocked:predecessor`` and ``refused:<code>`` are raised by ``apply`` as
``ImportBlocked`` / ``ImportRefused``; they roll the effect back, record nothing,
and come back as a result rather than an exception so the endpoint can answer
from the closed set. A refusal never echoes the payload.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel.exceptions import BadRequestError, ForbiddenError
from dotmac_kernel.fingerprints import fingerprint_of
from dotmac_kernel.idempotency import (
    MAX_KEY_LENGTH,
    MAX_SCOPE_LENGTH,
    IdempotencyConflict,
    execute_once,
)
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.machine_auth import MachinePrincipal
from dotmac_kernel.resource_api import (
    ResourceDeclaration,
    check_session_tenant,
    require_read_committed,
)

_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]*")
_CODE = re.compile(r"[a-z0-9_]{1,64}")
IMPORT_SCOPE_PREFIX = "import"
MAX_VERSION = 10**15


class ImportVerdict(str, Enum):
    """The closed answer set. ``refused`` carries a code; see ``ImportResult.wire``."""

    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    STALE = "stale"
    CONFLICT = "conflict"
    BLOCKED_PREDECESSOR = "blocked:predecessor"
    REFUSED = "refused"


class ImportRefused(Exception):
    """Raised by ``apply`` to refuse a record with a closed ``code``."""

    def __init__(self, code: str) -> None:
        if not _CODE.fullmatch(code):
            raise ValueError("refusal code must be [a-z0-9_]{1,64}")
        super().__init__(code)
        self.code = code


class ImportBlocked(Exception):
    """Raised by ``apply`` when the record references one not yet imported."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    verdict: ImportVerdict
    source_system: str
    record_type: str
    external_ref: str
    version: int
    digest: str
    refusal_code: str | None = None
    #: What ``apply`` returned (first execution or the recorded replay).
    result: Mapping[str, object] | None = None

    @property
    def wire(self) -> str:
        if self.verdict is ImportVerdict.REFUSED:
            return f"refused:{self.refusal_code}"
        return self.verdict.value

    @property
    def writes(self) -> bool:
        return self.verdict in (ImportVerdict.CREATED, ImportVerdict.UPDATED)


def content_digest(content: Any) -> str:
    """The digest recorded for a body: canonical, key-order independent sha256."""
    return fingerprint_of(content)


def import_scope(source_system: str, record_type: str) -> str:
    for label, value in (
        ("source system", source_system),
        ("record type", record_type),
    ):
        if not _NAME.fullmatch(value):
            raise BadRequestError(f"{label} {value!r} is not a valid import name")
    scope = f"{IMPORT_SCOPE_PREFIX}:{source_system}:{record_type}"
    if len(scope) > MAX_SCOPE_LENGTH:
        raise BadRequestError("import scope is too long")
    return scope


def _ledger_key(external_ref: str, version: int) -> str:
    key = f"{external_ref}@{version}"
    if not external_ref or external_ref.strip() != external_ref:
        raise BadRequestError("external reference is required and must be trimmed")
    if len(key) > MAX_KEY_LENGTH:
        raise BadRequestError("external reference is too long")
    return key


MAX_RESULT_KEYS = 16
MAX_RESULT_VALUE_LENGTH = 200
_RESULT_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")


def flat_id_map(result: Mapping[str, object] | None) -> dict[str, object]:
    """Validate the stored result: a small FLAT map of ids (B7).

    The ledger keeps the result forever, so it must never carry payload or
    personal data. Keys are short snake_case names; values are strings (at most
    200 characters) or integers; no nesting. Anything else is a programming
    error in the adopter's ``apply`` and raises ``ValueError``.
    """
    if result is None:
        return {}
    if len(result) > MAX_RESULT_KEYS:
        raise ValueError(f"import result has more than {MAX_RESULT_KEYS} keys")
    checked: dict[str, object] = {}
    for key, value in result.items():
        if not isinstance(key, str) or not _RESULT_KEY.fullmatch(key):
            raise ValueError("import result keys must be short snake_case names")
        if isinstance(value, bool) or not isinstance(value, str | int):
            raise ValueError("import result values must be strings or integers")
        if isinstance(value, str) and len(value) > MAX_RESULT_VALUE_LENGTH:
            raise ValueError("import result string value is too long")
        checked[key] = value
    return checked


def _lock_reference(
    db: Session, *, tenant_id: Any, scope: str, external_ref: str
) -> None:
    """Serialise importers of one (tenant, scope, reference) until transaction end (B6).

    A transaction-scoped PostgreSQL advisory lock taken BEFORE the versions-seen
    read, so a racing v3 and v5 cannot both observe the same prior state, both
    apply, and mislabel created/updated. Requires READ COMMITTED (the kernel
    default) so the second importer sees the first's ledger row after the lock.
    No-op off PostgreSQL.
    """
    if db.get_bind().dialect.name != "postgresql":
        return
    require_read_committed(db)
    db.execute(
        sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"{tenant_id}|{scope}|{external_ref}"},
    )


def _exact_key_recorded(db: Session, *, tenant_id: Any, scope: str, key: str) -> bool:
    return (
        db.execute(
            select(IdempotencyRecord.id).where(
                IdempotencyRecord.tenant_id == tenant_id,
                IdempotencyRecord.scope == scope,
                IdempotencyRecord.key == key,
            )
        ).first()
        is not None
    )


def _versions_seen(
    db: Session, *, tenant_id: Any, scope: str, external_ref: str
) -> set[int]:
    prefix = f"{external_ref}@"
    keys = db.execute(
        select(IdempotencyRecord.key).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key.startswith(prefix, autoescape=True),
        )
    ).scalars()
    tails = (key[len(prefix) :] for key in keys)
    # "a@1@2" belongs to reference "a@1", not "a": only a pure-digit tail counts.
    return {int(tail) for tail in tails if tail.isdigit()}


def import_record(
    db: Session,
    declaration: ResourceDeclaration,
    *,
    principal: MachinePrincipal,
    external_ref: str,
    version: int,
    content: Any,
    apply: Callable[[Session, frozenset[int]], Mapping[str, object] | None],
    current_version: Callable[[Session], int | None] | None = None,
) -> ImportResult:
    """Import one record version idempotently.

    ``principal`` supplies the source system and tenant. The resource must declare
    an ``import_write`` scope and the key must hold exactly that scope; a key that
    can read the resource, or import a different one, is refused. ``apply``
    receives the session and the set of versions already imported for this
    reference, runs at most once per key, and returns the result to record,
    which must be a small flat id map (``flat_id_map``).

    ``current_version`` (B8) lets the receiver supply the highest version it has
    already imported from its OWN record store, by exact key. Without it the kit
    scans the ledger for ``<ref>@`` keys: that query is served by the unique
    ``(tenant_id, scope, key)`` index only to the extent the database collation
    allows a prefix range (a plain btree does not serve ``LIKE 'prefix%'`` under
    a non-C collation), so a receiver with many versions per reference should
    pass the callback and keep the scan as a fallback.
    """
    required = declaration.scopes.import_write
    if required is None or not principal.has_scope(required):
        raise ForbiddenError(
            f"machine credential {principal.label!r} does not carry the import "
            f"scope for {declaration.name!r}"
        )
    # B9: no key both reads and writes a resource.
    for read_scope in (declaration.scopes.read, declaration.scopes.contact_read):
        if read_scope is not None and principal.has_scope(read_scope):
            raise ForbiddenError(
                f"machine credential {principal.label!r} holds a read scope for "
                f"{declaration.name!r}; a key may read or write a resource, not both"
            )
    check_session_tenant(db, principal)
    if isinstance(version, bool) or not isinstance(version, int):
        raise BadRequestError("version must be an integer")
    if not 1 <= version <= MAX_VERSION:
        raise BadRequestError("version is out of range")

    source = principal.application
    scope = import_scope(source, declaration.name)
    key = _ledger_key(external_ref, version)
    digest = content_digest(content)

    def outcome(
        verdict: ImportVerdict,
        *,
        refusal_code: str | None = None,
        result: Mapping[str, object] | None = None,
    ) -> ImportResult:
        return ImportResult(
            verdict=verdict,
            source_system=source,
            record_type=declaration.name,
            external_ref=external_ref,
            version=version,
            digest=digest,
            refusal_code=refusal_code,
            result=result,
        )

    _lock_reference(
        db, tenant_id=principal.tenant_id, scope=scope, external_ref=external_ref
    )
    if current_version is not None:
        latest = current_version(db)
        seen: set[int] = {latest} if latest else set()
        # The callback only knows the highest version. If this exact version is
        # already in the ledger, let the ledger answer (unchanged or conflict)
        # rather than reporting a replayed older version as stale.
        if version not in seen and _exact_key_recorded(
            db, tenant_id=principal.tenant_id, scope=scope, key=key
        ):
            seen.add(version)
    else:
        seen = _versions_seen(
            db, tenant_id=principal.tenant_id, scope=scope, external_ref=external_ref
        )
    # The exact key is decided by the ledger below (replay or conflict) even when
    # a later version exists; only a genuinely new, lower version is stale.
    if version not in seen and seen and max(seen) > version:
        return outcome(ImportVerdict.STALE)

    try:
        executed = execute_once(
            db,
            tenant_id=principal.tenant_id,
            scope=scope,
            key=key,
            operation=lambda session: flat_id_map(apply(session, frozenset(seen))),
            operation_name=f"import:{declaration.name}",
            fingerprint=digest,
            expires_at=None,
        )
    except IdempotencyConflict:
        return outcome(ImportVerdict.CONFLICT)
    except ImportRefused as refusal:
        return outcome(ImportVerdict.REFUSED, refusal_code=refusal.code)
    except ImportBlocked:
        return outcome(ImportVerdict.BLOCKED_PREDECESSOR)

    if executed.replayed:
        return outcome(ImportVerdict.UNCHANGED, result=executed.result)
    return outcome(
        ImportVerdict.UPDATED if seen else ImportVerdict.CREATED,
        result=executed.result,
    )


__all__ = [
    "IMPORT_SCOPE_PREFIX",
    "ImportBlocked",
    "ImportRefused",
    "ImportResult",
    "ImportVerdict",
    "MAX_RESULT_KEYS",
    "content_digest",
    "flat_id_map",
    "import_record",
    "import_scope",
]
