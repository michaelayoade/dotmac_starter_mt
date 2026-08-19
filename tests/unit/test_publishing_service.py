"""Mkt publication parity for the product-neutral publishing owner."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.models import Tenant
from dotmac_publishing import (
    Conflict,
    DeliveryObservationV1,
    DeliveryOutcome,
    DeliveryState,
    NotFound,
    PublicationSnapshotV1,
    PublicationState,
    PublicationTarget,
    RequestPublication,
    StaleTimer,
    TimerAcceptance,
    TimerCancellation,
    cancel_publication,
    dispatch_due_publication,
    get_publication,
    list_publications,
    record_delivery_observation,
    request_publication,
    retry_delivery,
)
from dotmac_publishing.contracts import (
    PublicationTimerTrigger,
    ScheduledPublicationTimer,
)
from dotmac_publishing.models import (
    TENANT_TABLES,
    PublicationAttempt,
    PublicationDelivery,
    PublicationObservation,
    metadata_table,
)
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)


class FakeTimers:
    def __init__(self) -> None:
        self.current: dict[tuple[uuid.UUID, uuid.UUID], ScheduledPublicationTimer] = {}
        self.schedule_count = 0

    def schedule(
        self,
        db: Session | None,
        *,
        tenant_id: uuid.UUID,
        publication_release_id: uuid.UUID,
        due_at: datetime,
        recorded_at: datetime,
    ) -> ScheduledPublicationTimer:
        del db, recorded_at
        key = (tenant_id, publication_release_id)
        previous = self.current.get(key)
        scheduled = ScheduledPublicationTimer(
            timer_ref=uuid.uuid4(),
            publication_release_id=publication_release_id,
            generation=1 if previous is None else previous.generation + 1,
            due_at=due_at,
        )
        self.current[key] = scheduled
        self.schedule_count += 1
        return scheduled

    def accept(
        self,
        db: Session | None,
        *,
        tenant_id: uuid.UUID,
        trigger: PublicationTimerTrigger,
        accepted_at: datetime,
    ) -> TimerAcceptance:
        del db, accepted_at
        current = self.current.get((tenant_id, trigger.publication_release_id))
        if current is None or current.generation != trigger.generation:
            return TimerAcceptance(current=False, reason="stale_generation")
        return TimerAcceptance(current=True)

    def cancel(
        self,
        db: Session | None,
        *,
        tenant_id: uuid.UUID,
        publication_release_id: uuid.UUID,
        recorded_at: datetime,
    ) -> TimerCancellation:
        del db, recorded_at
        removed = self.current.pop((tenant_id, publication_release_id), None)
        return TimerCancellation(cancelled=removed is not None)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_publishing": None}},
    )
    Tenant.__table__.create(engine)
    IdempotencyRecord.__table__.create(engine)
    OutboxEvent.__table__.create(engine)
    for table_name in TENANT_TABLES:
        metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="alpha", name="Alpha"),
                Tenant(id=TENANT_B, slug="bravo", name="Bravo"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def _snapshot(body: str = "Immutable launch body") -> PublicationSnapshotV1:
    return PublicationSnapshotV1(
        source_ref="content:item:launch",
        title="Launch",
        body=body,
        variant_key=None,
        creative_refs=("file:hero",),
    )


def _command(
    *,
    key: str = "launch-01",
    body: str = "Immutable launch body",
    targets: tuple[str, ...] = ("binding:one", "binding:two"),
) -> RequestPublication:
    return RequestPublication(
        request_key=key,
        requested_for=NOW + timedelta(hours=1),
        snapshot=_snapshot(body),
        targets=tuple(PublicationTarget(target_ref=value) for value in targets),
        actor_ref="party:editor",
    )


def _request(
    db: Session,
    timers: FakeTimers,
    *,
    command: RequestPublication | None = None,
):
    return request_publication(
        db,
        scope=TenantScope(TENANT_A),
        command=command or _command(),
        timers=timers,
        recorded_at=NOW,
    )


def _dispatch(db: Session, timers: FakeTimers, release_id: uuid.UUID):
    timer = timers.current[(TENANT_A, release_id)]
    return dispatch_due_publication(
        db,
        scope=TenantScope(TENANT_A),
        trigger=timer.trigger(),
        timers=timers,
        recorded_at=NOW + timedelta(hours=1),
    )


def _attempts(db: Session, release_id: uuid.UUID) -> tuple[PublicationAttempt, ...]:
    return tuple(
        db.scalars(
            select(PublicationAttempt)
            .join(
                PublicationDelivery,
                (PublicationDelivery.tenant_id == PublicationAttempt.tenant_id)
                & (
                    PublicationDelivery.id == PublicationAttempt.publication_delivery_id
                ),
            )
            .where(
                PublicationAttempt.tenant_id == TENANT_A,
                PublicationDelivery.publication_release_id == release_id,
            )
            .order_by(PublicationAttempt.attempt_number)
        )
    )


def _observe(
    db: Session,
    attempt: PublicationAttempt,
    *,
    receipt: str,
    outcome: DeliveryOutcome,
    remote_ref: str | None = None,
    error: str | None = None,
):
    return record_delivery_observation(
        db,
        scope=TenantScope(TENANT_A),
        command=DeliveryObservationV1(
            receipt_ref=receipt,
            attempt_ref=str(attempt.id),
            outcome=outcome,
            observed_at=NOW + timedelta(hours=1, minutes=1),
            remote_ref=remote_ref,
            error_detail=error,
        ),
        recorded_at=NOW + timedelta(hours=1, minutes=2),
    )


def test_request_freezes_snapshot_targets_and_timer_inside_the_tenant(
    db: Session,
) -> None:
    timers = FakeTimers()
    release = _request(db, timers)

    assert release.tenant_id == TENANT_A
    assert release.state == PublicationState.SCHEDULED
    assert release.snapshot_payload["body"] == "Immutable launch body"
    assert release.snapshot_digest == _snapshot().digest
    assert release.timer_generation == 1
    assert timers.schedule_count == 1
    assert [delivery.target_ref for delivery in release.deliveries] == [
        "binding:one",
        "binding:two",
    ]
    assert all(
        delivery.state == DeliveryState.PENDING for delivery in release.deliveries
    )
    assert list_publications(db, scope=TenantScope(TENANT_A)) == (release,)
    assert (
        get_publication(
            db, scope=TenantScope(TENANT_A), publication_release_id=release.id
        )
        is release
    )
    with pytest.raises(NotFound):
        get_publication(
            db, scope=TenantScope(TENANT_B), publication_release_id=release.id
        )


def test_request_replays_same_fingerprint_and_conflicts_on_changed_content(
    db: Session,
) -> None:
    timers = FakeTimers()
    first = _request(db, timers)
    replay = _request(db, timers)
    assert replay.id == first.id
    assert timers.schedule_count == 1

    with pytest.raises(Conflict, match="different request"):
        _request(db, timers, command=_command(body="Changed body"))


def test_due_dispatch_writes_one_attempt_and_outbox_intent_per_target(
    db: Session,
) -> None:
    timers = FakeTimers()
    release = _request(db, timers)
    dispatched = _dispatch(db, timers, release.id)

    assert dispatched.state == PublicationState.DISPATCHING
    attempts = _attempts(db, release.id)
    assert len(attempts) == 2
    assert {attempt.state for attempt in attempts} == {DeliveryState.INTENT_PUBLISHED}
    assert {attempt.attempt_number for attempt in attempts} == {1}
    assert db.scalar(select(func.count(OutboxEvent.id))) == 2
    assert {event.event_type for event in db.scalars(select(OutboxEvent))} == {
        "publishing.dispatch.v1"
    }


def test_stale_timer_cannot_duplicate_dispatch(db: Session) -> None:
    timers = FakeTimers()
    release = _request(db, timers)
    stale = timers.current[(TENANT_A, release.id)].trigger()
    timers.schedule(
        db,
        tenant_id=TENANT_A,
        publication_release_id=release.id,
        due_at=release.requested_for,
        recorded_at=NOW,
    )
    with pytest.raises(StaleTimer, match="stale"):
        dispatch_due_publication(
            db,
            scope=TenantScope(TENANT_A),
            trigger=stale,
            timers=timers,
            recorded_at=release.requested_for,
        )
    assert _attempts(db, release.id) == ()
    assert db.scalar(select(func.count(OutboxEvent.id))) == 0


def test_partial_and_all_failed_outcomes_are_retained_and_derived(db: Session) -> None:
    timers = FakeTimers()
    partial = _request(db, timers)
    _dispatch(db, timers, partial.id)
    first, second = _attempts(db, partial.id)

    _observe(
        db,
        first,
        receipt="receipt:published",
        outcome=DeliveryOutcome.PUBLISHED,
        remote_ref="remote:one",
    )
    result = _observe(
        db,
        second,
        receipt="receipt:failed",
        outcome=DeliveryOutcome.FAILED,
        error="remote publish failed",
    )
    assert result.publication_state == PublicationState.PARTIAL
    assert (
        get_publication(
            db, scope=TenantScope(TENANT_A), publication_release_id=partial.id
        ).state
        == PublicationState.PARTIAL
    )

    failed = _request(db, timers, command=_command(key="launch-02"))
    _dispatch(db, timers, failed.id)
    for index, attempt in enumerate(_attempts(db, failed.id), start=1):
        outcome = _observe(
            db,
            attempt,
            receipt=f"receipt:all-failed:{index}",
            outcome=DeliveryOutcome.FAILED,
            error="remote publish failed",
        )
    assert outcome.publication_state == PublicationState.FAILED
    assert db.scalar(select(func.count(PublicationObservation.id))) == 4


def test_observation_replay_is_idempotent_and_changed_receipt_conflicts(
    db: Session,
) -> None:
    timers = FakeTimers()
    release = _request(db, timers, command=_command(targets=("binding:one",)))
    _dispatch(db, timers, release.id)
    attempt = _attempts(db, release.id)[0]
    first = _observe(
        db,
        attempt,
        receipt="receipt:stable",
        outcome=DeliveryOutcome.PUBLISHED,
        remote_ref="remote:one",
    )
    replay = _observe(
        db,
        attempt,
        receipt="receipt:stable",
        outcome=DeliveryOutcome.PUBLISHED,
        remote_ref="remote:one",
    )
    assert replay.observation_id == first.observation_id
    assert db.scalar(select(func.count(PublicationObservation.id))) == 1

    with pytest.raises(Conflict, match="receipt"):
        _observe(
            db,
            attempt,
            receipt="receipt:stable",
            outcome=DeliveryOutcome.FAILED,
            error="changed claim",
        )


def test_failed_target_retries_with_monotonic_attempt_and_can_reconcile(
    db: Session,
) -> None:
    timers = FakeTimers()
    release = _request(db, timers, command=_command(targets=("binding:one",)))
    _dispatch(db, timers, release.id)
    first = _attempts(db, release.id)[0]
    first_result = _observe(
        db,
        first,
        receipt="receipt:first-failure",
        outcome=DeliveryOutcome.FAILED,
        error="temporary failure",
    )
    assert first_result.publication_state == PublicationState.FAILED

    second = retry_delivery(
        db,
        scope=TenantScope(TENANT_A),
        publication_delivery_id=first.publication_delivery_id,
        request_key="retry:launch-01:one",
        recorded_at=NOW + timedelta(hours=2),
    )
    assert second.attempt_number == 2
    assert second.state == DeliveryState.INTENT_PUBLISHED
    final = _observe(
        db,
        second,
        receipt="receipt:retry-success",
        outcome=DeliveryOutcome.PUBLISHED,
        remote_ref="remote:one",
    )
    assert final.publication_state == PublicationState.PUBLISHED
    assert db.scalar(select(func.count(OutboxEvent.id))) == 2


def test_cancel_before_dispatch_retains_release_and_cancels_targets(
    db: Session,
) -> None:
    timers = FakeTimers()
    release = _request(db, timers)
    cancelled = cancel_publication(
        db,
        scope=TenantScope(TENANT_A),
        publication_release_id=release.id,
        timers=timers,
        recorded_at=NOW + timedelta(minutes=1),
    )
    assert cancelled.state == PublicationState.CANCELLED
    assert {delivery.state for delivery in cancelled.deliveries} == {
        DeliveryState.CANCELLED
    }
    assert (TENANT_A, release.id) not in timers.current


def test_explicit_tenant_scope_is_required(db: Session) -> None:
    with pytest.raises(TypeError, match="TenantScope"):
        request_publication(  # type: ignore[arg-type]
            db,
            scope=TENANT_A,
            command=_command(),
            timers=FakeTimers(),
            recorded_at=NOW,
        )
