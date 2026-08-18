"""One timer-generation lifecycle over two explicitly selected planes.

Scheduling, cancellation and acceptance serialize on the same PostgreSQL
transaction advisory lock derived from scope plus identity. That lock exists
before the first row does, so concurrent initial schedules are covered as well
as reschedules.

The module writes the kernel outbox and moves its ``available_at`` to the
requested due instant. Delivery mechanics stay entirely in the kernel relay.
Every business instant is supplied by the caller, and every mutation remains
inside the caller's transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Final
from uuid import UUID, uuid4

from dotmac_kernel.cache import Scope, TenantScope, scope_segment
from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from dotmac_durable_timers.models import (
    PlatformTimer,
    PlatformTimerAcceptance,
    Timer,
    TimerAcceptance,
)

SCHEDULED: Final[str] = "scheduled"
SUPERSEDED: Final[str] = "superseded"
CANCELED: Final[str] = "canceled"
FIRED: Final[str] = "fired"

_IDENTITY_FIELD_LIMITS: Final[tuple[tuple[str, int], ...]] = (
    ("owner", 120),
    ("entity_kind", 120),
    ("entity_id", 255),
    ("purpose", 120),
)


class TimerError(ValueError):
    """Fail-closed timer contract violation with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = f"durable_timers.{code}"


def _required_text(name: str, value: str, limit: int) -> None:
    if not value or len(value) > limit:
        raise TimerError(
            f"invalid_{name}",
            f"{name} is required and must be at most {limit} characters",
        )


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise TimerError(f"naive_{name}", f"{name} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class TimerIdentity:
    """Open, provider-neutral identity of one logical timer stream."""

    owner: str
    entity_kind: str
    entity_id: str
    purpose: str

    def __post_init__(self) -> None:
        for name, limit in _IDENTITY_FIELD_LIMITS:
            _required_text(name, getattr(self, name), limit)


@dataclass(frozen=True, slots=True)
class TimerOutput:
    """The typed outbox output emitted when a generation becomes due."""

    event_type: str

    def __post_init__(self) -> None:
        _required_text("event_type", self.event_type, 120)


@dataclass(frozen=True, slots=True)
class TimerTrigger:
    """Versioned transport payload accepted immediately before a local effect."""

    timer_id: UUID
    identity: TimerIdentity
    generation: int
    due_at: datetime
    output: TimerOutput

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise TimerError("invalid_generation", "generation must be positive")
        _aware("due_at", self.due_at)

    def as_payload(self) -> dict[str, object]:
        return {
            "contract": "durable_timer.trigger.v1",
            "timer_id": str(self.timer_id),
            "owner": self.identity.owner,
            "entity_kind": self.identity.entity_kind,
            "entity_id": self.identity.entity_id,
            "purpose": self.identity.purpose,
            "generation": self.generation,
            "due_at": self.due_at.isoformat(),
            "output_event_type": self.output.event_type,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> TimerTrigger:
        if payload.get("contract") != "durable_timer.trigger.v1":
            raise TimerError(
                "unknown_contract", "timer trigger contract is unsupported"
            )
        try:
            timer_id = UUID(str(payload["timer_id"]))
            generation = int(str(payload["generation"]))
            due_at = datetime.fromisoformat(str(payload["due_at"]))
            identity = TimerIdentity(
                owner=str(payload["owner"]),
                entity_kind=str(payload["entity_kind"]),
                entity_id=str(payload["entity_id"]),
                purpose=str(payload["purpose"]),
            )
            output = TimerOutput(str(payload["output_event_type"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise TimerError(
                "invalid_trigger", "timer trigger payload is invalid"
            ) from exc
        return cls(
            timer_id=timer_id,
            identity=identity,
            generation=generation,
            due_at=due_at,
            output=output,
        )

    @classmethod
    def for_scheduled(cls, scheduled: ScheduleResult) -> TimerTrigger:
        return cls(
            timer_id=scheduled.timer_id,
            identity=scheduled.identity,
            generation=scheduled.generation,
            due_at=scheduled.due_at,
            output=scheduled.output,
        )


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    timer_id: UUID
    outbox_event_id: UUID
    identity: TimerIdentity
    generation: int
    due_at: datetime
    output: TimerOutput


class AcceptanceOutcome(str, Enum):
    CURRENT = "current"
    STALE = "stale"
    CANCELED = "canceled"
    ALREADY_FIRED = "already_fired"


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    outcome: AcceptanceOutcome
    observed_generation: int
    current_generation: int | None
    replayed: bool = False


class CancelOutcome(str, Enum):
    CANCELED = "canceled"
    ALREADY_FIRED = "already_fired"
    ALREADY_CANCELED = "already_canceled"
    NOTHING_SCHEDULED = "nothing_scheduled"


@dataclass(frozen=True, slots=True)
class CancelResult:
    outcome: CancelOutcome
    generation: int | None


def _identity_key(scope: Scope, identity: TimerIdentity) -> str:
    parts = (
        "durable_timers",
        scope_segment(scope),
        identity.owner,
        identity.entity_kind,
        identity.entity_id,
        identity.purpose,
    )
    # Length-prefix every open string. A delimiter alone would let a value
    # containing that delimiter alias a different tuple and silently weaken
    # serialization of the first schedule, where no row lock can help.
    return "".join(f"{len(part)}:{part}" for part in parts)


def _lock_identity(db: Session, scope: Scope, identity: TimerIdentity) -> None:
    db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(_identity_key(scope, identity), 0)
            )
        )
    )


def _tenant_identity_query(identity: TimerIdentity) -> Select[tuple[Timer]]:
    return select(Timer).where(
        Timer.owner == identity.owner,
        Timer.entity_kind == identity.entity_kind,
        Timer.entity_id == identity.entity_id,
        Timer.purpose == identity.purpose,
    )


def _platform_identity_query(
    identity: TimerIdentity,
) -> Select[tuple[PlatformTimer]]:
    return select(PlatformTimer).where(
        PlatformTimer.owner == identity.owner,
        PlatformTimer.entity_kind == identity.entity_kind,
        PlatformTimer.entity_id == identity.entity_id,
        PlatformTimer.purpose == identity.purpose,
    )


def _latest(
    db: Session, scope: Scope, identity: TimerIdentity
) -> Timer | PlatformTimer | None:
    if isinstance(scope, TenantScope):
        tenant_statement = _tenant_identity_query(identity).where(
            Timer.tenant_id == scope.tenant_id
        )
        return db.scalar(tenant_statement.order_by(Timer.generation.desc()).limit(1))
    platform_statement = _platform_identity_query(identity)
    return db.scalar(
        platform_statement.order_by(PlatformTimer.generation.desc()).limit(1)
    )


def _timer_by_trigger(
    db: Session, scope: Scope, trigger: TimerTrigger
) -> Timer | PlatformTimer | None:
    if isinstance(scope, TenantScope):
        tenant_statement = _tenant_identity_query(trigger.identity).where(
            Timer.id == trigger.timer_id,
            Timer.generation == trigger.generation,
            Timer.tenant_id == scope.tenant_id,
        )
        return db.scalar(tenant_statement.limit(1))
    platform_statement = _platform_identity_query(trigger.identity).where(
        PlatformTimer.id == trigger.timer_id,
        PlatformTimer.generation == trigger.generation,
    )
    return db.scalar(platform_statement.limit(1))


def _transition(
    db: Session,
    *,
    scope: Scope,
    timer_id: UUID,
    expected: str,
    target: str,
    changed_at: datetime,
) -> bool:
    if isinstance(scope, TenantScope):
        value = db.scalar(
            text(
                "SELECT mod_timers.transition_timer("
                ":tenant_id, :timer_id, :expected, :target, :changed_at)"
            ),
            {
                "tenant_id": scope.tenant_id,
                "timer_id": timer_id,
                "expected": expected,
                "target": target,
                "changed_at": changed_at,
            },
        )
    else:
        value = db.scalar(
            text(
                "SELECT mod_timers.transition_platform_timer("
                ":timer_id, :expected, :target, :changed_at)"
            ),
            {
                "timer_id": timer_id,
                "expected": expected,
                "target": target,
                "changed_at": changed_at,
            },
        )
    return bool(value)


def schedule_timer(
    db: Session,
    *,
    scope: Scope,
    identity: TimerIdentity,
    due_at: datetime,
    output: TimerOutput,
    recorded_at: datetime,
    expires_at: datetime | None = None,
) -> ScheduleResult:
    """Append one generation and delay its kernel outbox row until ``due_at``."""
    _aware("due_at", due_at)
    _aware("recorded_at", recorded_at)
    if expires_at is not None:
        _aware("expires_at", expires_at)

    _lock_identity(db, scope, identity)
    previous = _latest(db, scope, identity)
    generation = 1 if previous is None else previous.generation + 1
    if previous is not None and previous.status == SCHEDULED:
        if not _transition(
            db,
            scope=scope,
            timer_id=previous.id,
            expected=SCHEDULED,
            target=SUPERSEDED,
            changed_at=recorded_at,
        ):
            raise TimerError(
                "transition_conflict", "current generation changed unexpectedly"
            )
        db.expire(previous)

    timer_id = uuid4()
    trigger = TimerTrigger(
        timer_id=timer_id,
        identity=identity,
        generation=generation,
        due_at=due_at,
        output=output,
    )
    if isinstance(scope, TenantScope):
        # Function-local by design. Importing a Python submodule first executes
        # `dotmac_kernel.messaging.__init__`, whose command adapter reaches the
        # process DB authority. Package discovery must stay DB-free; scheduling
        # is the first point at which this outbox dependency is needed.
        from dotmac_kernel.messaging.outbox import enqueue_event

        tenant_outbox = enqueue_event(
            db,
            tenant_id=scope.tenant_id,
            event_type=output.event_type,
            payload=trigger.as_payload(),
            correlation_id=str(timer_id),
        )
        tenant_outbox.available_at = due_at
        outbox_event_id = tenant_outbox.id
        row: Timer | PlatformTimer = Timer(
            id=timer_id,
            tenant_id=scope.tenant_id,
            owner=identity.owner,
            entity_kind=identity.entity_kind,
            entity_id=identity.entity_id,
            purpose=identity.purpose,
            generation=generation,
            status=SCHEDULED,
            due_at=due_at,
            output_event_type=output.event_type,
            outbox_event_id=tenant_outbox.id,
            recorded_at=recorded_at,
            expires_at=expires_at,
        )
    else:
        from dotmac_kernel.messaging.outbox import enqueue_platform_event

        platform_outbox = enqueue_platform_event(
            db,
            event_type=output.event_type,
            payload=trigger.as_payload(),
            correlation_id=str(timer_id),
        )
        platform_outbox.available_at = due_at
        outbox_event_id = platform_outbox.id
        row = PlatformTimer(
            id=timer_id,
            owner=identity.owner,
            entity_kind=identity.entity_kind,
            entity_id=identity.entity_id,
            purpose=identity.purpose,
            generation=generation,
            status=SCHEDULED,
            due_at=due_at,
            output_event_type=output.event_type,
            outbox_event_id=platform_outbox.id,
            recorded_at=recorded_at,
            expires_at=expires_at,
        )
    db.add(row)
    db.flush()
    return ScheduleResult(
        timer_id=timer_id,
        outbox_event_id=outbox_event_id,
        identity=identity,
        generation=generation,
        due_at=due_at,
        output=output,
    )


def cancel_timer(
    db: Session,
    *,
    scope: Scope,
    identity: TimerIdentity,
    recorded_at: datetime,
) -> CancelResult:
    """Cancel the current generation, preserving a distinct terminal verdict."""
    _aware("recorded_at", recorded_at)
    _lock_identity(db, scope, identity)
    current = _latest(db, scope, identity)
    if current is None:
        return CancelResult(CancelOutcome.NOTHING_SCHEDULED, None)
    if current.status == FIRED:
        return CancelResult(CancelOutcome.ALREADY_FIRED, current.generation)
    if current.status == CANCELED:
        return CancelResult(CancelOutcome.ALREADY_CANCELED, current.generation)
    if current.status != SCHEDULED:
        return CancelResult(CancelOutcome.NOTHING_SCHEDULED, current.generation)
    generation = current.generation
    if not _transition(
        db,
        scope=scope,
        timer_id=current.id,
        expected=SCHEDULED,
        target=CANCELED,
        changed_at=recorded_at,
    ):
        raise TimerError(
            "transition_conflict", "current generation changed unexpectedly"
        )
    db.expire(current)
    return CancelResult(CancelOutcome.CANCELED, generation)


def _accepted(
    db: Session, scope: Scope, timer_id: UUID
) -> TimerAcceptance | PlatformTimerAcceptance | None:
    if isinstance(scope, TenantScope):
        tenant_statement = select(TimerAcceptance).where(
            TimerAcceptance.timer_id == timer_id,
            TimerAcceptance.tenant_id == scope.tenant_id,
        )
        return db.scalar(tenant_statement.limit(1))
    platform_statement = select(PlatformTimerAcceptance).where(
        PlatformTimerAcceptance.timer_id == timer_id
    )
    return db.scalar(platform_statement.limit(1))


def accept_trigger(
    db: Session,
    *,
    scope: Scope,
    trigger: TimerTrigger,
    accepted_at: datetime,
) -> AcceptanceResult:
    """Accept only the current generation immediately before the local effect."""
    _aware("accepted_at", accepted_at)
    _lock_identity(db, scope, trigger.identity)
    timer = _timer_by_trigger(db, scope, trigger)
    latest = _latest(db, scope, trigger.identity)
    current_generation = latest.generation if latest is not None else None
    if timer is None or timer.status == SUPERSEDED:
        return AcceptanceResult(
            AcceptanceOutcome.STALE,
            trigger.generation,
            current_generation,
        )
    if timer.status == CANCELED:
        return AcceptanceResult(
            AcceptanceOutcome.CANCELED,
            trigger.generation,
            current_generation,
        )
    if timer.status == FIRED:
        replayed = _accepted(db, scope, timer.id) is not None
        return AcceptanceResult(
            AcceptanceOutcome.CURRENT if replayed else AcceptanceOutcome.ALREADY_FIRED,
            trigger.generation,
            current_generation,
            replayed=replayed,
        )
    if timer.status != SCHEDULED or current_generation != trigger.generation:
        return AcceptanceResult(
            AcceptanceOutcome.STALE,
            trigger.generation,
            current_generation,
        )
    if not _transition(
        db,
        scope=scope,
        timer_id=timer.id,
        expected=SCHEDULED,
        target=FIRED,
        changed_at=accepted_at,
    ):
        raise TimerError(
            "transition_conflict", "current generation changed unexpectedly"
        )
    db.expire(timer)
    if isinstance(scope, TenantScope):
        evidence: TimerAcceptance | PlatformTimerAcceptance = TimerAcceptance(
            tenant_id=scope.tenant_id,
            timer_id=timer.id,
            accepted_at=accepted_at,
        )
    else:
        evidence = PlatformTimerAcceptance(
            timer_id=timer.id,
            accepted_at=accepted_at,
        )
    db.add(evidence)
    db.flush()
    return AcceptanceResult(
        AcceptanceOutcome.CURRENT,
        trigger.generation,
        current_generation,
    )


def purge_history(
    db: Session,
    *,
    scope: Scope,
    before: datetime,
    limit: int,
) -> int:
    """Delete bounded terminal history; a scheduled generation is never eligible."""
    _aware("before", before)
    if limit < 1:
        raise TimerError("invalid_limit", "limit must be positive")
    if isinstance(scope, TenantScope):
        value = db.scalar(
            text("SELECT mod_timers.purge_timer_history(:tenant_id, :before, :limit)"),
            {"tenant_id": scope.tenant_id, "before": before, "limit": limit},
        )
    else:
        value = db.scalar(
            text("SELECT mod_timers.purge_platform_timer_history(:before, :limit)"),
            {"before": before, "limit": limit},
        )
    return int(value or 0)


__all__ = [
    "AcceptanceOutcome",
    "AcceptanceResult",
    "CancelOutcome",
    "CancelResult",
    "ScheduleResult",
    "TimerError",
    "TimerIdentity",
    "TimerOutput",
    "TimerTrigger",
    "accept_trigger",
    "cancel_timer",
    "purge_history",
    "schedule_timer",
]
