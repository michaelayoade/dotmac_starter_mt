"""Platform outbox relay behavior (WS3 platform relay) — typed claim/success/failure.

The platform peer of `messaging.relay`. It drains `platform_outbox_events` using
the SAME leasing/backoff/dead-letter ENGINE — `RelayPolicy`, `FailureOutcome`, and
the `_backoff_seconds` policy are REUSED from `relay`, not duplicated — but against
a SEPARATE table via its own hardened `SECURITY DEFINER` functions
(`claim_platform_outbox_batch` / `settle_platform_outbox_event`) and a SEPARATE
dispatcher role (`platform_outbox_dispatcher`). The two tables are never combined.

The one shape difference: a platform event has NO tenant, so `ClaimedPlatformEvent`
carries no `tenant_id` — consumers process it on a platform session (no RLS context
to restore).

Transaction-authority contract (kernel `db.py`): every function RECEIVES a
`Session` (bound to the *platform-dispatcher-role* connection) and only executes —
never constructs a session or commits; the worker owns the transaction boundary.
Guarantees **at-least-once** delivery with **one active claim per lease** — not
exactly-once (a crash after delivery but before settle re-delivers; the consumer
dedupes via `process_once_platform`).

Submodule-only: `from dotmac_kernel.messaging.platform_relay import ...`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from dotmac_kernel.messaging.relay import (
    FailureOutcome,
    RelayPolicy,
    _backoff_seconds,
)

_ERR_MAX = 500  # last_error column width


@dataclass(frozen=True, slots=True)
class ClaimedPlatformEvent:
    """A leased platform outbox row, typed for delivery. No `tenant_id`: a
    platform event is a control-plane fact with no tenant to scope by."""

    id: UUID
    event_type: str
    payload: dict[str, object]
    attempts: int
    correlation_id: str | None


def claim_platform_batch(
    db: Session, *, worker_id: str, policy: RelayPolicy = RelayPolicy()
) -> list[ClaimedPlatformEvent]:
    """Lease up to `policy.batch_size` ready rows (pending-and-due OR stale-claimed)
    via `claim_platform_outbox_batch`. The caller must commit to make the lease
    durable before delivering."""
    rows = (
        db.execute(
            text(
                "SELECT id, event_type, payload, attempts, correlation_id "
                "FROM claim_platform_outbox_batch(:w, :b, :s)"
            ),
            {
                "w": worker_id,
                "b": policy.batch_size,
                "s": policy.stale_lease_seconds,
            },
        )
        .mappings()
        .all()
    )
    return [
        ClaimedPlatformEvent(
            id=r["id"],
            event_type=r["event_type"],
            payload=dict(r["payload"] or {}),
            attempts=r["attempts"],
            correlation_id=r["correlation_id"],
        )
        for r in rows
    ]


def record_success(db: Session, *, event_id: UUID, worker_id: str) -> bool:
    """Settle a delivered platform event to `sent`. Returns False if this worker
    no longer holds the lease (e.g. it was reclaimed as stale)."""
    return bool(
        db.execute(
            text("SELECT settle_platform_outbox_event(:id, :w, 'sent', NULL, 0, NULL)"),
            {"id": str(event_id), "w": worker_id},
        ).scalar()
    )


def record_failure(
    db: Session,
    *,
    event_id: UUID,
    worker_id: str,
    attempts: int,
    error: str,
    policy: RelayPolicy = RelayPolicy(),
) -> FailureOutcome:
    """Record a delivery failure: increment attempts, then either back off
    (`pending` with a future `available_at`) or dead-letter (`dead`, retained)
    once `max_attempts` is reached. Backoff is computed by the shared engine
    (`relay._backoff_seconds`); the SQL function stays mechanical."""
    next_attempts = attempts + 1
    trimmed = error[:_ERR_MAX]
    if next_attempts >= policy.max_attempts:
        db.execute(
            text("SELECT settle_platform_outbox_event(:id, :w, 'dead', NULL, :a, :e)"),
            {"id": str(event_id), "w": worker_id, "a": next_attempts, "e": trimmed},
        )
        return FailureOutcome(event_id, next_attempts, True, None)
    retry_at = datetime.now(UTC) + timedelta(seconds=_backoff_seconds(attempts, policy))
    db.execute(
        text("SELECT settle_platform_outbox_event(:id, :w, 'pending', :ts, :a, :e)"),
        {
            "id": str(event_id),
            "w": worker_id,
            "ts": retry_at,
            "a": next_attempts,
            "e": trimmed,
        },
    )
    return FailureOutcome(event_id, next_attempts, False, retry_at)


__all__ = [
    "ClaimedPlatformEvent",
    "claim_platform_batch",
    "record_failure",
    "record_success",
]
