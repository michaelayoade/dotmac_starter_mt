"""Campaign lifecycle canaries, written before the shared implementation.

These are the Sub parity properties that define the extraction: immutable
audiences, repeated consent checks, ordered nurture progression, deterministic
send windows/senders, one delivery intent, monotonic observations and repairable
projections. Product ORM and provider behavior deliberately do not appear here.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import pytest
from dotmac_campaigns import (
    AudienceBatch,
    AudienceCandidate,
    CampaignKind,
    CampaignStatus,
    ContractError,
    CreateCampaign,
    DatedCampaignOccurrence,
    DeliveryState,
    Observation,
    ObservationKind,
    RenderedMessage,
    RenderRequest,
    SequenceStep,
    SnapshotImmutable,
    UnsubscribeRequest,
    accept_due_work,
    authorize_delivery,
    campaign_snapshot,
    cancel_campaign,
    create_campaign,
    delivery_intent,
    ingest_audience,
    next_send_at,
    pause_campaign,
    purge_expired_pii,
    rebuild_counters,
    record_observation,
    report_drift,
    request_unsubscribe,
    response_facts,
    resume_campaign,
    schedule_campaign,
)
from dotmac_campaigns.fakes import FakeRenderer, FakeSenderResolver, FakeTimerPort
from dotmac_campaigns.models import (
    ALL_MODELS,
    Campaign,
    CampaignCounter,
    CampaignDeliveryIntent,
    CampaignRecipient,
    CampaignResponse,
)
from dotmac_kernel.consent import may_send, register_marketing_categories, suppress
from dotmac_kernel.consent_models import CommunicationSuppression
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

TENANT_ID = uuid.uuid4()
NOW = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={
            "schema_translate_map": {"public": None, "mod_campaigns": None}
        },
    )
    tables = (
        Tenant.__table__,
        CommunicationSuppression.__table__,
        IdempotencyRecord.__table__,
        OutboxEvent.__table__,
        *(model.__table__ for model in ALL_MODELS),
    )
    Base.metadata.create_all(engine, tables=tables)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_ID, slug="campaigns", name="Campaigns"))
        session.flush()
        register_marketing_categories("campaign")
        yield session
    engine.dispose()


def _command(*, scheduled_at: datetime = NOW) -> CreateCampaign:
    return CreateCampaign(
        code="welcome-2026",
        name="Welcome",
        kind=CampaignKind.NURTURE,
        channel="email",
        timezone="Africa/Lagos",
        scheduled_at=scheduled_at,
        send_window_start=time(8, 0),
        send_window_end=time(18, 0),
        sender_key="growth",
        steps=(
            SequenceStep(
                position=0,
                delay=timedelta(0),
                template_slug="welcome-one",
                template_channel="email",
            ),
            SequenceStep(
                position=1,
                delay=timedelta(days=2),
                template_slug="welcome-two",
                template_channel="email",
                advance_on=frozenset({DeliveryState.DELIVERED}),
            ),
        ),
        evidence_expires_at=NOW + timedelta(days=365),
        pii_expires_at=NOW + timedelta(days=90),
    )


def _create(db: Session, *, key: str = "create-1"):
    return create_campaign(
        db,
        tenant_id=TENANT_ID,
        command=_command(),
        idempotency_key=key,
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )


def _audience(address: str = "person@example.com") -> AudienceBatch:
    return AudienceBatch(
        source_owner="dotmac_sub.subscriber_marketing",
        source_version="cohort-v4",
        source_fingerprint="a" * 64,
        eligibility_reason="new_customer",
        candidates=(
            AudienceCandidate(
                source_subject_id="subscriber-42",
                channel="email",
                address=address,
                context={"first_name": "Ada"},
                eligibility_reason="new_customer",
            ),
        ),
    )


def _publish_first_intent(db: Session, *, timers: FakeTimerPort | None = None):
    campaign = _create(db)
    ingest_audience(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="audience-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )
    timer_port = timers or FakeTimerPort()
    schedule_campaign(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        timers=timer_port,
        idempotency_key="schedule-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )
    accepted = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=timer_port.only_current_trigger(),
        timers=timer_port,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="due-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=NOW,
    )
    return campaign, timer_port, accepted


def _dated(
    db: Session, *, window: tuple[time, time] | None = None
) -> tuple[Campaign, FakeTimerPort, CreateCampaign]:
    occurrence = DatedCampaignOccurrence(
        local_date=NOW.date(), local_time=time(9), timezone="Africa/Lagos"
    )
    command = occurrence.as_campaign(
        code="holiday",
        name="Holiday greeting",
        channel="email",
        template_slug="holiday",
        sender_key="growth",
        evidence_expires_at=NOW + timedelta(days=365),
        pii_expires_at=NOW + timedelta(days=30),
    )
    if window is not None:
        command = replace(
            command, send_window_start=window[0], send_window_end=window[1]
        )
    campaign = create_campaign(
        db,
        tenant_id=TENANT_ID,
        command=command,
        idempotency_key="holiday-create",
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )
    ingest_audience(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="holiday-audience",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )
    timers = FakeTimerPort()
    schedule_campaign(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        timers=timers,
        idempotency_key="holiday-schedule",
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )
    return campaign, timers, command


def test_dated_occurrence_uses_local_midnight_not_a_fixed_utc_day() -> None:
    occurrence = DatedCampaignOccurrence(
        local_date=NOW.date(), local_time=time(9), timezone="Africa/Lagos"
    )
    assert occurrence.scheduled_at == NOW
    assert occurrence.delivery_expires_at == NOW.replace(hour=23)
    assert (
        occurrence.as_campaign(
            code="greeting",
            name="Greeting",
            channel="sms",
            template_slug="greeting",
            sender_key="support",
            evidence_expires_at=NOW + timedelta(days=30),
            pii_expires_at=NOW + timedelta(days=7),
        ).kind
        is CampaignKind.ONE_TIME
    )


@pytest.mark.parametrize(
    ("day", "clock"),
    (
        (datetime(2026, 3, 8).date(), time(2, 30)),
        (datetime(2026, 11, 1).date(), time(1, 30)),
    ),
)
def test_dated_occurrence_refuses_nonexistent_or_ambiguous_local_time(
    day: date, clock: time
) -> None:
    with pytest.raises(ContractError):
        DatedCampaignOccurrence(
            local_date=day, local_time=clock, timezone="America/New_York"
        )


def test_dated_occurrence_checks_zone_and_naive_wall_clock() -> None:
    for zone, clock in (("invalid/timezone", time(9)), ("UTC", time(9, tzinfo=UTC))):
        with pytest.raises(ContractError):
            DatedCampaignOccurrence(
                local_date=NOW.date(), local_time=clock, timezone=zone
            )


def test_delivery_deadline_must_follow_schedule_and_fit_retention() -> None:
    command = _command()
    for deadline in (NOW, NOW.replace(tzinfo=None), NOW + timedelta(days=91)):
        with pytest.raises(ContractError):
            replace(command, delivery_expires_at=deadline)
    assert "delivery_expires_at" not in command.fingerprint_payload()
    bounded = replace(command, delivery_expires_at=NOW + timedelta(hours=1))
    assert bounded.fingerprint_payload() != command.fingerprint_payload()


def test_expired_due_work_never_renders_or_publishes(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    campaign, timers, command = _dated(db)
    assert command.delivery_expires_at is not None
    renderer = FakeRenderer()

    def forbidden_render(request: RenderRequest) -> RenderedMessage:
        raise AssertionError("Expired work reached the renderer")

    monkeypatch.setattr(renderer, "render", forbidden_render)
    trigger = timers.only_current_trigger()
    result = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=trigger,
        timers=timers,
        renderer=renderer,
        senders=FakeSenderResolver(),
        idempotency_key="holiday-due",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=command.delivery_expires_at,
    )
    assert result.status == "expired"
    replay = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=trigger,
        timers=timers,
        renderer=renderer,
        senders=FakeSenderResolver(),
        idempotency_key="holiday-due",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=command.delivery_expires_at,
    )
    assert replay.status == "expired" and replay.replayed
    assert db.scalars(select(OutboxEvent)).all() == []
    snapshot = campaign_snapshot(db, tenant_id=TENANT_ID, campaign_id=campaign.id)
    assert snapshot.delivery_expires_at == command.delivery_expires_at
    assert snapshot.counters.suppressed == 1
    assert len(snapshot.recipients[0].consent_receipts) == 2
    assert (
        snapshot.recipients[0].consent_receipts[-1].reason
        == "campaign_delivery_expired"
    )
    assert (
        snapshot.recipients[0].consent_receipts[-1].policy_owner == "dotmac_campaigns"
    )


def test_expiry_after_publication_stops_transport_and_keeps_consent_evidence(
    db: Session,
) -> None:
    campaign, timers, command = _dated(db)
    assert command.delivery_expires_at is not None
    result = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=timers.only_current_trigger(),
        timers=timers,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="holiday-due",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=NOW,
    )
    assert result.dispatch_id is not None
    before = authorize_delivery(
        db,
        tenant_id=TENANT_ID,
        dispatch_id=result.dispatch_id,
        evaluated_at=command.delivery_expires_at - timedelta(microseconds=1),
    )
    after = authorize_delivery(
        db,
        tenant_id=TENANT_ID,
        dispatch_id=result.dispatch_id,
        evaluated_at=command.delivery_expires_at,
    )
    assert before.allowed
    assert not after.allowed and after.reason == "campaign_delivery_expired"
    assert (
        campaign_snapshot(
            db, tenant_id=TENANT_ID, campaign_id=campaign.id
        ).counters.suppressed
        == 1
    )
    assert may_send(
        db,
        TENANT_ID,
        channel="email",
        address="person@example.com",
        category="campaign",
    )


def test_expiry_cannot_erase_an_already_delivered_fact(db: Session) -> None:
    campaign, timers, command = _dated(db)
    assert command.delivery_expires_at is not None
    result = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=timers.only_current_trigger(),
        timers=timers,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="holiday-due",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=NOW,
    )
    assert result.dispatch_id is not None
    record_observation(
        db,
        tenant_id=TENANT_ID,
        observation=Observation(
            dispatch_id=result.dispatch_id,
            source_owner="test",
            source_event_id="delivered",
            source_fingerprint="d" * 64,
            kind=ObservationKind.DELIVERY,
            delivery_state=DeliveryState.DELIVERED,
            occurred_at=NOW + timedelta(minutes=1),
        ),
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW + timedelta(minutes=1),
        timers=timers,
    )
    assert not authorize_delivery(
        db,
        tenant_id=TENANT_ID,
        dispatch_id=result.dispatch_id,
        evaluated_at=command.delivery_expires_at,
    ).allowed
    snapshot = campaign_snapshot(db, tenant_id=TENANT_ID, campaign_id=campaign.id)
    assert snapshot.counters.delivered == 1
    assert snapshot.recipients[0].steps[0].delivery_state is DeliveryState.DELIVERED


def test_expired_stale_trigger_cannot_suppress_a_new_timer_generation(
    db: Session,
) -> None:
    campaign, timers, command = _dated(db)
    assert command.delivery_expires_at is not None
    stale = timers.only_current_trigger()
    pause_campaign(db, tenant_id=TENANT_ID, campaign_id=campaign.id, paused_at=NOW)
    resume_campaign(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        timers=timers,
        resumed_at=NOW + timedelta(seconds=1),
    )
    result = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=stale,
        timers=timers,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="stale-dated",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=command.delivery_expires_at,
    )
    assert result.status == "stale"
    assert (
        campaign_snapshot(
            db, tenant_id=TENANT_ID, campaign_id=campaign.id
        ).counters.suppressed
        == 0
    )
    assert timers.only_current_trigger().generation != stale.generation


def test_dated_campaign_cannot_resume_after_its_deadline(db: Session) -> None:
    campaign, timers, command = _dated(db)
    assert command.delivery_expires_at is not None
    pause_campaign(db, tenant_id=TENANT_ID, campaign_id=campaign.id, paused_at=NOW)
    from dotmac_campaigns import InvalidTransition

    with pytest.raises(InvalidTransition, match="deadline"):
        resume_campaign(
            db,
            tenant_id=TENANT_ID,
            campaign_id=campaign.id,
            timers=timers,
            resumed_at=command.delivery_expires_at,
        )


def test_next_send_window_cannot_push_a_dated_greeting_into_tomorrow(
    db: Session,
) -> None:
    campaign, timers, _ = _dated(db, window=(time(8), time(10)))
    result = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=timers.only_current_trigger(),
        timers=timers,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="late-window",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=NOW + timedelta(hours=3),
    )
    assert result.status == "delivery_window_closed"
    assert not db.scalars(select(OutboxEvent)).all()
    snapshot = campaign_snapshot(db, tenant_id=TENANT_ID, campaign_id=campaign.id)
    assert snapshot.counters.suppressed == 1
    assert (
        snapshot.recipients[0].consent_receipts[-1].reason
        == "campaign_delivery_window_closed"
    )


def test_changed_create_fingerprint_conflicts_instead_of_reusing_the_key(
    db: Session,
) -> None:
    campaign = _create(db)
    replay = _create(db)
    assert replay.id == campaign.id

    changed = _command()
    changed = CreateCampaign(**{**changed.as_dict(), "name": "Changed"})
    with pytest.raises(Exception, match="different request"):
        create_campaign(
            db,
            tenant_id=TENANT_ID,
            command=changed,
            idempotency_key="create-1",
            idempotency_expires_at=NOW + timedelta(days=7),
            recorded_at=NOW,
        )


def test_suppression_is_receipted_when_the_audience_snapshot_is_built(
    db: Session,
) -> None:
    campaign = _create(db)
    suppress(
        db,
        TENANT_ID,
        channel="email",
        address="person@example.com",
        reason="unsubscribe",
    )

    result = ingest_audience(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="audience-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )

    assert result.created == 1
    assert result.suppressed == 1
    timeline = campaign_snapshot(db, tenant_id=TENANT_ID, campaign_id=campaign.id)
    assert timeline.counters.suppressed == 1
    assert timeline.recipients[0].consent_receipts[0].phase == "audience"
    assert timeline.recipients[0].consent_receipts[0].allowed is False


def test_later_suppression_wins_before_delayed_delivery(db: Session) -> None:
    campaign = _create(db)
    ingest_audience(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="audience-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )
    timers = FakeTimerPort()
    schedule_campaign(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        timers=timers,
        idempotency_key="schedule-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )
    trigger = timers.only_current_trigger()

    suppress(
        db,
        TENANT_ID,
        channel="email",
        address="person@example.com",
        reason="complaint",
    )
    outcome = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=trigger,
        timers=timers,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="due-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=NOW,
    )

    assert outcome.status == "suppressed"
    assert db.scalars(select(OutboxEvent)).all() == []
    assert (
        campaign_snapshot(
            db, tenant_id=TENANT_ID, campaign_id=campaign.id
        ).counters.suppressed
        == 1
    )


def test_snapshot_and_steps_cannot_change_once_sending_begins(db: Session) -> None:
    campaign = _create(db)
    ingest_audience(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="audience-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )
    timers = FakeTimerPort()
    schedule_campaign(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        timers=timers,
        idempotency_key="schedule-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )
    accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=timers.only_current_trigger(),
        timers=timers,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="due-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=NOW,
    )

    with pytest.raises(SnapshotImmutable):
        ingest_audience(
            db,
            tenant_id=TENANT_ID,
            campaign_id=campaign.id,
            batch=_audience("later@example.com"),
            idempotency_key="audience-2",
            idempotency_expires_at=NOW + timedelta(days=7),
            evaluated_at=NOW,
        )


def test_delivery_observation_schedules_only_the_immediate_successor(
    db: Session,
) -> None:
    campaign = _create(db)
    ingest_audience(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="audience-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )
    timers = FakeTimerPort()
    schedule_campaign(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        timers=timers,
        idempotency_key="schedule-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )
    accepted = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=timers.only_current_trigger(),
        timers=timers,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="due-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=NOW,
    )
    assert accepted.delivery_intent_id is not None

    recorded = record_observation(
        db,
        tenant_id=TENANT_ID,
        observation=Observation(
            dispatch_id=accepted.dispatch_id,
            kind=ObservationKind.DELIVERY,
            delivery_state=DeliveryState.DELIVERED,
            source_owner="dotmac_integrator",
            source_event_id="provider-event-1",
            source_fingerprint="b" * 64,
            occurred_at=NOW + timedelta(minutes=1),
        ),
        timers=timers,
        idempotency_expires_at=NOW + timedelta(days=30),
        recorded_at=NOW + timedelta(minutes=1),
    )

    assert recorded.replayed is False
    current = timers.current_triggers()
    assert len(current) == 1
    assert current[0].due_at == NOW + timedelta(minutes=1, days=2)


def test_duplicate_observation_is_a_replay_and_terminal_state_does_not_regress(
    db: Session,
) -> None:
    campaign = _create(db)
    ingest_audience(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="audience-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )
    timers = FakeTimerPort()
    schedule_campaign(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        timers=timers,
        idempotency_key="schedule-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )
    accepted = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=timers.only_current_trigger(),
        timers=timers,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="due-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=NOW,
    )
    delivered = Observation(
        dispatch_id=accepted.dispatch_id,
        kind=ObservationKind.DELIVERY,
        delivery_state=DeliveryState.DELIVERED,
        source_owner="dotmac_integrator",
        source_event_id="provider-event-1",
        source_fingerprint="b" * 64,
        occurred_at=NOW + timedelta(minutes=1),
    )
    first = record_observation(
        db,
        tenant_id=TENANT_ID,
        observation=delivered,
        timers=timers,
        idempotency_expires_at=NOW + timedelta(days=30),
        recorded_at=NOW + timedelta(minutes=1),
    )
    replay = record_observation(
        db,
        tenant_id=TENANT_ID,
        observation=delivered,
        timers=timers,
        idempotency_expires_at=NOW + timedelta(days=30),
        recorded_at=NOW + timedelta(minutes=2),
    )
    assert first.observation_id == replay.observation_id
    assert replay.replayed

    record_observation(
        db,
        tenant_id=TENANT_ID,
        observation=Observation(
            dispatch_id=accepted.dispatch_id,
            kind=ObservationKind.DELIVERY,
            delivery_state=DeliveryState.ACCEPTED,
            source_owner="dotmac_integrator",
            source_event_id="late-accepted",
            source_fingerprint="c" * 64,
            occurred_at=NOW,
        ),
        timers=timers,
        idempotency_expires_at=NOW + timedelta(days=30),
        recorded_at=NOW + timedelta(minutes=3),
    )
    snapshot = campaign_snapshot(db, tenant_id=TENANT_ID, campaign_id=campaign.id)
    assert snapshot.recipients[0].steps[0].delivery_state == DeliveryState.DELIVERED


def test_cancellation_stops_new_intents_without_erasing_history(db: Session) -> None:
    campaign = _create(db)
    ingest_audience(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="audience-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )
    timers = FakeTimerPort()
    schedule_campaign(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        timers=timers,
        idempotency_key="schedule-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )
    trigger = timers.only_current_trigger()
    cancel_campaign(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        timers=timers,
        reason="operator_request",
        cancelled_at=NOW,
    )

    assert campaign.status == CampaignStatus.CANCELLED.value
    outcome = accept_due_work(
        db,
        tenant_id=TENANT_ID,
        trigger=trigger,
        timers=timers,
        renderer=FakeRenderer(),
        senders=FakeSenderResolver(),
        idempotency_key="due-after-cancel",
        idempotency_expires_at=NOW + timedelta(days=7),
        accepted_at=NOW,
    )
    assert outcome.status == "cancelled"
    assert db.scalar(
        select(CampaignRecipient).where(CampaignRecipient.campaign_id == campaign.id)
    )
    assert db.scalars(select(OutboxEvent)).all() == []


def test_counter_drift_is_reported_then_rebuilt_from_recipient_facts(
    db: Session,
) -> None:
    campaign = _create(db)
    ingest_audience(
        db,
        tenant_id=TENANT_ID,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="audience-1",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )
    counter = db.scalar(
        select(CampaignCounter).where(CampaignCounter.campaign_id == campaign.id)
    )
    assert counter is not None
    counter.total_recipients = 99
    db.flush()

    drift = report_drift(db, tenant_id=TENANT_ID, campaign_id=campaign.id)
    assert drift.has_drift
    assert drift.fields["total_recipients"] == (99, 1)
    rebuild_counters(db, tenant_id=TENANT_ID, campaign_id=campaign.id, rebuilt_at=NOW)
    assert not report_drift(db, tenant_id=TENANT_ID, campaign_id=campaign.id).has_drift


def test_send_windows_preserve_sub_day_overnight_and_all_day_semantics() -> None:
    before_day_window = datetime(2026, 8, 18, 5, 30, tzinfo=UTC)
    assert next_send_at(
        before_day_window,
        timezone="Africa/Lagos",
        window_start=time(8),
        window_end=time(18),
    ) == datetime(2026, 8, 18, 7, 0, tzinfo=UTC)

    inside_overnight = datetime(2026, 8, 18, 22, 0, tzinfo=UTC)
    assert (
        next_send_at(
            inside_overnight,
            timezone="Africa/Lagos",
            window_start=time(20),
            window_end=time(6),
        )
        == inside_overnight
    )

    overnight_gap = datetime(2026, 8, 18, 10, 0, tzinfo=UTC)
    assert next_send_at(
        overnight_gap,
        timezone="Africa/Lagos",
        window_start=time(20),
        window_end=time(6),
    ) == datetime(2026, 8, 18, 19, 0, tzinfo=UTC)

    assert (
        next_send_at(
            overnight_gap,
            timezone="Africa/Lagos",
            window_start=time(8),
            window_end=time(8),
        )
        == overnight_gap
    )


def test_final_delivery_gate_revalidates_after_the_intent_was_published(
    db: Session,
) -> None:
    campaign, _, accepted = _publish_first_intent(db)
    suppress(
        db,
        TENANT_ID,
        channel="email",
        address="person@example.com",
        reason="complaint",
    )

    gate = authorize_delivery(
        db,
        tenant_id=TENANT_ID,
        dispatch_id=accepted.dispatch_id,
        evaluated_at=NOW + timedelta(minutes=1),
    )

    assert gate.allowed is False
    assert gate.reason == "complaint"
    snapshot = campaign_snapshot(db, tenant_id=TENANT_ID, campaign_id=campaign.id)
    assert snapshot.recipients[0].consent_receipts[-1].phase == "delivery"
    assert snapshot.recipients[0].steps[0].delivery_state == DeliveryState.SUPPRESSED


def test_click_implies_open_and_reply_emits_a_fact_for_the_sales_adapter(
    db: Session,
) -> None:
    campaign, timers, accepted = _publish_first_intent(db)
    record_observation(
        db,
        tenant_id=TENANT_ID,
        observation=Observation(
            dispatch_id=accepted.dispatch_id,
            kind=ObservationKind.CLICK,
            source_owner="dotmac_crm.tracking",
            source_event_id="click-1",
            source_fingerprint="d" * 64,
            occurred_at=NOW + timedelta(minutes=1),
        ),
        timers=timers,
        idempotency_expires_at=NOW + timedelta(days=30),
        recorded_at=NOW + timedelta(minutes=1),
    )
    record_observation(
        db,
        tenant_id=TENANT_ID,
        observation=Observation(
            dispatch_id=accepted.dispatch_id,
            kind=ObservationKind.REPLY,
            source_owner="dotmac_inbox",
            source_event_id="reply-1",
            source_fingerprint="e" * 64,
            occurred_at=NOW + timedelta(minutes=2),
            correlation_ref="conversation:opaque-9",
        ),
        timers=timers,
        idempotency_expires_at=NOW + timedelta(days=30),
        recorded_at=NOW + timedelta(minutes=2),
    )

    snapshot = campaign_snapshot(db, tenant_id=TENANT_ID, campaign_id=campaign.id)
    step = snapshot.recipients[0].steps[0]
    assert step.first_clicked_at is not None
    assert step.first_opened_at == step.first_clicked_at
    assert step.first_replied_at is not None
    response = db.scalar(select(CampaignResponse))
    assert response is not None
    assert response.correlation_ref == "conversation:opaque-9"
    assert db.scalar(
        select(OutboxEvent).where(OutboxEvent.event_type == "campaigns.response.v1")
    )
    facts = response_facts(db, tenant_id=TENANT_ID, campaign_id=campaign.id)
    assert facts[0].correlation_ref == "conversation:opaque-9"
    assert facts[0].kind == ObservationKind.REPLY

    published = delivery_intent(
        db, tenant_id=TENANT_ID, dispatch_id=accepted.dispatch_id
    )
    assert published.address_hash
    assert not hasattr(published, "address")


def test_unsubscribe_blocks_marketing_without_silencing_billing(db: Session) -> None:
    request_unsubscribe(
        db,
        tenant_id=TENANT_ID,
        command=UnsubscribeRequest(
            channel="email",
            address="person@example.com",
            source_owner="campaigns.unsubscribe_edge",
            source_event_id="unsubscribe-1",
            source_fingerprint="f" * 64,
            requested_at=NOW,
        ),
        idempotency_expires_at=NOW + timedelta(days=365),
    )

    assert not may_send(
        db,
        TENANT_ID,
        channel="email",
        address="person@example.com",
        category="campaign",
    )
    assert may_send(
        db,
        TENANT_ID,
        channel="email",
        address="person@example.com",
        category="billing",
    )


def test_explicit_privacy_deadline_scrubs_pii_but_keeps_hashed_evidence(
    db: Session,
) -> None:
    _, _, accepted = _publish_first_intent(db)
    assert (
        purge_expired_pii(
            db,
            tenant_id=TENANT_ID,
            before=NOW + timedelta(days=89),
            limit=10,
            scrubbed_at=NOW + timedelta(days=89),
        )
        == 0
    )
    assert (
        purge_expired_pii(
            db,
            tenant_id=TENANT_ID,
            before=NOW + timedelta(days=91),
            limit=10,
            scrubbed_at=NOW + timedelta(days=91),
        )
        == 2
    )
    recipient = db.scalar(select(CampaignRecipient))
    intent = db.get(CampaignDeliveryIntent, accepted.delivery_intent_id)
    assert recipient is not None and recipient.address is None
    assert recipient.address_hash
    assert intent is not None and intent.address is None
    assert intent.address_hash
    assert intent.rendered_body is None
