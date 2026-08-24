"""Response-obligation behaviour canaries ported from Sub's SLA clock.

Every test here is a claim about the arithmetic: how much time a promise has
left once some of the elapsed time did not count.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.models import Tenant
from dotmac_response_obligations.contracts import (
    CancelClock,
    ClockStatus,
    CompleteClock,
    Conflict,
    ObligationKind,
    ObservationKind,
    PauseClock,
    PauseReason,
    RegisterPolicy,
    ResumeClock,
    SetTarget,
    StartClock,
    SweepDueClocks,
)
from dotmac_response_obligations.models import (
    TENANT_TABLES,
    ResponseClockPause,
    ResponseObservation,
)
from dotmac_response_obligations.service import (
    cancel_clock,
    complete_clock,
    pause_clock,
    register_policy,
    resume_clock,
    set_target,
    start_clock,
    sweep_due_clocks,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
NOW = datetime(2026, 8, 24, 9, 0, tzinfo=UTC)
SCOPE = TenantScope(TENANT_A)
HOUR = 3600


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_sla": None}},
    )
    Tenant.__table__.create(engine)
    OutboxEvent.__table__.create(engine)
    from dotmac_response_obligations import models

    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_A, slug="a", name="A"))
        session.flush()
        yield session
    engine.dispose()


def _policy(db: Session, *, subject_type: str = "conversation"):
    return register_policy(
        db,
        scope=SCOPE,
        command=RegisterPolicy("support", "Support", subject_type),
    )


def _target(
    db: Session,
    policy,
    *,
    seconds: int = 4 * HOUR,
    warning: int | None = None,
    priority: str | None = None,
    kind: ObligationKind = ObligationKind.FIRST_RESPONSE,
):
    return set_target(
        db,
        scope=SCOPE,
        command=SetTarget(
            policy_id=policy.id,
            kind=kind,
            target_seconds=seconds,
            priority=priority,
            warning_seconds=warning,
        ),
    )


def _at(value: datetime) -> datetime:
    """SQLite drops the tzinfo on round trip; the promise is still in UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _start(
    db: Session,
    *,
    at: datetime = NOW,
    priority: str | None = None,
    key="c1",
    subject: str = "conv-1",
):
    return start_clock(
        db,
        scope=SCOPE,
        command=StartClock(
            policy_code="support",
            subject_reference=subject,
            kind=ObligationKind.FIRST_RESPONSE,
            started_at=at,
            dedup_key=key,
            priority=priority,
        ),
    )


def _events(db: Session) -> list[str]:
    return [row.event_type for row in db.scalars(select(OutboxEvent)).all()]


# ── Targets ─────────────────────────────────────────────────────────────────


def test_a_priority_specific_target_beats_the_default(db: Session) -> None:
    """Two rows say "4 hours, except urgent which is 30 minutes" — rather than
    one row per priority forever."""
    policy = _policy(db)
    _target(db, policy, seconds=4 * HOUR)
    _target(db, policy, seconds=1800, priority="urgent")

    ordinary = _start(db, key="ordinary", subject="conv-ordinary")
    urgent = _start(db, priority="urgent", key="urgent", subject="conv-urgent")
    assert _at(ordinary.due_at) == NOW + timedelta(hours=4)
    assert _at(urgent.due_at) == NOW + timedelta(minutes=30)


def test_an_unknown_priority_falls_back_to_the_default(db: Session) -> None:
    policy = _policy(db)
    _target(db, policy, seconds=4 * HOUR)
    clock = _start(db, priority="chartreuse")
    assert _at(clock.due_at) == NOW + timedelta(hours=4)


def test_a_promise_with_no_target_is_refused_not_guessed(db: Session) -> None:
    _policy(db)
    with pytest.raises(Conflict, match="no response target matches"):
        _start(db)


def test_a_warning_must_fall_inside_the_target_it_warns_about(db: Session) -> None:
    policy = _policy(db)
    with pytest.raises(Conflict, match="inside the target"):
        _target(db, policy, seconds=3600, warning=7200)


def test_setting_a_target_again_replaces_it_without_moving_live_clocks(
    db: Session,
) -> None:
    """Tightening a target tomorrow must not retroactively breach work that was
    answered under yesterday's promise."""
    policy = _policy(db)
    _target(db, policy, seconds=4 * HOUR)
    clock = _start(db)
    _target(db, policy, seconds=HOUR)
    db.refresh(clock)
    assert _at(clock.due_at) == NOW + timedelta(hours=4)


# ── Starting ────────────────────────────────────────────────────────────────


def test_starting_is_idempotent_on_its_dedup_key(db: Session) -> None:
    """A retried webhook must not start a second first-response clock — the
    second would be measured from a later instant and breach on its own."""
    policy = _policy(db)
    _target(db, policy)
    first = _start(db)
    second = _start(db, at=NOW + timedelta(minutes=5))
    assert first.id == second.id
    assert _at(first.due_at) == NOW + timedelta(hours=4)


def test_one_subject_cannot_hold_two_live_clocks_of_a_kind(db: Session) -> None:
    policy = _policy(db)
    _target(db, policy)
    _start(db, key="first")
    with pytest.raises(Conflict, match="already has a live clock"):
        _start(db, key="second")


def test_a_reused_dedup_key_for_different_work_is_refused(db: Session) -> None:
    policy = _policy(db)
    _target(db, policy)
    _start(db)
    with pytest.raises(Conflict, match="reused for different work"):
        start_clock(
            db,
            scope=SCOPE,
            command=StartClock(
                policy_code="support",
                subject_reference="conv-OTHER",
                kind=ObligationKind.FIRST_RESPONSE,
                started_at=NOW,
                dedup_key="c1",
            ),
        )


# ── Paused time ─────────────────────────────────────────────────────────────


def test_paused_time_moves_the_deadline_by_exactly_what_it_cost(
    db: Session,
) -> None:
    policy = _policy(db)
    _target(db, policy, seconds=4 * HOUR, warning=1800)
    clock = _start(db)
    assert _at(clock.warn_at) == NOW + timedelta(hours=3, minutes=30)

    pause_clock(
        db,
        scope=SCOPE,
        command=PauseClock(
            clock_id=clock.id,
            reason=PauseReason.WAITING_ON_CUSTOMER,
            paused_at=NOW + timedelta(hours=1),
        ),
    )
    resume_clock(
        db,
        scope=SCOPE,
        command=ResumeClock(clock_id=clock.id, resumed_at=NOW + timedelta(hours=3)),
    )
    assert clock.status is ClockStatus.RUNNING
    assert clock.total_paused_seconds == 2 * HOUR
    # Both the deadline and the warning move, or the warning would fire during
    # a period nobody owed an answer.
    assert _at(clock.due_at) == NOW + timedelta(hours=6)
    assert _at(clock.warn_at) == NOW + timedelta(hours=5, minutes=30)


def test_every_pause_records_why(db: Session) -> None:
    """The first question asked about any disputed breach."""
    policy = _policy(db)
    _target(db, policy)
    clock = _start(db)
    pause_clock(
        db,
        scope=SCOPE,
        command=PauseClock(
            clock_id=clock.id,
            reason=PauseReason.OUTSIDE_BUSINESS_HOURS,
            paused_at=NOW + timedelta(hours=1),
            actor_reference="scheduler",
            note="desk closed 18:00",
        ),
    )
    row = db.scalars(select(ResponseClockPause)).one()
    assert row.reason is PauseReason.OUTSIDE_BUSINESS_HOURS
    assert row.resumed_at is None
    assert row.note == "desk closed 18:00"


def test_a_paused_clock_is_never_swept(db: Session) -> None:
    """Sweeping one would breach a customer for a night the desk was closed."""
    policy = _policy(db)
    _target(db, policy, seconds=HOUR)
    clock = _start(db)
    pause_clock(
        db,
        scope=SCOPE,
        command=PauseClock(
            clock_id=clock.id,
            reason=PauseReason.OUTSIDE_BUSINESS_HOURS,
            paused_at=NOW + timedelta(minutes=10),
        ),
    )
    result = sweep_due_clocks(
        db,
        scope=SCOPE,
        command=SweepDueClocks(observed_at=NOW + timedelta(days=1)),
    )
    assert result == result.__class__((), (), ())
    assert not _events(db)


def test_a_clock_cannot_be_paused_twice(db: Session) -> None:
    policy = _policy(db)
    _target(db, policy)
    clock = _start(db)
    command = PauseClock(
        clock_id=clock.id,
        reason=PauseReason.WAITING_ON_CUSTOMER,
        paused_at=NOW + timedelta(minutes=5),
    )
    pause_clock(db, scope=SCOPE, command=command)
    with pytest.raises(Conflict, match="already paused"):
        pause_clock(db, scope=SCOPE, command=command)


# ── Thresholds ──────────────────────────────────────────────────────────────


def test_the_sweep_warns_before_it_breaches(db: Session) -> None:
    policy = _policy(db)
    _target(db, policy, seconds=4 * HOUR, warning=1800)
    clock = _start(db)

    warned = sweep_due_clocks(
        db,
        scope=SCOPE,
        command=SweepDueClocks(observed_at=NOW + timedelta(hours=3, minutes=40)),
    )
    assert warned.warned == (clock.id,) and not warned.breached
    assert _events(db) == ["response_obligations.obligation_at_risk.v1"]
    (request,) = warned.escalation_requests
    assert request.observation is ObservationKind.WARNING
    assert request.subject_reference == "conv-1"
    assert request.severity == "NORMAL"

    breached = sweep_due_clocks(
        db,
        scope=SCOPE,
        command=SweepDueClocks(observed_at=NOW + timedelta(hours=5)),
    )
    assert breached.breached == (clock.id,)
    assert breached.escalation_requests[0].severity == "HIGH"
    assert "response_obligations.obligation_breached.v1" in _events(db)


def test_re_running_the_sweep_does_not_escalate_twice(db: Session) -> None:
    policy = _policy(db)
    _target(db, policy, seconds=HOUR)
    _start(db)
    late = NOW + timedelta(hours=2)
    first = sweep_due_clocks(db, scope=SCOPE, command=SweepDueClocks(observed_at=late))
    second = sweep_due_clocks(db, scope=SCOPE, command=SweepDueClocks(observed_at=late))
    assert first.breached and not second.breached
    assert len(db.scalars(select(ResponseObservation)).all()) == 1


def test_a_resume_earns_a_fresh_breach_against_the_new_deadline(
    db: Session,
) -> None:
    """The dedup key carries the deadline it was measured against, so moving
    the deadline legitimately allows a new observation — while a re-run against
    the same deadline does not."""
    policy = _policy(db)
    _target(db, policy, seconds=HOUR)
    clock = _start(db)
    sweep_due_clocks(
        db, scope=SCOPE, command=SweepDueClocks(observed_at=NOW + timedelta(hours=2))
    )
    pause_clock(
        db,
        scope=SCOPE,
        command=PauseClock(
            clock_id=clock.id,
            reason=PauseReason.WAITING_ON_CUSTOMER,
            paused_at=NOW + timedelta(hours=2),
        ),
    )
    resume_clock(
        db,
        scope=SCOPE,
        command=ResumeClock(clock_id=clock.id, resumed_at=NOW + timedelta(hours=3)),
    )
    again = sweep_due_clocks(
        db, scope=SCOPE, command=SweepDueClocks(observed_at=NOW + timedelta(hours=4))
    )
    assert again.breached == (clock.id,)
    assert len(db.scalars(select(ResponseObservation)).all()) == 2


def test_a_breached_clock_keeps_running_because_the_answer_is_still_owed(
    db: Session,
) -> None:
    policy = _policy(db)
    _target(db, policy, seconds=HOUR)
    clock = _start(db)
    sweep_due_clocks(
        db, scope=SCOPE, command=SweepDueClocks(observed_at=NOW + timedelta(hours=2))
    )
    assert clock.status is ClockStatus.RUNNING
    assert _at(clock.breached_at) == _at(clock.due_at)


# ── Settlement ──────────────────────────────────────────────────────────────


def test_answering_in_time_meets_the_promise(db: Session) -> None:
    policy = _policy(db)
    _target(db, policy, seconds=4 * HOUR)
    clock = _start(db)
    complete_clock(
        db,
        scope=SCOPE,
        command=CompleteClock(clock_id=clock.id, completed_at=NOW + timedelta(hours=1)),
    )
    assert clock.status is ClockStatus.MET
    assert clock.breached_at is None


def test_answering_late_does_not_erase_the_breach(db: Session) -> None:
    """ "We answered eventually" and "we answered in time" are different facts,
    and only one of them is the promise."""
    policy = _policy(db)
    _target(db, policy, seconds=HOUR)
    clock = _start(db)
    complete_clock(
        db,
        scope=SCOPE,
        command=CompleteClock(clock_id=clock.id, completed_at=NOW + timedelta(hours=5)),
    )
    assert clock.status is ClockStatus.BREACHED
    assert _at(clock.breached_at) == NOW + timedelta(hours=1)
    assert _at(clock.settled_at) == NOW + timedelta(hours=5)


def test_cancelling_is_neither_kept_nor_missed(db: Session) -> None:
    policy = _policy(db)
    _target(db, policy, seconds=HOUR)
    clock = _start(db)
    cancel_clock(
        db,
        scope=SCOPE,
        command=CancelClock(
            clock_id=clock.id,
            cancelled_at=NOW + timedelta(minutes=5),
            reason="conversation merged into conv-2",
        ),
    )
    assert clock.status is ClockStatus.CANCELLED
    assert clock.breached_at is None
    assert clock.settlement_reason == "conversation merged into conv-2"


def test_a_settled_clock_cannot_be_changed_again(db: Session) -> None:
    policy = _policy(db)
    _target(db, policy, seconds=HOUR)
    clock = _start(db)
    complete_clock(
        db,
        scope=SCOPE,
        command=CompleteClock(clock_id=clock.id, completed_at=NOW),
    )
    with pytest.raises(Conflict, match="running or paused"):
        pause_clock(
            db,
            scope=SCOPE,
            command=PauseClock(
                clock_id=clock.id,
                reason=PauseReason.WAITING_ON_CUSTOMER,
                paused_at=NOW + timedelta(minutes=1),
            ),
        )


def test_settling_frees_the_subject_for_a_new_clock_of_the_same_kind(
    db: Session,
) -> None:
    policy = _policy(db)
    _target(db, policy, seconds=HOUR)
    first = _start(db, key="first")
    complete_clock(
        db, scope=SCOPE, command=CompleteClock(clock_id=first.id, completed_at=NOW)
    )
    second = _start(db, at=NOW + timedelta(hours=1), key="second")
    assert second.id != first.id
