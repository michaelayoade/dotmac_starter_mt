"""One timer-generation lifecycle over two explicitly selected planes.

Scheduling serializes on a PostgreSQL transaction advisory lock derived from
scope plus identity; it exists before the first row does. Scheduling,
cancellation and acceptance also lock the latest generation through a
security-definer helper, so online roles can serialize transitions without
receiving permission to rewrite timer history directly.

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
from dotmac_kernel.outbox_event_types import active_outbox_event_types
from sqlalchemy import Select, func, select, text
from sqlalchemy.orm import Session

from dotmac_durable_timers.models import (
    PlatformTimer,
    PlatformTimerAcceptance,
    PlatformTimerRejection,
    Timer,
    TimerAcceptance,
    TimerRejection,
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


def _generation(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise TimerError(f"invalid_{name}", f"{name} must be a positive integer")


def _source_version(value: int | None) -> None:
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise TimerError(
            "invalid_expected_source_version",
            "expected_source_version must be an integer or None",
        )


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
    expected_source_version: int | None = None

    def __post_init__(self) -> None:
        _generation("generation", self.generation)
        _aware("due_at", self.due_at)
        _source_version(self.expected_source_version)

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
            "expected_source_version": self.expected_source_version,
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
            source_version = payload.get("expected_source_version")
            if source_version is not None and (
                isinstance(source_version, bool) or not isinstance(source_version, int)
            ):
                raise ValueError("expected_source_version must be an integer")
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
            expected_source_version=source_version,
        )

    @classmethod
    def for_scheduled(cls, scheduled: ScheduleResult) -> TimerTrigger:
        return cls(
            timer_id=scheduled.timer_id,
            identity=scheduled.identity,
            generation=scheduled.generation,
            due_at=scheduled.due_at,
            output=scheduled.output,
            expected_source_version=scheduled.expected_source_version,
        )


@dataclass(frozen=True, slots=True)
class ScheduleResult:
    timer_id: UUID
    outbox_event_id: UUID
    identity: TimerIdentity
    generation: int
    due_at: datetime
    output: TimerOutput
    expected_source_version: int | None


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
    expected_source_version: int | None
    replayed: bool = False


class CancelOutcome(str, Enum):
    CANCELED = "canceled"
    ALREADY_FIRED = "already_fired"
    NOTHING_SCHEDULED = "nothing_scheduled"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class CancelResult:
    outcome: CancelOutcome
    observed_generation: int
    current_generation: int | None


@dataclass(frozen=True, slots=True)
class TimerSnapshot:
    timer_id: UUID
    identity: TimerIdentity
    generation: int
    status: str
    due_at: datetime
    output: TimerOutput
    expected_source_version: int | None
    recorded_at: datetime
    superseded_at: datetime | None
    canceled_at: datetime | None
    fired_at: datetime | None
    expires_at: datetime | None


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
    db: Session,
    scope: Scope,
    identity: TimerIdentity,
    *,
    for_update: bool,
) -> Timer | PlatformTimer | None:
    if isinstance(scope, TenantScope):
        if for_update:
            db.scalar(
                text(
                    "SELECT mod_timers.lock_timer_identity("
                    ":tenant_id, :owner, :entity_kind, :entity_id, :purpose)"
                ),
                {
                    "tenant_id": scope.tenant_id,
                    "owner": identity.owner,
                    "entity_kind": identity.entity_kind,
                    "entity_id": identity.entity_id,
                    "purpose": identity.purpose,
                },
            )
        tenant_statement = (
            _tenant_identity_query(identity)
            .where(Timer.tenant_id == scope.tenant_id)
            .order_by(Timer.generation.desc())
            .limit(1)
        )
        return db.scalar(tenant_statement)
    if for_update:
        db.scalar(
            text(
                "SELECT mod_timers.lock_platform_timer_identity("
                ":owner, :entity_kind, :entity_id, :purpose)"
            ),
            {
                "owner": identity.owner,
                "entity_kind": identity.entity_kind,
                "entity_id": identity.entity_id,
                "purpose": identity.purpose,
            },
        )
    platform_statement = (
        _platform_identity_query(identity)
        .order_by(PlatformTimer.generation.desc())
        .limit(1)
    )
    return db.scalar(platform_statement)


def _snapshot(row: Timer | PlatformTimer) -> TimerSnapshot:
    return TimerSnapshot(
        timer_id=row.id,
        identity=TimerIdentity(
            owner=row.owner,
            entity_kind=row.entity_kind,
            entity_id=row.entity_id,
            purpose=row.purpose,
        ),
        generation=row.generation,
        status=row.status,
        due_at=row.due_at,
        output=TimerOutput(row.output_event_type),
        expected_source_version=row.expected_source_version,
        recorded_at=row.recorded_at,
        superseded_at=row.superseded_at,
        canceled_at=row.canceled_at,
        fired_at=row.fired_at,
        expires_at=row.expires_at,
    )


def current_timer(
    db: Session, *, scope: Scope, identity: TimerIdentity
) -> TimerSnapshot | None:
    """Read the latest generation without exporting the package's ORM models."""
    row = _latest(db, scope, identity, for_update=False)
    return None if row is None else _snapshot(row)


def _trigger_matches_current(
    trigger: TimerTrigger, current: Timer | PlatformTimer | None
) -> bool:
    return (
        current is not None
        and current.id == trigger.timer_id
        and current.generation == trigger.generation
    )


def _trigger_evidence_matches_current(
    trigger: TimerTrigger, current: Timer | PlatformTimer
) -> bool:
    return (
        current.due_at == trigger.due_at
        and current.output_event_type == trigger.output.event_type
        and current.expected_source_version == trigger.expected_source_version
    )


def _existing_rejection(
    db: Session,
    *,
    scope: Scope,
    trigger: TimerTrigger,
    current_generation: int | None,
) -> TimerRejection | PlatformTimerRejection | None:
    if isinstance(scope, TenantScope):
        statement = select(TimerRejection).where(
            TimerRejection.tenant_id == scope.tenant_id,
            TimerRejection.timer_id == trigger.timer_id,
        )
        if current_generation is None:
            statement = statement.where(TimerRejection.current_generation.is_(None))
        else:
            statement = statement.where(
                TimerRejection.current_generation == current_generation
            )
        return db.scalar(statement.limit(1))
    platform_statement = select(PlatformTimerRejection).where(
        PlatformTimerRejection.timer_id == trigger.timer_id
    )
    if current_generation is None:
        platform_statement = platform_statement.where(
            PlatformTimerRejection.current_generation.is_(None)
        )
    else:
        platform_statement = platform_statement.where(
            PlatformTimerRejection.current_generation == current_generation
        )
    return db.scalar(platform_statement.limit(1))


def _record_stale_rejection(
    db: Session,
    *,
    scope: Scope,
    trigger: TimerTrigger,
    current_generation: int | None,
    rejected_at: datetime,
) -> None:
    if (
        _existing_rejection(
            db,
            scope=scope,
            trigger=trigger,
            current_generation=current_generation,
        )
        is not None
    ):
        return
    values = {
        "id": uuid4(),
        "timer_id": trigger.timer_id,
        "owner": trigger.identity.owner,
        "entity_kind": trigger.identity.entity_kind,
        "entity_id": trigger.identity.entity_id,
        "purpose": trigger.identity.purpose,
        "observed_generation": trigger.generation,
        "current_generation": current_generation,
        "expected_source_version": trigger.expected_source_version,
        "rejected_at": rejected_at,
    }
    if isinstance(scope, TenantScope):
        evidence: TimerRejection | PlatformTimerRejection = TimerRejection(
            tenant_id=scope.tenant_id, **values
        )
    else:
        evidence = PlatformTimerRejection(**values)
    db.add(evidence)
    db.flush()


def _cancel_is_stale(observed_generation: int, current_generation: int) -> bool:
    return observed_generation != current_generation


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
    expected_source_version: int | None = None,
    expires_at: datetime | None = None,
) -> ScheduleResult:
    """Append one generation and delay its kernel outbox row until ``due_at``."""
    _aware("due_at", due_at)
    _aware("recorded_at", recorded_at)
    _source_version(expected_source_version)
    if expires_at is not None:
        _aware("expires_at", expires_at)
    active_outbox_event_types().require(output.event_type)

    _lock_identity(db, scope, identity)
    previous = _latest(db, scope, identity, for_update=True)
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
        expected_source_version=expected_source_version,
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
            expected_source_version=expected_source_version,
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
            expected_source_version=expected_source_version,
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
        expected_source_version=expected_source_version,
    )


def cancel_timer(
    db: Session,
    *,
    scope: Scope,
    identity: TimerIdentity,
    observed_generation: int,
    recorded_at: datetime,
) -> CancelResult:
    """Cancel exactly the generation the caller observed, or report staleness."""
    _aware("recorded_at", recorded_at)
    _generation("observed_generation", observed_generation)
    current = _latest(db, scope, identity, for_update=True)
    if current is None:
        return CancelResult(
            CancelOutcome.NOTHING_SCHEDULED,
            observed_generation,
            None,
        )
    if _cancel_is_stale(observed_generation, current.generation):
        return CancelResult(
            CancelOutcome.STALE,
            observed_generation,
            current.generation,
        )
    if current.status == FIRED:
        return CancelResult(
            CancelOutcome.ALREADY_FIRED,
            observed_generation,
            current.generation,
        )
    if current.status == CANCELED:
        return CancelResult(
            CancelOutcome.CANCELED,
            observed_generation,
            current.generation,
        )
    if current.status != SCHEDULED:
        return CancelResult(
            CancelOutcome.NOTHING_SCHEDULED,
            observed_generation,
            current.generation,
        )
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
    return CancelResult(
        CancelOutcome.CANCELED,
        observed_generation,
        generation,
    )


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
    latest = _latest(db, scope, trigger.identity, for_update=True)
    current_generation = latest.generation if latest is not None else None
    if not _trigger_matches_current(trigger, latest):
        _record_stale_rejection(
            db,
            scope=scope,
            trigger=trigger,
            current_generation=current_generation,
            rejected_at=accepted_at,
        )
        return AcceptanceResult(
            AcceptanceOutcome.STALE,
            trigger.generation,
            current_generation,
            trigger.expected_source_version,
        )
    if latest is None:
        raise TimerError(
            "transition_conflict",
            "current timer disappeared after current-generation verification",
        )
    if not _trigger_evidence_matches_current(trigger, latest):
        raise TimerError(
            "invalid_trigger_evidence",
            "timer trigger evidence does not match the recorded generation",
        )
    if latest.status == CANCELED:
        return AcceptanceResult(
            AcceptanceOutcome.CANCELED,
            trigger.generation,
            current_generation,
            latest.expected_source_version,
        )
    if latest.status == FIRED:
        replayed = _accepted(db, scope, latest.id) is not None
        return AcceptanceResult(
            AcceptanceOutcome.CURRENT if replayed else AcceptanceOutcome.ALREADY_FIRED,
            trigger.generation,
            current_generation,
            latest.expected_source_version,
            replayed=replayed,
        )
    if latest.status != SCHEDULED:
        _record_stale_rejection(
            db,
            scope=scope,
            trigger=trigger,
            current_generation=current_generation,
            rejected_at=accepted_at,
        )
        return AcceptanceResult(
            AcceptanceOutcome.STALE,
            trigger.generation,
            current_generation,
            latest.expected_source_version,
        )
    if not _transition(
        db,
        scope=scope,
        timer_id=latest.id,
        expected=SCHEDULED,
        target=FIRED,
        changed_at=accepted_at,
    ):
        raise TimerError(
            "transition_conflict", "current generation changed unexpectedly"
        )
    db.expire(latest)
    if isinstance(scope, TenantScope):
        evidence: TimerAcceptance | PlatformTimerAcceptance = TimerAcceptance(
            tenant_id=scope.tenant_id,
            timer_id=latest.id,
            accepted_at=accepted_at,
        )
    else:
        evidence = PlatformTimerAcceptance(
            timer_id=latest.id,
            accepted_at=accepted_at,
        )
    db.add(evidence)
    db.flush()
    return AcceptanceResult(
        AcceptanceOutcome.CURRENT,
        trigger.generation,
        current_generation,
        latest.expected_source_version,
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
    "TimerSnapshot",
    "TimerError",
    "TimerIdentity",
    "TimerOutput",
    "TimerTrigger",
    "accept_trigger",
    "cancel_timer",
    "current_timer",
    "purge_history",
    "schedule_timer",
]
