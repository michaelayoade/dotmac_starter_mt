"""At-most-once execution with result replay — the one owner (ADR-0014).

`execute_once` runs an effect AT MOST ONCE per `(tenant_id, scope, key)`: the
first attempt runs the handler and records an `IdempotencyRecord` with its
result; a later attempt finds that record and replays the result WITHOUT
re-running. `execute_once_platform` is the same contract without a tenant.

`dotmac_kernel.messaging.process_once` / `process_once_platform` are thin
callers of this module — they are the transport-delivery spelling of the same
operation (`scope="inbox"`, key = the transport's `command_id`), not a second
mechanism. There is exactly one table answering "has this been done".

Three properties are the whole design, and each one is a defect found in a
product implementation (`docs/inventories/idempotency-sources.md`):

**Nothing is written before the effect.** The handler runs and the ledger row is
inserted in the SAME transaction, so they commit together or not at all. A crash
mid-effect therefore leaves NO row and the retry re-drives cleanly. ERP's
implementation reserves a `202 "Request in progress"` row first, and when a
request dies that placeholder is replayed to every retry for 24 hours with no
lease, no stale detector and no way out. There is no such state here — the
failure mode does not exist rather than being recovered from.

**The fingerprint is its own column.** A replay carrying a DIFFERENT request is
a conflict, not a duplicate. `fingerprint=None` means the caller asserts the key
alone identifies the request (true for a transport-generated command id). Sub's
shared table overloads one untyped column to mean a fingerprint in two services
and a result id in five others; that is unrepresentable here.

**Retention is the product's policy.** `expires_at` is nullable and this module
sets no default TTL — a payment replay window and a provisioning replay window
are not the same duration. Call `purge_expired` on whatever schedule the product
chooses.

Non-transactional effects (an external API call that cannot join the
transaction) are OUT OF SCOPE — see ADR-0014 § 7. Enqueue through the outbox in
the same transaction and let the relay own delivery and retry; do not rebuild a
reservation on top of this contract.

Follows the kernel transaction-authority rule: RECEIVES a `Session`, never
builds one; does `add`/`flush`, never `commit`/`rollback` (the request boundary
owns that).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

from dotmac_kernel.exceptions import BadRequestError, ConflictError
from dotmac_kernel.idempotency_models import (
    IdempotencyRecord,
    IdempotencyStatus,
    PlatformIdempotencyRecord,
)

# An operation applies its effect and returns a JSON-serializable result (or
# None) recorded for idempotent replay. It runs inside the caller's transaction
# and must only add/flush — never commit.
Operation = Callable[[Session], Mapping[str, object] | None]

# Column limits from `idempotency_models`. Enforced here so an over-long key is
# a typed 400 at the seam rather than a database error deep in a flush — Sub
# validates its key length for the same reason, and truncating instead would
# silently merge two distinct requests into one ledger entry.
MAX_KEY_LENGTH = 200
MAX_SCOPE_LENGTH = 120


class IdempotencyConflict(ConflictError):
    """The `(scope, key)` was already used by a request with a DIFFERENT
    fingerprint. Subclasses `ConflictError`, so an assembly that already maps
    domain errors to HTTP returns 409 with no extra wiring."""


@dataclass(frozen=True, slots=True)
class IdempotentOutcome:
    """The result of `execute_once`. `replayed` is True when a prior result was
    returned instead of running the operation — the caller can tell a first
    execution from a replay, which neither product implementation exposed."""

    scope: str
    key: str
    result: Mapping[str, object]
    replayed: bool


def fingerprint_of(payload: Any) -> str:
    """A stable SHA256 over `payload`, for detecting a key reused with a
    different request.

    Stability is the whole point, so the encoding is pinned: sorted keys and
    compact separators, meaning dict ordering and incidental whitespace cannot
    change the digest. Objects exposing `model_dump` (pydantic models, without
    importing pydantic here) are dumped in JSON mode first; anything else
    non-serializable falls back to `str`, so a UUID or Decimal fingerprints
    consistently instead of raising.
    """
    if hasattr(payload, "model_dump"):
        payload = payload.model_dump(mode="json")
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate(scope: str, key: str) -> None:
    if not scope or not scope.strip():
        raise BadRequestError("Idempotency scope is required")
    if not key or not key.strip():
        raise BadRequestError("Idempotency key is required")
    if len(scope) > MAX_SCOPE_LENGTH:
        raise BadRequestError(
            f"Idempotency scope must be at most {MAX_SCOPE_LENGTH} characters"
        )
    if len(key) > MAX_KEY_LENGTH:
        raise BadRequestError(
            f"Idempotency key must be at most {MAX_KEY_LENGTH} characters"
        )


def _replay_or_conflict(
    record: IdempotencyRecord | PlatformIdempotencyRecord,
    *,
    scope: str,
    key: str,
    fingerprint: str | None,
) -> IdempotentOutcome:
    """Turn an existing ledger row into a replay, or refuse it as a conflict.

    A conflict needs BOTH sides to have a fingerprint. If either is None the
    caller has asserted the key alone identifies the request, and second-guessing
    that would break the transport case where the sender generated the id.
    """
    if (
        fingerprint is not None
        and record.fingerprint is not None
        and fingerprint != record.fingerprint
    ):
        raise IdempotencyConflict(
            f"Idempotency key was already used in scope {scope!r} "
            "with a different request"
        )
    return IdempotentOutcome(
        scope=scope, key=key, result=dict(record.result or {}), replayed=True
    )


def _lookup(
    db: Session, *, tenant_id: UUID, scope: str, key: str
) -> IdempotencyRecord | None:
    return db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.scope == scope,
            IdempotencyRecord.key == key,
        )
    ).scalar_one_or_none()


def _lookup_platform(
    db: Session, *, scope: str, key: str
) -> PlatformIdempotencyRecord | None:
    return db.execute(
        select(PlatformIdempotencyRecord).where(
            PlatformIdempotencyRecord.scope == scope,
            PlatformIdempotencyRecord.key == key,
        )
    ).scalar_one_or_none()


def execute_once(
    db: Session,
    *,
    tenant_id: UUID,
    scope: str,
    key: str,
    operation: Operation,
    operation_name: str | None = None,
    fingerprint: str | None = None,
    correlation_id: str | None = None,
    expires_at: datetime | None = None,
) -> IdempotentOutcome:
    """Run `operation` at most once per `(tenant_id, scope, key)`.

    Returns an `IdempotentOutcome` whose `replayed` is True when a prior result
    was returned instead of running `operation`. Raises `IdempotencyConflict`
    when the key was already used with a different `fingerprint`.

    `operation_name` records what the key was spent on, for operators reading
    the ledger; it defaults to `scope` and never participates in the key.
    """
    # The DB module constructs the configured engine on import. Defer it until
    # this write path runs so packages can import idempotency contracts and
    # manifests before an application installs its database URL.
    from dotmac_kernel.db import conflict_savepoint

    _validate(scope, key)

    existing = _lookup(db, tenant_id=tenant_id, scope=scope, key=key)
    if existing is not None:
        return _replay_or_conflict(
            existing, scope=scope, key=key, fingerprint=fingerprint
        )

    try:
        with conflict_savepoint(db):
            result = dict(operation(db) or {})
            db.add(
                IdempotencyRecord(
                    tenant_id=tenant_id,
                    scope=scope,
                    key=key,
                    fingerprint=fingerprint,
                    operation=operation_name or scope,
                    status=IdempotencyStatus.EXECUTED.value,
                    result=result,
                    correlation_id=correlation_id,
                    expires_at=expires_at,
                )
            )
            db.flush()
    except IntegrityError:
        # A concurrent attempt won the (tenant_id, scope, key) race; this call's
        # effects rolled back with the SAVEPOINT. Replay the winner — or refuse,
        # if the winner was a different request.
        winner = _lookup(db, tenant_id=tenant_id, scope=scope, key=key)
        if winner is None:
            # The IntegrityError came from the operation itself, not from our
            # unique constraint. Surfacing it as a replay would swallow a real
            # domain conflict.
            raise
        return _replay_or_conflict(
            winner, scope=scope, key=key, fingerprint=fingerprint
        )

    return IdempotentOutcome(scope=scope, key=key, result=result, replayed=False)


def execute_once_platform(
    db: Session,
    *,
    scope: str,
    key: str,
    operation: Operation,
    operation_name: str | None = None,
    fingerprint: str | None = None,
    correlation_id: str | None = None,
    expires_at: datetime | None = None,
) -> IdempotentOutcome:
    """Run `operation` at most once per `(scope, key)`, with no tenant.

    The platform peer of `execute_once` — same contract, same guarantees,
    against the platform catalog table (no RLS; see ADR-0004).
    """
    from dotmac_kernel.db import conflict_savepoint

    _validate(scope, key)

    existing = _lookup_platform(db, scope=scope, key=key)
    if existing is not None:
        return _replay_or_conflict(
            existing, scope=scope, key=key, fingerprint=fingerprint
        )

    try:
        with conflict_savepoint(db):
            result = dict(operation(db) or {})
            db.add(
                PlatformIdempotencyRecord(
                    scope=scope,
                    key=key,
                    fingerprint=fingerprint,
                    operation=operation_name or scope,
                    status=IdempotencyStatus.EXECUTED.value,
                    result=result,
                    correlation_id=correlation_id,
                    expires_at=expires_at,
                )
            )
            db.flush()
    except IntegrityError:
        winner = _lookup_platform(db, scope=scope, key=key)
        if winner is None:
            raise
        return _replay_or_conflict(
            winner, scope=scope, key=key, fingerprint=fingerprint
        )

    return IdempotentOutcome(scope=scope, key=key, result=result, replayed=False)


def purge_expired(
    db: Session,
    *,
    now: datetime | None = None,
    scope: str | None = None,
    platform: bool = False,
) -> int:
    """Delete ledger rows whose `expires_at` has passed. Returns the row count.

    Rows with `expires_at IS NULL` are NEVER purged: a NULL means the product
    made no retention decision, and deleting a key it still considers live would
    let a replay re-execute. Opting into expiry is explicit.

    Does not commit — the caller's transaction boundary owns that (hard rule 8).
    Under RLS the tenant-scoped sweep only ever sees the current tenant's rows;
    a fleet-wide purge runs as `app_admin`.
    """
    model: Any = PlatformIdempotencyRecord if platform else IdempotencyRecord
    # Default to the DATABASE's clock, not the worker's: a skewed worker would
    # otherwise purge keys that are still live.
    cutoff = func.now() if now is None else now

    stmt = delete(model).where(model.expires_at.is_not(None), model.expires_at < cutoff)
    if scope is not None:
        stmt = stmt.where(model.scope == scope)

    # `Session.execute` is typed as returning `Result`, but a DELETE always
    # yields a `CursorResult` — the cast is the narrowing, not a claim.
    return int(cast("CursorResult[Any]", db.execute(stmt)).rowcount or 0)


__all__ = [
    "IdempotencyConflict",
    "IdempotentOutcome",
    "MAX_KEY_LENGTH",
    "MAX_SCOPE_LENGTH",
    "Operation",
    "execute_once",
    "execute_once_platform",
    "fingerprint_of",
    "purge_expired",
]
