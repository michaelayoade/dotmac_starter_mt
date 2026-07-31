"""Outbox relay behavior (WS3 slice 2, PR 2) — typed claim / success / failure.

The typed operations a dispatcher worker (PR 3) uses to drain the outbox. They are
thin wrappers over the PR-1 `SECURITY DEFINER` functions (`claim_outbox_batch` /
`settle_outbox_event`) plus the **retry/backoff/dead-letter policy** (computed
here, in Python — the SQL functions stay mechanical):

- `claim_batch` — lease a batch of ready rows (incl. stale-lease reclaim), typed
  as `ClaimedEvent`s (each carries `tenant_id` so the worker can restore tenant
  context for delivery — the dispatcher connection never reads tenant data).
- `record_success` — settle a delivered event to `sent`.
- `record_failure` — increment attempts and either back off (`pending`, future
  `available_at`) or dead-letter (`dead`, retained) at `max_attempts`.

Transaction-authority contract (kernel `db.py`): every function RECEIVES a
`Session` (bound to the *dispatcher-role* connection) and only executes — it never
constructs a session or commits; the worker owns the transaction boundary. These
guarantee **at-least-once** delivery with **one active claim per lease** — not
exactly-once (a crash after delivery but before settle re-delivers; the consumer
dedupes).

Submodule-only (pulls in the DB layer): `from dotmac_kernel.messaging.relay import ...`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

_ERR_MAX = 500  # last_error column width


@dataclass(frozen=True, slots=True)
class RelayPolicy:
    """Dispatcher tuning (prod-safe defaults). `stale_lease_seconds` bounds crash
    recovery; `max_attempts` bounds retries before dead-lettering; backoff is
    capped exponential."""

    batch_size: int = 50
    max_attempts: int = 8
    base_backoff_seconds: float = 2.0
    max_backoff_seconds: float = 3600.0
    stale_lease_seconds: int = 300


@dataclass(frozen=True, slots=True)
class ClaimedEvent:
    """A leased outbox row, typed for delivery. `tenant_id` lets the worker
    restore the event's tenant context for any delivery-time read — the claim
    itself is cross-tenant, delivery is not."""

    id: UUID
    tenant_id: UUID
    event_type: str
    payload: dict[str, object]
    attempts: int
    correlation_id: str | None


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    """Result of `record_failure`. `dead_lettered` True means the event reached
    `max_attempts` and is retained as `dead`; otherwise `retry_at` is when it
    becomes claimable again."""

    event_id: UUID
    attempts: int
    dead_lettered: bool
    retry_at: datetime | None


def claim_batch(
    db: Session, *, worker_id: str, policy: RelayPolicy = RelayPolicy()
) -> list[ClaimedEvent]:
    """Lease up to `policy.batch_size` ready rows (pending-and-due OR stale-claimed)
    via `claim_outbox_batch`. The caller must commit to make the lease durable
    before delivering."""
    rows = (
        db.execute(
            text(
                "SELECT id, tenant_id, event_type, payload, attempts, correlation_id "
                "FROM claim_outbox_batch(:w, :b, :s)"
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
        ClaimedEvent(
            id=r["id"],
            tenant_id=r["tenant_id"],
            event_type=r["event_type"],
            payload=dict(r["payload"] or {}),
            attempts=r["attempts"],
            correlation_id=r["correlation_id"],
        )
        for r in rows
    ]


def record_success(db: Session, *, event_id: UUID, worker_id: str) -> bool:
    """Settle a delivered event to `sent`. Returns False if this worker no longer
    holds the lease (e.g. it was reclaimed as stale) — the delivery still
    happened, so the row is left to the current lease holder."""
    return bool(
        db.execute(
            text("SELECT settle_outbox_event(:id, :w, 'sent', NULL, 0, NULL)"),
            {"id": str(event_id), "w": worker_id},
        ).scalar()
    )


def _backoff_seconds(attempts: int, policy: RelayPolicy) -> float:
    return min(policy.base_backoff_seconds * (2**attempts), policy.max_backoff_seconds)


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
    (`pending` with a future `available_at`) or dead-letter (`dead`, retained) once
    `max_attempts` is reached. `attempts` is the event's current count (from the
    `ClaimedEvent`)."""
    next_attempts = attempts + 1
    trimmed = error[:_ERR_MAX]
    if next_attempts >= policy.max_attempts:
        db.execute(
            text("SELECT settle_outbox_event(:id, :w, 'dead', NULL, :a, :e)"),
            {"id": str(event_id), "w": worker_id, "a": next_attempts, "e": trimmed},
        )
        return FailureOutcome(event_id, next_attempts, True, None)
    retry_at = datetime.now(UTC) + timedelta(seconds=_backoff_seconds(attempts, policy))
    db.execute(
        text("SELECT settle_outbox_event(:id, :w, 'pending', :ts, :a, :e)"),
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
    "RelayPolicy",
    "ClaimedEvent",
    "FailureOutcome",
    "claim_batch",
    "record_success",
    "record_failure",
]
