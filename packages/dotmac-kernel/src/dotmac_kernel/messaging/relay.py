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
- `record_dead_letter` — an immediate, non-retryable dead-letter (a transport's
  `DeliveryTerminal`, K-8), retained for the audited requeue.
- `failure_dead_letters` / `deferral_dead_letters` — whether the NEXT failure or
  deferral will dead-letter, so a transport can settle its own dead state
  before the outbox row goes dead.
- `record_deferral` — a `DeliveryDeferred` hold (K-4): reschedule (`pending`,
  future `available_at`) WITHOUT charging an attempt, bounded by
  `RelayPolicy.max_deferrals`, after which the event is dead-lettered.

Transaction-authority contract (kernel `db.py`): every function RECEIVES a
`Session` (bound to the *dispatcher-role* connection) and only executes — it never
constructs a session or commits; the worker owns the transaction boundary. These
guarantee **at-least-once** delivery with **one active claim per lease** — not
exactly-once (a crash after delivery but before settle re-delivers; the consumer
dedupes).

Submodule-only (pulls in the DB layer): `from dotmac_kernel.messaging.relay import ...`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

_ERR_MAX = 500  # last_error column width
_DEFER_PREFIX = "deferred:"


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
    #: Deferral (K-4). A `DeliveryDeferred` does not charge `max_attempts`, so it
    #: needs its own bound or a gate held forever would reschedule forever,
    #: silently. 288 deferrals at the 300 s default delay is 24 h: a mail-gate
    #: hold is an operational event measured in hours, and a day of it should
    #: surface as a dead-lettered (alertable, audited-requeue) event rather than
    #: a row that quietly never moves. A longer `retry_after` from the holder
    #: stretches that wall-clock window, never the count.
    max_deferrals: int = 288
    default_defer_seconds: float = 300.0
    #: A holder's `retry_after` is clamped into [min, max] so a hostile or buggy
    #: value cannot hot-loop the relay nor park an event for weeks.
    min_defer_seconds: float = 30.0
    max_defer_seconds: float = 3600.0


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
    #: The row's `last_error` at claim time. It carries the deferral count
    #: (`deferrals`); no schema column exists for it, by design (K-4 is additive
    #: and the outbox prerequisite contract is unchanged).
    last_error: str | None = None

    @property
    def deferrals(self) -> int:
        """Consecutive deferrals recorded since the last failure or success."""
        return _parse_deferrals(self.last_error)


@dataclass(frozen=True, slots=True)
class DeferralOutcome:
    """Result of `record_deferral`. `dead_lettered` True means the deferral bound
    was exceeded and the event is retained as `dead`; otherwise `retry_at` is when
    it becomes claimable again. `attempts` is unchanged by a deferral."""

    event_id: UUID
    deferrals: int
    dead_lettered: bool
    retry_at: datetime | None


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    """Result of `record_failure`. `dead_lettered` True means the event reached
    `max_attempts` and is retained as `dead`; otherwise `retry_at` is when it
    becomes claimable again."""

    event_id: UUID
    attempts: int
    dead_lettered: bool
    retry_at: datetime | None


def _claimed(rows: Iterable[Mapping[str, Any]]) -> list[ClaimedEvent]:
    return [
        ClaimedEvent(
            id=r["id"],
            tenant_id=r["tenant_id"],
            event_type=r["event_type"],
            payload=dict(r["payload"] or {}),
            attempts=r["attempts"],
            correlation_id=r["correlation_id"],
            last_error=r["last_error"],
        )
        for r in rows
    ]


def claim_batch(
    db: Session, *, worker_id: str, policy: RelayPolicy = RelayPolicy()
) -> list[ClaimedEvent]:
    """Lease up to `policy.batch_size` ready rows (pending-and-due OR stale-claimed)
    via `claim_outbox_batch`. The caller must commit to make the lease durable
    before delivering. Claims EVERY event type; a consumer of one type uses
    `claim_batch_by_type`."""
    rows = (
        db.execute(
            text(
                "SELECT id, tenant_id, event_type, payload, attempts, "
                "correlation_id, last_error "
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
    return _claimed(rows)


def claim_batch_by_type(
    db: Session,
    *,
    worker_id: str,
    event_types: Sequence[str],
    policy: RelayPolicy = RelayPolicy(),
) -> list[ClaimedEvent]:
    """Lease ready rows of ONLY the named event types, via
    `claim_outbox_batch_by_type` (prerequisite `outbox_typed_claim.v1`).

    A separate function, not a parameter of `claim_batch`, so a type list that
    arrives as `None` can never silently mean "every type". A foreign event is
    never leased, not even a stale-claimed one. An empty or non-string list is
    refused. The caller must commit to make the lease durable.
    """
    if isinstance(event_types, str) or not isinstance(event_types, Sequence):
        raise ValueError("event_types must be a non-empty sequence of strings")
    if not event_types or any(
        not isinstance(item, str) or not item for item in event_types
    ):
        raise ValueError("event_types must be a non-empty sequence of strings")
    rows = (
        db.execute(
            text(
                "SELECT id, tenant_id, event_type, payload, attempts, "
                "correlation_id, last_error "
                "FROM claim_outbox_batch_by_type(:w, :b, :s, CAST(:t AS text[]))"
            ),
            {
                "w": worker_id,
                "b": policy.batch_size,
                "s": policy.stale_lease_seconds,
                "t": sorted(set(event_types)),
            },
        )
        .mappings()
        .all()
    )
    return _claimed(rows)


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


def failure_dead_letters(attempts: int, policy: RelayPolicy = RelayPolicy()) -> bool:
    """True when `record_failure` with this claimed `attempts` dead-letters."""
    return attempts + 1 >= policy.max_attempts


def deferral_dead_letters(deferrals: int, policy: RelayPolicy = RelayPolicy()) -> bool:
    """True when `record_deferral` with this claimed `deferrals` dead-letters."""
    return deferrals + 1 > policy.max_deferrals


def record_dead_letter(
    db: Session,
    *,
    event_id: UUID,
    worker_id: str,
    attempts: int,
    error: str,
) -> None:
    """Dead-letter an event NOW, without further retries (K-8). Used when a
    transport says the outcome must not be retried (an unknown provider outcome
    could double-send). `attempts` is passed back unchanged. The row is retained
    as `dead`; the audited requeue is the only way back in."""
    db.execute(
        text("SELECT settle_outbox_event(:id, :w, 'dead', NULL, :a, :e)"),
        {
            "id": str(event_id),
            "w": worker_id,
            "a": attempts,
            "e": error[:_ERR_MAX],
        },
    )


def _parse_deferrals(last_error: str | None) -> int:
    """The deferral count from a `deferred:<n>: ...` marker; 0 for anything else
    (a failure overwrites `last_error`, which correctly resets the run)."""
    if not last_error or not last_error.startswith(_DEFER_PREFIX):
        return 0
    head = last_error[len(_DEFER_PREFIX) :].split(":", 1)[0]
    return int(head) if head.isascii() and head.isdigit() else 0


def record_deferral(
    db: Session,
    *,
    event_id: UUID,
    worker_id: str,
    attempts: int,
    deferrals: int,
    reason: str,
    retry_after: float | None = None,
    policy: RelayPolicy = RelayPolicy(),
) -> DeferralOutcome:
    """Record a `DeliveryDeferred` hold: reschedule WITHOUT charging an attempt.

    `attempts` and `deferrals` are the event's claimed values
    (`ClaimedEvent.attempts` / `.deferrals`); `attempts` is passed back to the
    settle function unchanged, which is what "does not charge" means (the
    dispatcher role cannot read the table, so it cannot look the count up). The
    next run length is stored in `last_error` as `deferred:<n>: <reason>`. Once it
    would exceed `policy.max_deferrals` the event is dead-lettered (`dead`,
    retained, attempts unchanged) so a permanent hold surfaces instead of
    looping silently; the audited requeue is the way back in.
    """
    next_deferrals = deferrals + 1
    trimmed_reason = reason[: _ERR_MAX - 40]
    if next_deferrals > policy.max_deferrals:
        error = f"deferral bound exceeded ({policy.max_deferrals}): {trimmed_reason}"
        db.execute(
            text("SELECT settle_outbox_event(:id, :w, 'dead', NULL, :a, :e)"),
            {
                "id": str(event_id),
                "w": worker_id,
                "a": attempts,
                "e": error[:_ERR_MAX],
            },
        )
        return DeferralOutcome(event_id, deferrals, True, None)
    delay = policy.default_defer_seconds if retry_after is None else retry_after
    delay = min(max(delay, policy.min_defer_seconds), policy.max_defer_seconds)
    retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    db.execute(
        text("SELECT settle_outbox_event(:id, :w, 'pending', :ts, :a, :e)"),
        {
            "id": str(event_id),
            "w": worker_id,
            "ts": retry_at,
            "a": attempts,
            "e": f"{_DEFER_PREFIX}{next_deferrals}: {trimmed_reason}"[:_ERR_MAX],
        },
    )
    return DeferralOutcome(event_id, next_deferrals, False, retry_at)


__all__ = [
    "ClaimedEvent",
    "DeferralOutcome",
    "FailureOutcome",
    "RelayPolicy",
    "claim_batch",
    "claim_batch_by_type",
    "deferral_dead_letters",
    "failure_dead_letters",
    "record_dead_letter",
    "record_deferral",
    "record_failure",
    "record_success",
]
