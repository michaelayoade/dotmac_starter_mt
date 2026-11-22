"""GAP-4: the per-account access hold, the due and hold wakes and the R10 holds.

SQLite proves the decisions and idempotency; the lock and sequence behaviour is
proved on PostgreSQL in ``tests/test_collections_holds_pg.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from dotmac_collections.holds import (
    CollectionSubjectStateV1,
    HoldLifted,
    HoldNotLive,
    HoldPlaced,
    LiftCollectionHoldV1,
    PlaceCollectionHoldV1,
    RepublishSubjectStateV1,
    decide_subject_access,
)
from dotmac_collections.models import (
    CollectionCase,
    CollectionNoticeRequest,
    CollectionStepAttempt,
    CollectionSubjectAccess,
)
from dotmac_collections.receivables import (
    FakeReceivablesReader,
    PositionReadOk,
    ReceivableObservationV1,
)
from dotmac_collections.service import (
    AccessHoldEvaluated,
    AssessmentBlocked,
    CaseAssessed,
    CaseLifecycleCommandV1,
    CollectionCaseService,
    CollectionHoldService,
    CollectionsConflict,
    ProcessCollectionAccessHoldDueV1,
    ProcessCollectionStepDueV1,
    StepDueBlocked,
    get_collection_case,
    get_collection_subject_state,
    republish_subject_state,
)
from dotmac_collections.timers import (
    ACCESS_HOLD_DUE_EVENT_TYPE,
    EXPOSURE_DUE_EVENT_TYPE,
    FakeCollectionsTimer,
    TimerIdentityV1,
    TimerTriggerV1,
)
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.money import Money
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.unit import test_collections_service_contract as base

NOW = base.NOW
SCOPE = base.SCOPE
SUBJECT = "billing-account:00000000-0000-0000-0000-000000000001"
OTHER_SUBJECT = "billing-account:00000000-0000-0000-0000-000000000002"
DUE_AFTER = timedelta(days=10)
GRACE = timedelta(days=60)
ACCESS_EVENT = "collections.subject.access_hold.v1"

db = base.db


def _policy(db: Session) -> UUID:
    return base._policy(db, request_kind="notice")


def _observation(
    *,
    ref: str = "invoice:inv-1",
    subject: str = SUBJECT,
    version: int = 1,
    amount: str = "100.00",
    state: str = "open",
    due_at: datetime | None = None,
    hold_from: datetime | None = None,
    verified: bool = True,
) -> ReceivableObservationV1:
    due = due_at or NOW + DUE_AFTER
    return ReceivableObservationV1(
        scope=SCOPE,
        source_owner="billing.receivables",
        exposure_ref=ref,
        source_version=version,
        state_fingerprint=f"sha256:{ref}-v{version}-{amount}",
        subject_ref=subject,
        service_ref=None,
        collection_timing="arrears",
        reason_code="invoice_overdue",
        collectible_receivable=Money.of(amount, base.NGN),
        service_period_status="not_applicable",
        service_period_starts_at=None,
        service_period_ends_at=None,
        due_at=due if verified else None,
        due_date_status="verified" if verified else "unknown_unverified",
        financial_state=state,  # type: ignore[arg-type]
        source_authority="internal",
        projection_mode="authoritative",
        completeness="complete",
        completeness_reason_code=None,
        observed_at=NOW,
        access_hold_from=hold_from,
    )


def _command(observation: ReceivableObservationV1, key: str | None = None):
    command = base._command(key=key)
    return replace(
        command,
        exposure_ref=observation.exposure_ref,
        subject_ref=observation.subject_ref,
        service_ref=None,
    )


def _reader(*observations: ReceivableObservationV1) -> FakeReceivablesReader:
    reader = FakeReceivablesReader()
    for observation in observations:
        reader.set_result(
            scope=SCOPE,
            source_owner=observation.source_owner,
            exposure_ref=observation.exposure_ref,
            result=PositionReadOk(observation),
        )
    return reader


def _assess(db, policy, observation, timer, at, key=None):
    return CollectionCaseService.assess(
        db,
        command=_command(observation, key),
        policy_version_id=policy,
        reader=_reader(observation),
        timer=timer,
        assessed_at=at,
    )


def _events(db: Session, active: bool | None = None) -> list[OutboxEvent]:
    rows = [
        row
        for row in db.scalars(select(OutboxEvent)).all()
        if row.event_type == ACCESS_EVENT
    ]
    if active is None:
        return rows
    return [row for row in rows if row.payload["active"] is active]


def _handle(timer: FakeCollectionsTimer, db: Session, purpose: str, entity: str):
    identity = next(
        i for i in timer.identities if i.purpose == purpose and i.entity_id == entity
    )
    return timer.current(db, identity)


def _trigger(handle) -> TimerTriggerV1:
    return TimerTriggerV1(
        timer_id=handle.timer_id,
        identity=handle.identity,
        generation=handle.generation,
        due_at=handle.due_at,
        output_event_type=handle.output_event_type,
        expected_source_version=handle.expected_source_version,
    )


def _hold_wake(db, timer, case_id, observation, at):
    handle = _handle(timer, db, "access_hold", str(case_id))
    assert handle is not None
    return CollectionCaseService.process_access_hold_due(
        db,
        command=ProcessCollectionAccessHoldDueV1(
            scope=SCOPE, trigger=_trigger(handle), processed_at=at
        ),
        reader=_reader(observation),
        timer=timer,
    )


def _place(db, ref="dispute:d-1", subject=SUBJECT, key=None, at=NOW, **extra):
    key = key or f"place:{uuid4()}"
    return CollectionHoldService.place(
        db,
        PlaceCollectionHoldV1(
            command_id=uuid5(NAMESPACE_URL, key),
            idempotency_key=key,
            scope=SCOPE,
            subject_ref=subject,
            external_ref=ref,
            reason_code="dispute_open",
            evidence_ref="evidence:1",
            actor_ref="staff:1",
            placed_at=at,
            **extra,
        ),
    )


def _lift(db, ref="dispute:d-1", subject=SUBJECT, key=None, at=NOW, timer=None):
    key = key or f"lift:{uuid4()}"
    timer = timer or FakeCollectionsTimer()
    return CollectionHoldService.lift(
        db,
        LiftCollectionHoldV1(
            command_id=uuid5(NAMESPACE_URL, key),
            idempotency_key=key,
            scope=SCOPE,
            subject_ref=subject,
            external_ref=ref,
            reason_code="dispute_closed",
            evidence_ref="evidence:2",
            actor_ref="staff:1",
            lifted_at=at,
        ),
        timer,
    )


def _held_case(db, timer, policy):
    """An open case whose hold instant has been reached: the subject is held."""

    due = NOW + DUE_AFTER
    hold_from = due + GRACE
    obs = _observation(due_at=due, hold_from=hold_from)
    opened = _assess(db, policy, obs, timer, due)
    assert isinstance(opened, CaseAssessed)
    result = _hold_wake(db, timer, opened.case_id, obs, hold_from)
    assert isinstance(result, AccessHoldEvaluated)
    assert result.subject_state == "held"
    return opened, obs, hold_from


# --- pure decision and contracts -------------------------------------------


@pytest.mark.parametrize(
    ("demanded", "suppressed", "expected"),
    [
        (False, False, "clear"),
        (False, True, "clear"),
        (True, False, "held"),
        (True, True, "suppressed"),
    ],
)
def test_subject_access_decision_table(demanded, suppressed, expected) -> None:
    assert decide_subject_access(demanded=demanded, suppressed=suppressed) == expected


@pytest.mark.parametrize(
    "external_ref", ["", "nokind", ":id", "kind:", "k:" + "x" * 255]
)
def test_hold_contract_refuses_a_malformed_external_ref(external_ref) -> None:
    with pytest.raises(ValueError):
        PlaceCollectionHoldV1(
            command_id=uuid4(),
            idempotency_key="k",
            scope=SCOPE,
            subject_ref=SUBJECT,
            external_ref=external_ref,
            reason_code="r",
            evidence_ref="e",
            actor_ref="a",
            placed_at=NOW,
        )


def test_hold_contract_requires_aware_instants_and_text() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PlaceCollectionHoldV1(
            uuid4(), "k", SCOPE, SUBJECT, "a:b", "r", "e", "a", NOW.replace(tzinfo=None)
        )
    with pytest.raises(ValueError, match="approval_ref"):
        LiftCollectionHoldV1(
            uuid4(), "k", SCOPE, SUBJECT, "a:b", "r", "e", "a", NOW, approval_ref=" "
        )


def test_observation_access_hold_from_needs_a_verified_due_and_not_before_it() -> None:
    due = NOW + DUE_AFTER
    assert _observation(due_at=due, hold_from=due).access_hold_from == due
    with pytest.raises(ValueError, match="cannot precede"):
        _observation(due_at=due, hold_from=due - timedelta(seconds=1))
    with pytest.raises(ValueError, match="verified due date"):
        _observation(verified=False, hold_from=due)
    with pytest.raises(ValueError, match="timezone-aware"):
        _observation(due_at=due, hold_from=due.replace(tzinfo=None))


# --- wakes -----------------------------------------------------------------


def test_pre_due_assess_arms_one_exposure_wake_and_a_replay_arms_no_second(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    obs = _observation()
    command = _command(obs, key="pre-due")
    for _ in range(2):
        result = CollectionCaseService.assess(
            db,
            command=command,
            policy_version_id=policy,
            reader=_reader(obs),
            timer=timer,
            assessed_at=NOW,
        )
        assert result == AssessmentBlocked("receivable_not_due", None)
    # A new command key for the same evidence leaves the identical wake alone.
    _assess(db, policy, obs, timer, NOW + timedelta(hours=1))
    identity = TimerIdentityV1(
        SCOPE,
        "collections.exposure",
        "exposure",
        "billing.receivables:invoice:inv-1",
        "due",
    )
    handle = timer.current(db, identity)
    assert handle is not None
    assert handle.generation == 1
    assert handle.due_at == obs.due_at
    assert handle.output_event_type == EXPOSURE_DUE_EVENT_TYPE
    assert db.scalars(select(CollectionCase)).all() == []


def test_due_wake_opens_the_case_and_arms_the_hold_wake_with_no_event(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    obs = _observation(due_at=due, hold_from=due + GRACE)
    opened = _assess(db, policy, obs, timer, due)
    assert isinstance(opened, CaseAssessed)
    row = db.scalars(select(CollectionCase)).one()
    assert row.access_hold_from is not None
    handle = _handle(timer, db, "access_hold", str(opened.case_id))
    assert handle is not None
    assert handle.due_at == due + GRACE
    assert handle.output_event_type == ACCESS_HOLD_DUE_EVENT_TYPE
    assert _events(db) == []
    assert get_collection_subject_state(
        db, scope=SCOPE, subject_ref=SUBJECT
    ) == CollectionSubjectStateV1(SUBJECT, "clear", None, (), None)


def test_hold_wake_holds_with_exactly_one_event_and_a_replay_emits_none(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    opened, obs, hold_from = _held_case(db, timer, policy)
    events = _events(db)
    assert len(events) == 1
    payload = events[0].payload
    assert payload["active"] is True
    assert payload["subject_ref"] == SUBJECT
    assert payload["source_version"] == 1
    assert payload["source_reference"] == f"collections:subject-access:{SUBJECT}:1"
    assert payload["policy_version"] == "collections.subject_access.v1"
    state = get_collection_subject_state(db, scope=SCOPE, subject_ref=SUBJECT)
    assert (state.state, state.version) == ("held", 1)

    # The same wake again replays from the ledger: nothing is emitted.
    replay = CollectionCaseService.process_access_hold_due(
        db,
        command=ProcessCollectionAccessHoldDueV1(
            scope=SCOPE,
            trigger=_trigger(_handle_any(timer, "access_hold", str(opened.case_id))),
            processed_at=hold_from,
        ),
        reader=_reader(obs),
        timer=timer,
    )
    assert isinstance(replay, AccessHoldEvaluated) and replay.replayed
    assert len(_events(db)) == 1


def _handle_any(timer, purpose, entity):
    """The latest handle for an identity, even after it fired."""

    identity = next(
        i for i in timer.identities if i.purpose == purpose and i.entity_id == entity
    )
    return timer._latest[identity]


def test_two_cases_of_one_subject_hold_until_both_resolve(db: Session) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    hold_from = due + GRACE
    first = _observation(ref="invoice:a", due_at=due, hold_from=hold_from)
    second = _observation(ref="invoice:b", due_at=due, hold_from=hold_from)
    a = _assess(db, policy, first, timer, due)
    b = _assess(db, policy, second, timer, due)
    assert isinstance(a, CaseAssessed) and isinstance(b, CaseAssessed)
    reader = _reader(first, second)
    for case in (a, b):
        CollectionCaseService.process_access_hold_due(
            db,
            command=ProcessCollectionAccessHoldDueV1(
                scope=SCOPE,
                trigger=_trigger(_handle(timer, db, "access_hold", str(case.case_id))),
                processed_at=hold_from,
            ),
            reader=reader,
            timer=timer,
        )
    assert len(_events(db, True)) == 1
    resolved_a = _observation(
        ref="invoice:a",
        version=2,
        amount="0.00",
        state="resolved",
        due_at=due,
        hold_from=hold_from,
    )
    _assess(db, policy, resolved_a, timer, hold_from + timedelta(days=1))
    assert _events(db, False) == []
    assert (
        get_collection_subject_state(db, scope=SCOPE, subject_ref=SUBJECT).state
        == "held"
    )
    resolved_b = _observation(
        ref="invoice:b",
        version=2,
        amount="0.00",
        state="resolved",
        due_at=due,
        hold_from=hold_from,
    )
    _assess(db, policy, resolved_b, timer, hold_from + timedelta(days=2))
    assert len(_events(db, False)) == 1
    assert (
        get_collection_subject_state(db, scope=SCOPE, subject_ref=SUBJECT).state
        == "clear"
    )


def test_payment_after_the_hold_lifts_it_and_a_reversal_holds_once_engaged(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    opened, obs, hold_from = _held_case(db, timer, policy)
    later = hold_from + timedelta(days=1)
    paid = _observation(
        version=2,
        amount="0.00",
        state="resolved",
        due_at=obs.due_at,
        hold_from=hold_from,
    )
    _assess(db, policy, paid, timer, later)
    assert len(_events(db, False)) == 1
    # A reversal reopens the invoice: a new case whose hold instant is in the past.
    # It is not held until its wake engages the hold (after the balance pass).
    reopened = _observation(
        version=3, due_at=obs.due_at, hold_from=hold_from, amount="100.00"
    )
    again = later + timedelta(days=1)
    result = _assess(db, policy, reopened, timer, again)
    assert isinstance(result, CaseAssessed) and result.opened
    assert len(_events(db, True)) == 1
    engaged = _hold_wake(db, timer, result.case_id, reopened, again)
    assert engaged.subject_state == "held"
    assert len(_events(db, True)) == 2
    state = get_collection_subject_state(db, scope=SCOPE, subject_ref=SUBJECT)
    assert state.state == "held"
    assert state.version == 3


def test_pause_clears_the_hold_and_resume_restores_it(db: Session) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    opened, _, hold_from = _held_case(db, timer, policy)

    def move(to_state: str, at: datetime) -> None:
        CollectionCaseService.transition(
            db,
            command=CaseLifecycleCommandV1(
                command_id=uuid4(),
                idempotency_key=f"move:{to_state}:{at}",
                scope=SCOPE,
                case_id=opened.case_id,
                actor_ref="staff:1",
                reason_code="staff_decision",
                occurred_at=at,
            ),
            to_state=to_state,  # type: ignore[arg-type]
            timer=timer,
        )

    move("paused", hold_from + timedelta(hours=1))
    assert len(_events(db, False)) == 1
    resumed = hold_from + timedelta(hours=2)
    move("active", resumed)
    # Resuming disengages the hold; the wake (after the balance pass) re-engages it.
    assert len(_events(db, True)) == 1
    _hold_wake(db, timer, opened.case_id, _observation(hold_from=hold_from), resumed)
    assert len(_events(db, True)) == 2


def test_no_hold_instant_never_holds_and_the_hold_still_suppresses_notices(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    obs = _observation(due_at=due, hold_from=None)
    opened = _assess(db, policy, obs, timer, due)
    assert isinstance(opened, CaseAssessed)
    assert [i for i in timer.identities if i.purpose == "access_hold"] == []
    _place(db, at=due)
    step = _handle(timer, db, "next_step", str(opened.case_id))
    assert step is not None
    result = CollectionCaseService.process_step_due(
        db,
        command=ProcessCollectionStepDueV1(
            scope=SCOPE, trigger=_trigger(step), processed_at=due
        ),
        reader=_reader(obs),
        timer=timer,
    )
    assert result == StepDueBlocked(
        opened.case_id, "collection_hold_active", None, False
    )
    assert db.scalars(select(CollectionNoticeRequest)).all() == []
    assert _events(db) == []


# --- R10 holds -------------------------------------------------------------


def test_a_hold_placed_while_held_restores_service_and_suppresses_the_ladder(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    opened, obs, hold_from = _held_case(db, timer, policy)
    placed = _place(db, at=hold_from + timedelta(hours=1))
    assert isinstance(placed, HoldPlaced)
    assert len(_events(db, False)) == 1
    state = get_collection_subject_state(db, scope=SCOPE, subject_ref=SUBJECT)
    assert (state.state, state.live_external_refs) == ("suppressed", ("dispute:d-1",))

    step = _handle(timer, db, "next_step", str(opened.case_id))
    assert step is not None
    blocked = CollectionCaseService.process_step_due(
        db,
        command=ProcessCollectionStepDueV1(
            scope=SCOPE,
            trigger=_trigger(step),
            processed_at=hold_from + timedelta(hours=2),
        ),
        reader=_reader(obs),
        timer=timer,
    )
    assert isinstance(blocked, StepDueBlocked)
    assert blocked.reason_code == "collection_hold_active"
    assert db.scalars(select(CollectionNoticeRequest)).all() == []
    assert _handle(timer, db, "next_step", str(opened.case_id)) is None


def test_lift_re_arms_the_ladder_and_holds_again_when_still_past_the_instant(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    opened, obs, hold_from = _held_case(db, timer, policy)
    _place(db, at=hold_from + timedelta(hours=1))
    step = _handle(timer, db, "next_step", str(opened.case_id))
    CollectionCaseService.process_step_due(
        db,
        command=ProcessCollectionStepDueV1(
            scope=SCOPE,
            trigger=_trigger(step),
            processed_at=hold_from + timedelta(hours=2),
        ),
        reader=_reader(obs),
        timer=timer,
    )
    lifted = _lift(db, at=hold_from + timedelta(hours=3), timer=timer)
    assert isinstance(lifted, HoldLifted)
    # The step was long past due when the hold lifted: skipped, never re-sent.
    assert _handle(timer, db, "next_step", str(opened.case_id)) is None
    assert [a.request_kind for a in db.scalars(select(CollectionStepAttempt))] == [
        "skipped"
    ]
    assert len(_events(db, True)) == 2
    assert (
        get_collection_subject_state(db, scope=SCOPE, subject_ref=SUBJECT).state
        == "held"
    )


def test_two_holds_one_lift_stays_suppressed(db: Session) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    _, _, hold_from = _held_case(db, timer, policy)
    _place(db, ref="dispute:one", at=hold_from)
    _place(db, ref="payment_arrangement:two", at=hold_from)
    _lift(db, ref="dispute:one", at=hold_from + timedelta(hours=1), timer=timer)
    state = get_collection_subject_state(db, scope=SCOPE, subject_ref=SUBJECT)
    assert state.state == "suppressed"
    assert state.live_external_refs == ("payment_arrangement:two",)
    assert len(_events(db, True)) == 1
    _lift(
        db,
        ref="payment_arrangement:two",
        at=hold_from + timedelta(hours=2),
        timer=timer,
    )
    assert len(_events(db, True)) == 2


def test_place_and_lift_are_idempotent_by_external_ref(db: Session) -> None:
    first = _place(db, key="k1")
    again = _place(db, key="k1")
    assert isinstance(first, HoldPlaced) and not first.replayed
    assert again.replayed and again.hold_id == first.hold_id
    other_key = _place(db, key="k2")
    assert other_key.hold_id == first.hold_id and other_key.replayed

    with pytest.raises(CollectionsConflict) as conflict:
        _place(db, subject=OTHER_SUBJECT, key="k3")
    assert conflict.value.code == "hold_subject_conflict"

    lifted = _lift(db, key="l1")
    replay = _lift(db, key="l1")
    assert isinstance(lifted, HoldLifted) and replay == replace(lifted, replayed=True)
    gone = _lift(db, key="l2")
    assert gone == HoldNotLive("dispute:d-1", False)

    # After a lift the same reference can be placed again, even on another subject.
    replaced = _place(db, subject=OTHER_SUBJECT, key="k4")
    assert replaced.hold_id != first.hold_id and not replaced.replayed


def test_approval_ref_is_stored_and_never_required(db: Session) -> None:
    placed = _place(db, approval_ref="approval:1")
    assert isinstance(placed, HoldPlaced)
    assert _place(db, ref="dispute:d-2").replayed is False


def test_subject_versions_rise_across_subjects(db: Session) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    hold_from = due + GRACE
    versions = []
    for ref, subject in (("invoice:a", SUBJECT), ("invoice:b", OTHER_SUBJECT)):
        obs = _observation(ref=ref, subject=subject, due_at=due, hold_from=hold_from)
        opened = _assess(db, policy, obs, timer, due)
        _hold_wake(db, timer, opened.case_id, obs, hold_from)
        versions.append(
            get_collection_subject_state(db, scope=SCOPE, subject_ref=subject).version
        )
    assert versions == [1, 2]
    assert len(db.scalars(select(CollectionSubjectAccess)).all()) == 2


# --- lock order ------------------------------------------------------------


def test_every_command_takes_the_case_lock_before_the_subject_lock(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dotmac_collections import service

    class Spy:
        def __init__(self) -> None:
            self.calls: list[str] = []

    spy = Spy()
    original = service._serialize_identity

    def record(session, scope, *parts):
        spy.calls.append(str(parts[0]))
        return original(session, scope, *parts)

    monkeypatch.setattr(service, "_serialize_identity", record)
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    _, _, hold_from = _held_case(db, timer, policy)

    def one_command(run) -> list[str]:
        spy.calls.clear()
        run()
        return list(spy.calls)

    placed = one_command(lambda: _place(db, at=hold_from))
    lifted = one_command(
        lambda: _lift(db, at=hold_from + timedelta(hours=1), timer=timer)
    )
    assert placed == ["hold", "subject"]
    # The case lock of every active case precedes the subject lock.
    assert lifted[0] == "hold"
    assert lifted.index("case") < lifted.index("subject")
    for calls in (placed, lifted):
        assert "case" not in calls[calls.index("subject") :]


def test_platform_plane_places_and_lifts_holds(platform_db: Session) -> None:
    from dotmac_collections.models import PlatformCollectionHold
    from dotmac_kernel.cache import PlatformScope

    scope = PlatformScope()
    command = PlaceCollectionHoldV1(
        command_id=uuid4(),
        idempotency_key="platform-place",
        scope=scope,
        subject_ref="vendor:v-1",
        external_ref="dispute:p-1",
        reason_code="dispute_open",
        evidence_ref="evidence:1",
        actor_ref="staff:1",
        placed_at=NOW,
    )
    assert isinstance(CollectionHoldService.place(platform_db, command), HoldPlaced)
    state = get_collection_subject_state(
        platform_db, scope=scope, subject_ref="vendor:v-1"
    )
    assert state.live_external_refs == ("dispute:p-1",)
    lifted = CollectionHoldService.lift(
        platform_db,
        LiftCollectionHoldV1(
            uuid4(),
            "platform-lift",
            scope,
            "vendor:v-1",
            "dispute:p-1",
            "closed",
            "evidence:2",
            "staff:1",
            NOW,
        ),
        FakeCollectionsTimer(),
    )
    assert isinstance(lifted, HoldLifted)
    assert len(platform_db.scalars(select(PlatformCollectionHold)).all()) == 1
    assert (
        get_collection_subject_state(
            platform_db, scope=scope, subject_ref="vendor:v-1"
        ).live_external_refs
        == ()
    )


platform_db = base.platform_db


# --- engagement, republish, case read, wake retirement, due anchor ----------


def test_a_case_opened_past_its_hold_instant_is_not_held_until_the_wake_engages(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    hold_from = due + timedelta(days=3)
    late = hold_from + timedelta(days=10)
    obs = _observation(due_at=due, hold_from=hold_from)
    opened = _assess(db, policy, obs, timer, late)
    assert isinstance(opened, CaseAssessed)
    # Past the instant but not engaged: no event, and the wake is due now.
    assert _events(db) == []
    handle = _handle(timer, db, "access_hold", str(opened.case_id))
    assert handle is not None and handle.due_at == late
    view = get_collection_case(db, scope=SCOPE, case_id=opened.case_id)
    assert view.access_hold_engaged_at is None
    assert view.access_hold_from == hold_from

    # The balance pass ran in between and paid the invoice: the wake finds it closed.
    paid = _observation(
        version=2, amount="0.00", state="resolved", due_at=due, hold_from=hold_from
    )
    closed = _hold_wake(db, timer, opened.case_id, paid, late)
    assert (closed.lifecycle, closed.subject_state) == ("resolved", "clear")
    assert _events(db) == []


def test_the_wake_engages_the_hold_when_the_invoice_is_still_open(db: Session) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    hold_from = due + timedelta(days=3)
    late = hold_from + timedelta(days=10)
    obs = _observation(due_at=due, hold_from=hold_from)
    opened = _assess(db, policy, obs, timer, late)
    engaged = _hold_wake(db, timer, opened.case_id, obs, late)
    assert engaged.subject_state == "held"
    assert len(_events(db, True)) == 1
    view = get_collection_case(db, scope=SCOPE, case_id=opened.case_id)
    assert view.access_hold_engaged_at == late


def test_a_later_hold_instant_disengages_and_other_commands_never_engage(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    opened, obs, hold_from = _held_case(db, timer, policy)
    # Billing now reports a later hold instant (terms were recorded differently).
    moved = replace(obs, source_version=2, access_hold_from=hold_from + GRACE)
    moved = replace(moved, state_fingerprint="sha256:moved")
    _assess(db, policy, moved, timer, hold_from + timedelta(hours=1))
    assert len(_events(db, False)) == 1
    view = get_collection_case(db, scope=SCOPE, case_id=opened.case_id)
    assert view.access_hold_engaged_at is None
    handle = _handle(timer, db, "access_hold", str(opened.case_id))
    assert handle is not None and handle.due_at == hold_from + GRACE


def test_republish_allocates_the_next_version_and_is_idempotent(db: Session) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    _held_case(db, timer, policy)
    other = _observation(ref="invoice:b", subject=OTHER_SUBJECT)
    del other  # a second subject only moves the shared sequence below

    def republish(subject: str, key: str):
        return republish_subject_state(
            db,
            RepublishSubjectStateV1(
                command_id=uuid5(NAMESPACE_URL, key),
                idempotency_key=key,
                scope=SCOPE,
                subject_ref=subject,
                requested_at=NOW + timedelta(days=100),
            ),
        )

    first = republish(SUBJECT, "r1")
    assert (first.state, first.version) == ("held", 2)
    assert republish(SUBJECT, "r1") == replace(first, replayed=True)
    never = republish(OTHER_SUBJECT, "r2")
    assert (never.state, never.version) == ("clear", 3)
    events = sorted(_events(db), key=lambda e: e.payload["source_version"])
    assert [(e.payload["subject_ref"], e.payload["active"]) for e in events] == [
        (SUBJECT, True),
        (SUBJECT, True),
        (OTHER_SUBJECT, False),
    ]
    assert [e.payload["source_version"] for e in events] == [1, 2, 3]


def test_the_case_read_is_read_only_and_refuses_an_unknown_case(db: Session) -> None:
    from dotmac_collections.service import CollectionsNotFound

    with pytest.raises(CollectionsNotFound):
        get_collection_case(db, scope=SCOPE, case_id=uuid4())


def test_the_exposure_wake_is_accepted_and_retired_once_actionable(
    db: Session,
) -> None:
    policy = _policy(db)
    timer = FakeCollectionsTimer()
    obs = _observation()
    _assess(db, policy, obs, timer, NOW)
    identity = TimerIdentityV1(
        SCOPE,
        "collections.exposure",
        "exposure",
        "billing.receivables:invoice:inv-1",
        "due",
    )
    handle = timer.current(db, identity)
    assert handle is not None
    due = obs.due_at
    actionable = replace(obs, source_version=2, state_fingerprint="sha256:v2")
    result = CollectionCaseService.assess(
        db,
        command=_command(actionable),
        policy_version_id=policy,
        reader=_reader(actionable),
        timer=timer,
        assessed_at=due,
        exposure_wake=_trigger(handle),
    )
    assert isinstance(result, CaseAssessed) and result.opened
    assert timer.current(db, identity) is None  # fired and consumed, none scheduled
    with pytest.raises(ValueError, match="due wake"):
        CollectionCaseService.assess(
            db,
            command=_command(replace(obs, exposure_ref="invoice:other")),
            policy_version_id=policy,
            reader=_reader(actionable),
            timer=timer,
            assessed_at=due,
            exposure_wake=_trigger(handle),
        )


def _ladder(
    db: Session,
    steps: tuple[tuple[str, int, int, str, bool], ...] = (
        ("notice_7", 1, 7, "exposure_at", False),
        ("notice_30", 2, 30, "exposure_at", False),
    ),
) -> UUID:
    """Notices as (code, ordinal, days, anchor, receipt); default due+7 and due+30."""

    from dotmac_collections.policies import (
        PolicyPublicationV1,
        PolicyStepDraftV1,
        PolicyVersionDraftV1,
    )
    from dotmac_collections.service import (
        CollectionPolicyService,
        CreateCollectionPolicyV1,
    )

    CollectionPolicyService.create(
        db,
        CreateCollectionPolicyV1(
            policy_id=uuid4(),
            scope=SCOPE,
            policy_code="standard_arrears",
            description="Ladder",
        ),
    )
    version_id = uuid4()
    CollectionPolicyService.publish(
        db,
        scope=SCOPE,
        draft=PolicyVersionDraftV1(
            policy_code="standard_arrears",
            reason_code="invoice_overdue",
            collection_timing="arrears",
            grace=None,
            steps=tuple(
                PolicyStepDraftV1(
                    code=code,
                    ordinal=ordinal,
                    offset=timedelta(days=days),
                    offset_anchor=anchor,  # type: ignore[arg-type]
                    request_kind="notice",
                    action_code=None,
                    purpose_code="overdue_notice",
                    effect_scope=None,
                    receipt_required=receipt,
                    retry_offsets=(),
                )
                for code, ordinal, days, anchor, receipt in steps
            ),
        ),
        publication=PolicyPublicationV1(
            policy_version_id=version_id,
            version=1,
            effective_from=NOW,
            actor_ref="staff:1",
            reason="ladder",
            published_at=NOW,
        ),
    )
    return version_id


def _skips(db: Session) -> list[str]:
    return [
        a.policy_step_code
        for a in db.scalars(select(CollectionStepAttempt))
        if a.request_kind == "skipped"
    ]


def test_reminders_count_from_the_due_date_not_from_when_the_case_opened(
    db: Session,
) -> None:
    policy = _ladder(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    obs = _observation(due_at=due, hold_from=None)
    opened = _assess(db, policy, obs, timer, due + timedelta(days=2))
    step = _handle(timer, db, "next_step", str(opened.case_id))
    assert step is not None and step.due_at == due + timedelta(days=7)
    assert _skips(db) == []


def test_a_case_opened_after_the_30_day_notice_skips_both_and_sends_neither(
    db: Session,
) -> None:
    policy = _ladder(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    obs = _observation(due_at=due, hold_from=None)
    opened = _assess(db, policy, obs, timer, due + timedelta(days=45))
    assert isinstance(opened, CaseAssessed)
    assert [i for i in timer.identities if i.purpose == "next_step"] == []
    assert _skips(db) == ["notice_7", "notice_30"]
    assert db.scalars(select(CollectionNoticeRequest)).all() == []


def test_a_case_opened_at_due_plus_10_arms_only_the_30_day_notice(
    db: Session,
) -> None:
    policy = _ladder(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    obs = _observation(due_at=due, hold_from=None)
    opened = _assess(db, policy, obs, timer, due + timedelta(days=10))
    step = _handle(timer, db, "next_step", str(opened.case_id))
    assert step is not None and step.due_at == due + timedelta(days=30)
    assert _skips(db) == ["notice_7"]
    assert db.scalars(select(CollectionNoticeRequest)).all() == []


def test_a_resumed_case_skips_the_reminders_that_fell_due_while_paused(
    db: Session,
) -> None:
    policy = _ladder(db)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    obs = _observation(due_at=due, hold_from=None)
    opened = _assess(db, policy, obs, timer, due + timedelta(days=1))

    def move(to_state: str, at: datetime) -> None:
        CollectionCaseService.transition(
            db,
            command=CaseLifecycleCommandV1(
                command_id=uuid4(),
                idempotency_key=f"move:{to_state}",
                scope=SCOPE,
                case_id=opened.case_id,
                actor_ref="staff:1",
                reason_code="staff_decision",
                occurred_at=at,
            ),
            to_state=to_state,  # type: ignore[arg-type]
            timer=timer,
        )

    move("paused", due + timedelta(days=2))
    move("active", due + timedelta(days=12))
    step = _handle(timer, db, "next_step", str(opened.case_id))
    assert step is not None and step.due_at == due + timedelta(days=30)
    assert _skips(db) == ["notice_7"]


_CHAIN = (
    ("notice_1", 1, 7, "exposure_at", False),
    ("notice_2", 2, 23, "request_at", False),
)


def test_a_skipped_notice_still_anchors_the_notice_counted_from_its_request(
    db: Session,
) -> None:
    policy = _ladder(db, _CHAIN)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    obs = _observation(due_at=due, hold_from=None)
    opened = _assess(db, policy, obs, timer, due + timedelta(days=10))
    assert isinstance(opened, CaseAssessed)
    step = _handle(timer, db, "next_step", str(opened.case_id))
    # notice 1 (due+7) skipped; notice 2 counts from that instant: due+7+23.
    assert step is not None and step.due_at == due + timedelta(days=30)
    assert _skips(db) == ["notice_1"]
    assert db.scalars(select(CollectionNoticeRequest)).all() == []


def test_a_chained_ladder_opened_at_due_plus_40_skips_both(db: Session) -> None:
    policy = _ladder(db, _CHAIN)
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    obs = _observation(due_at=due, hold_from=None)
    opened = _assess(db, policy, obs, timer, due + timedelta(days=40))
    assert isinstance(opened, CaseAssessed)
    assert [i for i in timer.identities if i.purpose == "next_step"] == []
    assert _skips(db) == ["notice_1", "notice_2"]
    assert db.scalars(select(CollectionNoticeRequest)).all() == []


def test_a_notice_counted_from_a_receipt_stops_the_pass_without_raising(
    db: Session,
) -> None:
    policy = _ladder(
        db,
        (
            ("notice_1", 1, 7, "exposure_at", True),
            ("notice_2", 2, 23, "accepted_notice_receipt_at", False),
        ),
    )
    timer = FakeCollectionsTimer()
    due = NOW + DUE_AFTER
    obs = _observation(due_at=due, hold_from=None)
    # notice 1 is skipped; notice 2 needs a receipt a skip cannot supply: no raise.
    opened = _assess(db, policy, obs, timer, due + timedelta(days=10))
    assert isinstance(opened, CaseAssessed)
    assert _skips(db) == ["notice_1"]
    assert [i for i in timer.identities if i.purpose == "next_step"] == []
