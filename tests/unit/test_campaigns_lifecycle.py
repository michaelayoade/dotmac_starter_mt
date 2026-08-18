"""Campaign lifecycle canaries, written before the shared implementation.

These are the Sub parity properties that define the extraction: immutable
audiences, repeated consent checks, ordered nurture progression, deterministic
send windows/senders, one delivery intent, monotonic observations and repairable
projections. Product ORM and provider behavior deliberately do not appear here.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta

import pytest
from dotmac_campaigns import (
    AudienceBatch,
    AudienceCandidate,
    CampaignKind,
    CampaignStatus,
    CreateCampaign,
    DeliveryState,
    Observation,
    ObservationKind,
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
    purge_expired_pii,
    rebuild_counters,
    record_observation,
    report_drift,
    request_unsubscribe,
    response_facts,
    schedule_campaign,
)
from dotmac_campaigns.fakes import FakeRenderer, FakeSenderResolver, FakeTimerPort
from dotmac_campaigns.models import (
    ALL_MODELS,
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
