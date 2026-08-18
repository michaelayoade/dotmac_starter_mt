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
    accept_due_work,
    campaign_snapshot,
    cancel_campaign,
    create_campaign,
    ingest_audience,
    rebuild_counters,
    record_observation,
    report_drift,
    schedule_campaign,
)
from dotmac_campaigns.fakes import FakeRenderer, FakeSenderResolver, FakeTimerPort
from dotmac_campaigns.models import ALL_MODELS, CampaignCounter, CampaignRecipient
from dotmac_kernel.consent import register_marketing_categories, suppress
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
