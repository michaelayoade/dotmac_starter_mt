"""Response-obligation commands; callers own authorization and transactions.

The whole module is one piece of arithmetic done carefully: how much time a
promise has left, given that some of the elapsed time did not count.

`due_at` is stored and MOVED by a resume, rather than derived from
`started_at + target - total_paused`. Two reasons. A derived due time disagrees
with whatever timer the assembly already scheduled against the old value, and
it makes the sweep's index — (tenant, status, due_at) — impossible, forcing the
rescan this module exists to avoid.

Services mutate and flush inside the caller's transaction; they never commit or
roll back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging import enqueue_event
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_response_obligations.contracts import (
    CancelClock,
    ClockStatus,
    CompleteClock,
    Conflict,
    EscalationRequested,
    ObligationKind,
    ObservationKind,
    PauseClock,
    RegisterPolicy,
    ResumeClock,
    SetTarget,
    StartClock,
    SweepDueClocks,
    SweptObligations,
)
from dotmac_response_obligations.models import (
    ResponseClock,
    ResponseClockPause,
    ResponseObservation,
    ResponsePolicy,
    ResponseTarget,
)

# Declared on this module's manifest; a producer may enqueue an event type only
# after the installed manifest set declares it, so these names and the manifest
# tuple move together.
WARNING_EVENT = "response_obligations.obligation_at_risk.v1"
BREACH_EVENT = "response_obligations.obligation_breached.v1"

# The severity each observation ASKS the escalation owner for. Opaque strings,
# not a declared vocabulary: `dotmac-operational-escalations` owns severity
# terms and naming them twice is how the two drift.
_SEVERITY: dict[ObservationKind, str] = {
    ObservationKind.WARNING: "NORMAL",
    ObservationKind.BREACH: "HIGH",
}

_LIVE = (ClockStatus.RUNNING, ClockStatus.PAUSED)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-response-obligations requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _instant(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive round trip for portable unit canaries."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def register_policy(
    db: Session, *, scope: TenantScope, command: RegisterPolicy
) -> ResponsePolicy:
    row = ResponsePolicy(
        tenant_id=_tenant(scope),
        code=_required(command.code, "policy code"),
        name=_required(command.name, "policy name"),
        subject_type=_required(command.subject_type, "subject type"),
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def set_target(
    db: Session, *, scope: TenantScope, command: SetTarget
) -> ResponseTarget:
    """Declare or replace one promise. Replacing does not move live clocks.

    A clock binds the target row it started under, so tightening a target
    tomorrow cannot retroactively breach work that was answered under
    yesterday's promise.
    """
    tenant_id = _tenant(scope)
    if command.target_seconds <= 0:
        raise Conflict("a response target must be a positive duration")
    if command.warning_seconds is not None and not (
        0 < command.warning_seconds < command.target_seconds
    ):
        raise Conflict("a warning must fall inside the target it warns about")
    policy = db.scalar(
        select(ResponsePolicy).where(
            ResponsePolicy.tenant_id == tenant_id,
            ResponsePolicy.id == command.policy_id,
        )
    )
    if policy is None or not policy.active:
        raise Conflict("response policy was not found or is inactive")
    priority = (
        _required(command.priority, "priority")
        if command.priority is not None
        else None
    )
    existing = db.scalar(
        select(ResponseTarget)
        .where(
            ResponseTarget.tenant_id == tenant_id,
            ResponseTarget.policy_id == policy.id,
            ResponseTarget.kind == command.kind,
            ResponseTarget.priority.is_(None)
            if priority is None
            else ResponseTarget.priority == priority,
        )
        .with_for_update()
    )
    if existing is not None:
        existing.target_seconds = command.target_seconds
        existing.warning_seconds = command.warning_seconds
        existing.active = True
        db.flush()
        return existing
    row = ResponseTarget(
        tenant_id=tenant_id,
        policy_id=policy.id,
        kind=command.kind,
        priority=priority,
        target_seconds=command.target_seconds,
        warning_seconds=command.warning_seconds,
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def _resolve_target(
    db: Session,
    *,
    tenant_id: UUID,
    policy: ResponsePolicy,
    kind: ObligationKind,
    priority: str | None,
) -> ResponseTarget:
    """The priority-specific promise, else the default. Never a guess.

    `priority.desc()` puts the non-NULL row first, so one query answers "the
    specific one if it exists" without a second round trip.
    """
    row = db.scalar(
        select(ResponseTarget)
        .where(
            ResponseTarget.tenant_id == tenant_id,
            ResponseTarget.policy_id == policy.id,
            ResponseTarget.kind == kind,
            ResponseTarget.active.is_(True),
            or_(
                ResponseTarget.priority.is_(None),
                ResponseTarget.priority == priority,
            )
            if priority is not None
            else ResponseTarget.priority.is_(None),
        )
        .order_by(ResponseTarget.priority.desc())
        .limit(1)
    )
    if row is None:
        raise Conflict("no response target matches the policy, kind and priority")
    return row


def _clock_for(db: Session, tenant_id: UUID, dedup_key: str) -> ResponseClock | None:
    return db.scalar(
        select(ResponseClock).where(
            ResponseClock.tenant_id == tenant_id,
            ResponseClock.dedup_key == dedup_key,
        )
    )


def start_clock(
    db: Session, *, scope: TenantScope, command: StartClock
) -> ResponseClock:
    """Begin measuring one promise. Idempotent on `dedup_key`.

    A retried webhook must not start a second first-response clock: the second
    would be measured from a later instant, so the two would disagree about
    when the desk was late.
    """
    tenant_id = _tenant(scope)
    dedup_key = _required(command.dedup_key, "dedup key")
    subject = _required(command.subject_reference, "subject reference")
    started_at = _aware(command.started_at, "started_at")
    priority = (
        _required(command.priority, "priority")
        if command.priority is not None
        else None
    )
    existing = _clock_for(db, tenant_id, dedup_key)
    if existing is not None:
        if existing.subject_reference != subject or existing.kind is not command.kind:
            raise Conflict("clock dedup key was reused for different work")
        return existing

    policy = db.scalar(
        select(ResponsePolicy).where(
            ResponsePolicy.tenant_id == tenant_id,
            ResponsePolicy.code == _required(command.policy_code, "policy code"),
        )
    )
    if policy is None or not policy.active:
        raise Conflict("response policy was not found or is inactive")
    target = _resolve_target(
        db, tenant_id=tenant_id, policy=policy, kind=command.kind, priority=priority
    )
    due_at = started_at + timedelta(seconds=target.target_seconds)
    row = ResponseClock(
        tenant_id=tenant_id,
        policy_id=policy.id,
        target_id=target.id,
        subject_type=policy.subject_type,
        subject_reference=subject,
        dedup_key=dedup_key,
        kind=command.kind,
        priority=priority,
        status=ClockStatus.RUNNING,
        started_at=started_at,
        due_at=due_at,
        warn_at=(
            due_at - timedelta(seconds=target.warning_seconds)
            if target.warning_seconds is not None
            else None
        ),
        total_paused_seconds=0,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = _clock_for(db, tenant_id, dedup_key)
        if winner is not None:
            return winner
        live = db.scalar(
            select(ResponseClock).where(
                ResponseClock.tenant_id == tenant_id,
                ResponseClock.subject_reference == subject,
                ResponseClock.kind == command.kind,
                ResponseClock.status.in_(_LIVE),
            )
        )
        if live is not None:
            raise Conflict("the subject already has a live clock of this kind") from exc
        raise Conflict("clock creation conflicted with operational state") from exc
    return row


def _live_clock(db: Session, tenant_id: UUID, clock_id: UUID) -> ResponseClock:
    row = db.scalar(
        select(ResponseClock)
        .where(ResponseClock.tenant_id == tenant_id, ResponseClock.id == clock_id)
        .with_for_update()
    )
    if row is None:
        raise Conflict("clock was not found in the tenant")
    if row.status not in _LIVE:
        raise Conflict("only a running or paused clock can be changed")
    return row


def pause_clock(
    db: Session, *, scope: TenantScope, command: PauseClock
) -> ResponseClockPause:
    """Stop counting, and record why.

    The reason is required by the command's type, not left to a free-text note,
    because "waiting on the customer" and "we were closed" defend a missed
    target very differently and only one of them is the desk's fault.
    """
    tenant_id = _tenant(scope)
    paused_at = _aware(command.paused_at, "paused_at")
    clock = _live_clock(db, tenant_id, command.clock_id)
    if clock.status is ClockStatus.PAUSED:
        raise Conflict("clock is already paused")
    if _instant(paused_at) < _instant(clock.started_at):
        raise Conflict("a pause cannot precede the clock it stops")
    clock.status = ClockStatus.PAUSED
    clock.paused_at = paused_at
    row = ResponseClockPause(
        tenant_id=tenant_id,
        clock_id=clock.id,
        reason=command.reason,
        paused_at=paused_at,
        actor_reference=command.actor_reference,
        note=command.note,
    )
    db.add(row)
    db.flush()
    return row


def resume_clock(
    db: Session, *, scope: TenantScope, command: ResumeClock
) -> ResponseClock:
    """Start counting again, and push the deadline out by the time not counted.

    Moving `due_at` rather than subtracting at read time is what keeps the
    sweep's index truthful: a paused clock whose stored due time still said
    "10:00" would be swept as breached while nobody owed an answer.
    """
    tenant_id = _tenant(scope)
    resumed_at = _aware(command.resumed_at, "resumed_at")
    clock = _live_clock(db, tenant_id, command.clock_id)
    if clock.status is not ClockStatus.PAUSED or clock.paused_at is None:
        raise Conflict("only a paused clock can be resumed")
    if _instant(resumed_at) < _instant(clock.paused_at):
        raise Conflict("a resume cannot precede the pause it ends")
    open_pause = db.scalar(
        select(ResponseClockPause)
        .where(
            ResponseClockPause.tenant_id == tenant_id,
            ResponseClockPause.clock_id == clock.id,
            ResponseClockPause.resumed_at.is_(None),
        )
        .with_for_update()
    )
    if open_pause is None:  # pragma: no cover - write-time invariant
        raise Conflict("paused clock has no open pause interval")
    elapsed = int((_instant(resumed_at) - _instant(clock.paused_at)).total_seconds())
    open_pause.resumed_at = resumed_at
    clock.total_paused_seconds += elapsed
    clock.due_at = clock.due_at + timedelta(seconds=elapsed)
    if clock.warn_at is not None:
        clock.warn_at = clock.warn_at + timedelta(seconds=elapsed)
    clock.paused_at = None
    clock.status = ClockStatus.RUNNING
    db.flush()
    return clock


def complete_clock(
    db: Session, *, scope: TenantScope, command: CompleteClock
) -> ResponseClock:
    """The response happened. Late completion does not erase the breach.

    A clock answered after its due time settles as `BREACHED` with a settlement
    instant, because "we answered eventually" and "we answered in time" are
    different facts and reporting must be able to tell them apart.
    """
    tenant_id = _tenant(scope)
    completed_at = _aware(command.completed_at, "completed_at")
    clock = _live_clock(db, tenant_id, command.clock_id)
    if _instant(completed_at) < _instant(clock.started_at):
        raise Conflict("a completion cannot precede the clock it ends")
    late = _instant(completed_at) > _instant(clock.due_at)
    clock.status = ClockStatus.BREACHED if late else ClockStatus.MET
    if late and clock.breached_at is None:
        clock.breached_at = clock.due_at
    clock.settled_at = completed_at
    clock.settlement_reason = "response recorded"
    clock.paused_at = None
    db.flush()
    return clock


def cancel_clock(
    db: Session, *, scope: TenantScope, command: CancelClock
) -> ResponseClock:
    """The promise stopped applying. Neither kept nor missed."""
    tenant_id = _tenant(scope)
    cancelled_at = _aware(command.cancelled_at, "cancelled_at")
    reason = _required(command.reason, "cancellation reason")
    clock = _live_clock(db, tenant_id, command.clock_id)
    clock.status = ClockStatus.CANCELLED
    clock.settled_at = cancelled_at
    clock.settlement_reason = reason
    clock.paused_at = None
    db.flush()
    return clock


def _observe(
    db: Session,
    *,
    tenant_id: UUID,
    clock: ResponseClock,
    kind: ObservationKind,
    observed_at: datetime,
) -> EscalationRequested | None:
    """Record one threshold crossing, once.

    The dedup key carries the clock, the threshold and the deadline it was
    measured against — so a resume that moves `due_at` legitimately allows a
    NEW breach observation against the new deadline, while a re-run of the
    sweep against the same deadline does not.
    """
    dedup_key = f"sla:{clock.id}:{kind.value}:{_instant(clock.due_at).isoformat()}"
    if db.scalar(
        select(ResponseObservation.id).where(
            ResponseObservation.tenant_id == tenant_id,
            ResponseObservation.dedup_key == dedup_key,
        )
    ):
        return None
    row = ResponseObservation(
        tenant_id=tenant_id,
        clock_id=clock.id,
        dedup_key=dedup_key,
        kind=kind,
        due_at=clock.due_at,
        observed_at=observed_at,
    )
    db.add(row)
    db.flush()
    severity = _SEVERITY[kind]
    enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=WARNING_EVENT if kind is ObservationKind.WARNING else BREACH_EVENT,
        payload={
            "observation_id": str(row.id),
            "clock_id": str(clock.id),
            "subject_type": clock.subject_type,
            "subject_reference": clock.subject_reference,
            "obligation_kind": clock.kind.value,
            "severity": severity,
            "due_at": _instant(clock.due_at).isoformat(),
            "dedup_key": dedup_key,
        },
    )
    return EscalationRequested(
        observation_id=row.id,
        clock_id=clock.id,
        subject_type=clock.subject_type,
        subject_reference=clock.subject_reference,
        kind=clock.kind,
        observation=kind,
        severity=severity,
        dedup_key=dedup_key,
        due_at=clock.due_at,
        observed_at=observed_at,
    )


def sweep_due_clocks(
    db: Session, *, scope: TenantScope, command: SweepDueClocks
) -> SweptObligations:
    """Record every threshold crossed, and hand back the escalations to raise.

    Bounded and index-driven: it reads the front of
    (tenant, status, due_at), never the table. A paused clock is excluded
    entirely — nobody owes an answer while the promise is stopped, and sweeping
    one would breach a customer for a night the desk was closed.
    """
    tenant_id = _tenant(scope)
    observed_at = _aware(command.observed_at, "observed_at")
    if command.limit < 1:
        raise ValueError("limit must be positive")
    candidates = list(
        db.scalars(
            select(ResponseClock)
            .where(
                ResponseClock.tenant_id == tenant_id,
                ResponseClock.status == ClockStatus.RUNNING,
                or_(
                    ResponseClock.due_at <= observed_at,
                    ResponseClock.warn_at <= observed_at,
                ),
            )
            .order_by(ResponseClock.due_at, ResponseClock.id)
            .limit(command.limit)
            .with_for_update(skip_locked=True)
        )
    )
    warned: list[UUID] = []
    breached: list[UUID] = []
    requests: list[EscalationRequested] = []
    for clock in candidates:
        if _instant(clock.due_at) <= _instant(observed_at):
            request = _observe(
                db,
                tenant_id=tenant_id,
                clock=clock,
                kind=ObservationKind.BREACH,
                observed_at=observed_at,
            )
            if request is not None:
                breached.append(clock.id)
                requests.append(request)
            # The clock stays RUNNING: the promise is missed but the answer is
            # still owed, and moving it to a terminal state here would lose the
            # eventual response time reporting needs.
            if clock.breached_at is None:
                clock.breached_at = clock.due_at
            continue
        request = _observe(
            db,
            tenant_id=tenant_id,
            clock=clock,
            kind=ObservationKind.WARNING,
            observed_at=observed_at,
        )
        if request is not None:
            warned.append(clock.id)
            requests.append(request)
    db.flush()
    return SweptObligations(
        warned=tuple(warned),
        breached=tuple(breached),
        escalation_requests=tuple(requests),
    )


__all__ = [
    "BREACH_EVENT",
    "WARNING_EVENT",
    "cancel_clock",
    "complete_clock",
    "pause_clock",
    "register_policy",
    "resume_clock",
    "set_target",
    "start_clock",
    "sweep_due_clocks",
]
