"""Canonical Collections writers over persisted tenant and platform facts."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from dotmac_collections.actions import (
    ActionApplied,
    ActionFailed,
    ActionReceiptConflict,
    CollectionActionRequestedV1,
)
from dotmac_collections.contracts import (
    AssessCollectionExposureV1,
    TriggerProvenanceV1,
)
from dotmac_collections.models import (
    CollectionActionReceipt,
    CollectionActionRequest,
    CollectionCase,
    CollectionCaseExposure,
    CollectionCaseTransition,
    CollectionGraceGrant,
    CollectionNoticeReceipt,
    CollectionNoticeRequest,
    CollectionPolicy,
    CollectionPolicyStep,
    CollectionPolicyVersion,
    CollectionReconciliation,
    CollectionStepAttempt,
    PaymentArrangement,
    PaymentArrangementExposure,
    PaymentArrangementInstallment,
    PaymentArrangementSettlementReceipt,
    PlatformCollectionActionReceipt,
    PlatformCollectionActionRequest,
    PlatformCollectionCase,
    PlatformCollectionCaseExposure,
    PlatformCollectionCaseTransition,
    PlatformCollectionGraceGrant,
    PlatformCollectionNoticeReceipt,
    PlatformCollectionNoticeRequest,
    PlatformCollectionPolicy,
    PlatformCollectionPolicyStep,
    PlatformCollectionPolicyVersion,
    PlatformCollectionReconciliation,
    PlatformCollectionStepAttempt,
    PlatformPaymentArrangement,
    PlatformPaymentArrangementExposure,
    PlatformPaymentArrangementInstallment,
    PlatformPaymentArrangementSettlementReceipt,
)
from dotmac_collections.notices import CollectionNoticeRequestedV1
from dotmac_collections.policies import (
    PolicyPublicationV1,
    PolicyStepDraftV1,
    PolicyVersionDraftV1,
)
from dotmac_collections.receivables import (
    FakeReceivablesReader,
    PositionReadOk,
    PositionUnavailable,
    ReceivableObservationV1,
)
from dotmac_collections.service import (
    AssessmentBlocked,
    CaseAssessed,
    CollectionActionService,
    CollectionCaseService,
    CollectionNoticeService,
    CollectionPolicyService,
    CreateCollectionPolicyV1,
    ProcessCollectionStepDueV1,
    StepDueBlocked,
    StepRequestWritten,
)
from dotmac_collections.timers import (
    STEP_DUE_EVENT_TYPE,
    FakeCollectionsTimer,
    TimerTriggerV1,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.idempotency_models import (
    IdempotencyRecord,
    PlatformIdempotencyRecord,
)
from dotmac_kernel.messaging.models import OutboxEvent, PlatformOutboxEvent
from dotmac_kernel.money import Currency, Money
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)
TENANT_ID = UUID("d0a99cb7-721d-4a14-8238-46ef3df0068b")
SCOPE = TenantScope(TENANT_ID)
PLATFORM_SCOPE = PlatformScope()
NGN = Currency("NGN", 2)

COLLECTION_TABLES = (
    CollectionPolicy,
    CollectionPolicyVersion,
    CollectionPolicyStep,
    CollectionCase,
    CollectionCaseExposure,
    CollectionCaseTransition,
    CollectionStepAttempt,
    PaymentArrangement,
    PaymentArrangementExposure,
    PaymentArrangementInstallment,
    PaymentArrangementSettlementReceipt,
    CollectionGraceGrant,
    CollectionNoticeRequest,
    CollectionNoticeReceipt,
    CollectionActionRequest,
    CollectionActionReceipt,
    CollectionReconciliation,
)
PLATFORM_COLLECTION_TABLES = (
    PlatformCollectionPolicy,
    PlatformCollectionPolicyVersion,
    PlatformCollectionPolicyStep,
    PlatformCollectionCase,
    PlatformCollectionCaseExposure,
    PlatformCollectionCaseTransition,
    PlatformCollectionStepAttempt,
    PlatformPaymentArrangement,
    PlatformPaymentArrangementExposure,
    PlatformPaymentArrangementInstallment,
    PlatformPaymentArrangementSettlementReceipt,
    PlatformCollectionGraceGrant,
    PlatformCollectionNoticeRequest,
    PlatformCollectionNoticeReceipt,
    PlatformCollectionActionRequest,
    PlatformCollectionActionReceipt,
    PlatformCollectionReconciliation,
)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_coll": None}},
    )
    IdempotencyRecord.__table__.create(engine)
    OutboxEvent.__table__.create(engine)
    for model in COLLECTION_TABLES:
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def platform_db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_coll": None}},
    )
    PlatformIdempotencyRecord.__table__.create(engine)
    PlatformOutboxEvent.__table__.create(engine)
    for model in PLATFORM_COLLECTION_TABLES:
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _policy(db: Session, *, request_kind: str = "action") -> UUID:
    policy_id = uuid4()
    CollectionPolicyService.create(
        db,
        CreateCollectionPolicyV1(
            policy_id=policy_id,
            scope=SCOPE,
            policy_code="standard_arrears",
            description="Versioned arrears ladder",
        ),
    )
    version_id = uuid4()
    result = CollectionPolicyService.publish(
        db,
        scope=SCOPE,
        draft=PolicyVersionDraftV1(
            policy_code="standard_arrears",
            reason_code="invoice_overdue",
            collection_timing="arrears",
            grace=None,
            steps=(
                PolicyStepDraftV1(
                    code="initial_notice",
                    ordinal=1,
                    offset=timedelta(0),
                    offset_anchor="exposure_at",
                    request_kind=request_kind,
                    action_code=(
                        "restrict_service" if request_kind == "action" else None
                    ),
                    purpose_code=(
                        "arrears_initial_notice" if request_kind == "notice" else None
                    ),
                    effect_scope="service" if request_kind == "action" else None,
                    receipt_required=True,
                    retry_offsets=(timedelta(minutes=5),),
                ),
            ),
        ),
        publication=PolicyPublicationV1(
            policy_version_id=version_id,
            version=1,
            effective_from=NOW,
            actor_ref="staff:1",
            reason="Initial publication",
            published_at=NOW,
        ),
    )
    assert result.policy_version_id == version_id
    assert not result.replayed
    return version_id


def _position(
    *, version: int, amount: str, financial_state: str = "open"
) -> ReceivableObservationV1:
    return ReceivableObservationV1(
        scope=SCOPE,
        source_owner="billing.receivables",
        exposure_ref="invoice:inv-1",
        source_version=version,
        state_fingerprint=f"sha256:position-v{version}-{amount}",
        subject_ref="subscriber:sub-1",
        service_ref="service:svc-1",
        collection_timing="arrears",
        reason_code="invoice_overdue",
        collectible_receivable=Money.of(amount, NGN),
        service_period_status="not_applicable",
        service_period_starts_at=None,
        service_period_ends_at=None,
        due_at=NOW - timedelta(days=1),
        due_date_status="verified",
        financial_state=financial_state,  # type: ignore[arg-type]
        source_authority="internal",
        projection_mode="authoritative",
        completeness="complete",
        completeness_reason_code=None,
        observed_at=NOW,
    )


def _command(
    *, command_id: UUID | None = None, key: str | None = None
) -> AssessCollectionExposureV1:
    command_id = command_id or uuid4()
    return AssessCollectionExposureV1(
        command_id=command_id,
        idempotency_key=key or f"assessment:{command_id}",
        correlation_id=uuid4(),
        causal_event_id=f"billing-event:{command_id}",
        scope=SCOPE,
        source_owner="billing.receivables",
        exposure_ref="invoice:inv-1",
        subject_ref="subscriber:sub-1",
        service_ref="service:svc-1",
        collection_timing="arrears",
        reason_code="invoice_overdue",
        trigger=TriggerProvenanceV1(
            kind="receivable_changed",
            trigger_id=f"trigger:{command_id}",
            triggered_at=NOW,
        ),
    )


def _reader(result: PositionReadOk | PositionUnavailable) -> FakeReceivablesReader:
    reader = FakeReceivablesReader()
    reader.set_result(
        scope=SCOPE,
        source_owner="billing.receivables",
        exposure_ref="invoice:inv-1",
        result=result,
    )
    return reader


def test_platform_scope_uses_the_platform_models_and_idempotency_ledger(
    platform_db: Session,
) -> None:
    policy_id = uuid4()
    CollectionPolicyService.create(
        platform_db,
        CreateCollectionPolicyV1(
            policy_id=policy_id,
            scope=PLATFORM_SCOPE,
            policy_code="platform_arrears",
            description="Control-plane arrears ladder",
        ),
    )
    version_id = uuid4()
    CollectionPolicyService.publish(
        platform_db,
        scope=PLATFORM_SCOPE,
        draft=PolicyVersionDraftV1(
            policy_code="platform_arrears",
            reason_code="invoice_overdue",
            collection_timing="arrears",
            grace=None,
            steps=(
                PolicyStepDraftV1(
                    code="initial_notice",
                    ordinal=1,
                    offset=timedelta(0),
                    offset_anchor="exposure_at",
                    request_kind="notice",
                    action_code=None,
                    purpose_code="platform_arrears_initial_notice",
                    effect_scope=None,
                    receipt_required=True,
                    retry_offsets=(timedelta(minutes=5),),
                ),
            ),
        ),
        publication=PolicyPublicationV1(
            policy_version_id=version_id,
            version=1,
            effective_from=NOW,
            actor_ref="platform-admin:1",
            reason="Initial platform publication",
            published_at=NOW,
        ),
    )
    position = replace(_position(version=1, amount="100.00"), scope=PLATFORM_SCOPE)
    command = replace(
        _command(),
        scope=PLATFORM_SCOPE,
        subject_ref="vendor:vendor-1",
        service_ref="platform-service:svc-1",
    )
    position = replace(
        position,
        subject_ref=command.subject_ref,
        service_ref=command.service_ref,
    )
    reader = FakeReceivablesReader()
    reader.set_result(
        scope=PLATFORM_SCOPE,
        source_owner=command.source_owner,
        exposure_ref=command.exposure_ref,
        result=PositionReadOk(position),
    )

    first = CollectionCaseService.assess(
        platform_db,
        command=command,
        policy_version_id=version_id,
        reader=reader,
        assessed_at=NOW,
    )
    replay = CollectionCaseService.assess(
        platform_db,
        command=command,
        policy_version_id=version_id,
        reader=reader,
        assessed_at=NOW,
    )

    assert isinstance(first, CaseAssessed)
    assert replay == replace(first, replayed=True)
    assert len(platform_db.scalars(select(PlatformCollectionCase)).all()) == 1
    assert len(platform_db.scalars(select(PlatformIdempotencyRecord)).all()) == 1

    notice = CollectionNoticeRequestedV1(
        request_id=uuid4(),
        idempotency_key=f"case:{first.case_id}:step:notice:attempt:1",
        case_id=first.case_id,
        policy_version_id=version_id,
        policy_step_code="initial_notice",
        step_attempt_ordinal=1,
        source_owner=position.source_owner,
        exposure_ref=position.exposure_ref,
        source_version=position.source_version,
        position_fingerprint=position.state_fingerprint,
        subject_ref=position.subject_ref,
        service_ref=position.service_ref,
        purpose_code="platform_arrears_initial_notice",
        decision_evidence=position,
        requested_at=NOW,
    )
    first_notice = CollectionNoticeService.request(
        platform_db, scope=PLATFORM_SCOPE, request=notice
    )
    replayed_notice = CollectionNoticeService.request(
        platform_db, scope=PLATFORM_SCOPE, request=notice
    )

    assert not first_notice.replayed
    assert replayed_notice.replayed
    outbox = platform_db.scalars(select(PlatformOutboxEvent)).all()
    assert len(outbox) == 1
    assert outbox[0].event_type == "collections.notice.requested.v1"
    assert outbox[0].correlation_id == str(notice.request_id)
    assert outbox[0].payload == {
        "request_id": str(notice.request_id),
        "idempotency_key": notice.idempotency_key,
        "case_id": str(first.case_id),
        "policy_version_id": str(version_id),
        "policy_step_code": "initial_notice",
        "step_attempt_ordinal": 1,
        "source_owner": position.source_owner,
        "exposure_ref": position.exposure_ref,
        "source_version": 1,
        "position_fingerprint": position.state_fingerprint,
        "subject_ref": position.subject_ref,
        "service_ref": position.service_ref,
        "purpose_code": "platform_arrears_initial_notice",
        "decision_evidence": {
            "source_owner": position.source_owner,
            "exposure_ref": position.exposure_ref,
            "source_version": 1,
            "state_fingerprint": position.state_fingerprint,
            "subject_ref": position.subject_ref,
            "service_ref": position.service_ref,
            "collection_timing": "arrears",
            "reason_code": "invoice_overdue",
            "collectible_receivable": {
                "amount": "100.00",
                "currency": "NGN",
                "minor_units": 2,
            },
            "service_period_status": "not_applicable",
            "service_period_starts_at": None,
            "service_period_ends_at": None,
            "due_at": (NOW - timedelta(days=1)).isoformat(),
            "due_date_status": "verified",
            "financial_state": "open",
            "source_authority": "internal",
            "projection_mode": "authoritative",
            "completeness": "complete",
            "completeness_reason_code": None,
            "observed_at": NOW.isoformat(),
        },
        "requested_at": NOW.isoformat(),
    }


def test_unavailable_position_writes_no_case(db: Session) -> None:
    policy_version_id = _policy(db)
    result = CollectionCaseService.assess(
        db,
        command=_command(),
        policy_version_id=policy_version_id,
        reader=_reader(
            PositionUnavailable(reason_code="billing_cutover", retry_after=NOW)
        ),
        assessed_at=NOW,
    )
    assert result == AssessmentBlocked("billing_cutover", NOW)
    assert db.scalars(select(CollectionCase)).all() == []


def test_case_closes_and_a_later_positive_position_opens_a_fresh_case(
    db: Session,
) -> None:
    policy_version_id = _policy(db)
    first_command = _command()
    first = CollectionCaseService.assess(
        db,
        command=first_command,
        policy_version_id=policy_version_id,
        reader=_reader(PositionReadOk(_position(version=1, amount="100.00"))),
        assessed_at=NOW,
    )
    replay = CollectionCaseService.assess(
        db,
        command=first_command,
        policy_version_id=policy_version_id,
        reader=_reader(PositionReadOk(_position(version=1, amount="100.00"))),
        assessed_at=NOW,
    )
    assert isinstance(first, CaseAssessed)
    assert replay == replace(first, replayed=True)

    closed = CollectionCaseService.assess(
        db,
        command=_command(),
        policy_version_id=policy_version_id,
        reader=_reader(
            PositionReadOk(
                _position(version=2, amount="0.00", financial_state="resolved")
            )
        ),
        assessed_at=NOW + timedelta(hours=1),
    )
    assert isinstance(closed, CaseAssessed)
    assert closed.lifecycle == "resolved"

    reopened = CollectionCaseService.assess(
        db,
        command=_command(),
        policy_version_id=policy_version_id,
        reader=_reader(PositionReadOk(_position(version=3, amount="50.00"))),
        assessed_at=NOW + timedelta(hours=2),
    )
    assert isinstance(reopened, CaseAssessed)
    assert reopened.lifecycle == "active"
    assert reopened.case_id != first.case_id
    assert len(db.scalars(select(CollectionCase)).all()) == 2


def test_action_receipt_replays_and_changed_owner_evidence_conflicts(
    db: Session,
) -> None:
    policy_version_id = _policy(db)
    assessed = CollectionCaseService.assess(
        db,
        command=_command(),
        policy_version_id=policy_version_id,
        reader=_reader(PositionReadOk(_position(version=1, amount="100.00"))),
        assessed_at=NOW,
    )
    assert isinstance(assessed, CaseAssessed)
    request_id = uuid4()
    request = CollectionActionRequestedV1(
        request_id=request_id,
        idempotency_key=f"case:{assessed.case_id}:step:restrict:attempt:1",
        case_id=assessed.case_id,
        policy_version_id=policy_version_id,
        policy_step_code="initial_notice",
        step_attempt_ordinal=1,
        source_owner="billing.receivables",
        exposure_ref="invoice:inv-1",
        source_version=1,
        position_fingerprint=_position(version=1, amount="100.00").state_fingerprint,
        subject_ref="subscriber:sub-1",
        service_ref="service:svc-1",
        action_code="restrict_service",
        effect_scope="service",
        decision_evidence=_position(version=1, amount="100.00"),
        requested_at=NOW,
    )
    first_request = CollectionActionService.request(db, scope=SCOPE, request=request)
    replay_request = CollectionActionService.request(db, scope=SCOPE, request=request)
    assert not first_request.replayed
    assert replay_request.replayed
    outbox = db.scalars(select(OutboxEvent)).all()
    assert len(outbox) == 1
    assert outbox[0].tenant_id == TENANT_ID
    assert outbox[0].event_type == "collections.action.requested.v1"
    assert outbox[0].correlation_id == str(request_id)
    assert outbox[0].payload["request_id"] == str(request_id)
    assert outbox[0].payload["action_code"] == "restrict_service"
    assert outbox[0].payload["effect_scope"] == "service"
    decision_evidence = outbox[0].payload["decision_evidence"]
    assert isinstance(decision_evidence, dict)
    assert decision_evidence["collectible_receivable"] == {
        "amount": "100.00",
        "currency": "NGN",
        "minor_units": 2,
    }

    receipt = ActionApplied(
        request_id=request_id,
        owner_code="subscriptions.access",
        owner_receipt_id="receipt:1",
        action_ref="restriction:1",
        applied_at=NOW,
        owner_state_fingerprint="sha256:access-v2",
    )
    first_receipt = CollectionActionService.record_receipt(
        db, scope=SCOPE, receipt=receipt
    )
    replay_receipt = CollectionActionService.record_receipt(
        db, scope=SCOPE, receipt=receipt
    )
    assert not first_receipt.replayed
    assert replay_receipt.replayed
    with pytest.raises(ActionReceiptConflict):
        CollectionActionService.record_receipt(
            db,
            scope=SCOPE,
            receipt=replace(receipt, owner_state_fingerprint="sha256:changed"),
        )


def _two_step_policy(db: Session) -> UUID:
    policy_id = uuid4()
    CollectionPolicyService.create(
        db,
        CreateCollectionPolicyV1(
            policy_id=policy_id,
            scope=SCOPE,
            policy_code="timer_ladder",
            description="Timer-owned production ladder",
        ),
    )
    version_id = uuid4()
    CollectionPolicyService.publish(
        db,
        scope=SCOPE,
        draft=PolicyVersionDraftV1(
            policy_code="timer_ladder",
            reason_code="invoice_overdue",
            collection_timing="arrears",
            grace=None,
            steps=(
                PolicyStepDraftV1(
                    code="restrict",
                    ordinal=1,
                    offset=timedelta(0),
                    offset_anchor="exposure_at",
                    request_kind="action",
                    action_code="restrict_service",
                    purpose_code=None,
                    effect_scope="service",
                    receipt_required=True,
                    retry_offsets=(timedelta(minutes=10),),
                ),
                PolicyStepDraftV1(
                    code="follow_up",
                    ordinal=2,
                    offset=timedelta(hours=1),
                    offset_anchor="request_at",
                    request_kind="notice",
                    action_code=None,
                    purpose_code="restriction_follow_up",
                    effect_scope=None,
                    receipt_required=False,
                    retry_offsets=(),
                ),
            ),
        ),
        publication=PolicyPublicationV1(
            policy_version_id=version_id,
            version=1,
            effective_from=NOW,
            actor_ref="staff:1",
            reason="Timer ladder publication",
            published_at=NOW,
        ),
    )
    return version_id


def _trigger(timer: FakeCollectionsTimer, db: Session, case_id: UUID) -> TimerTriggerV1:
    handle = timer.current(
        db,
        next(
            identity
            for identity in timer.identities
            if identity.entity_id == str(case_id)
        ),
    )
    assert handle is not None
    return TimerTriggerV1(
        timer_id=handle.timer_id,
        identity=handle.identity,
        generation=handle.generation,
        due_at=handle.due_at,
        output_event_type=handle.output_event_type,
        expected_source_version=handle.expected_source_version,
    )


def test_case_open_schedules_and_resolution_cancels_the_exact_timer(
    db: Session,
) -> None:
    policy_version_id = _policy(db)
    timer = FakeCollectionsTimer()
    opened = CollectionCaseService.assess(
        db,
        command=_command(),
        policy_version_id=policy_version_id,
        reader=_reader(PositionReadOk(_position(version=1, amount="100.00"))),
        timer=timer,
        assessed_at=NOW,
    )
    assert isinstance(opened, CaseAssessed)
    trigger = _trigger(timer, db, opened.case_id)
    assert trigger.due_at == NOW
    assert trigger.output_event_type == STEP_DUE_EVENT_TYPE
    assert trigger.expected_source_version == 1

    closed = CollectionCaseService.assess(
        db,
        command=_command(),
        policy_version_id=policy_version_id,
        reader=_reader(
            PositionReadOk(
                _position(version=2, amount="0.00", financial_state="resolved")
            )
        ),
        timer=timer,
        assessed_at=NOW + timedelta(minutes=1),
    )
    assert isinstance(closed, CaseAssessed)
    assert closed.lifecycle == "resolved"
    assert timer.current(db, trigger.identity) is None


def test_due_timer_rereads_and_receipt_schedules_the_next_policy_step(
    db: Session,
) -> None:
    policy_version_id = _two_step_policy(db)
    timer = FakeCollectionsTimer()
    reader = _reader(PositionReadOk(_position(version=1, amount="100.00")))
    opened = CollectionCaseService.assess(
        db,
        command=_command(),
        policy_version_id=policy_version_id,
        reader=reader,
        timer=timer,
        assessed_at=NOW,
    )
    assert isinstance(opened, CaseAssessed)
    first_trigger = _trigger(timer, db, opened.case_id)

    requested = CollectionCaseService.process_step_due(
        db,
        command=ProcessCollectionStepDueV1(
            scope=SCOPE,
            trigger=first_trigger,
            processed_at=NOW,
        ),
        reader=reader,
        timer=timer,
    )
    assert isinstance(requested, StepRequestWritten)
    assert requested.policy_step_code == "restrict"
    assert requested.request_kind == "action"
    assert requested.attempt_ordinal == 1
    assert len(reader.calls) == 2
    assert len(db.scalars(select(CollectionActionRequest)).all()) == 1
    assert len(db.scalars(select(OutboxEvent)).all()) == 1

    replay = CollectionCaseService.process_step_due(
        db,
        command=ProcessCollectionStepDueV1(
            scope=SCOPE,
            trigger=first_trigger,
            processed_at=NOW,
        ),
        reader=reader,
        timer=timer,
    )
    assert replay == replace(requested, replayed=True)
    assert len(reader.calls) == 2

    CollectionActionService.record_receipt(
        db,
        scope=SCOPE,
        receipt=ActionApplied(
            request_id=requested.request_id,
            owner_code="subscriptions.access",
            owner_receipt_id="receipt:timer-1",
            action_ref="restriction:timer-1",
            applied_at=NOW + timedelta(minutes=5),
            owner_state_fingerprint="sha256:access-v2",
        ),
        timer=timer,
    )
    second_trigger = _trigger(timer, db, opened.case_id)
    assert second_trigger.generation == first_trigger.generation + 1
    assert second_trigger.due_at == NOW + timedelta(hours=1)


def test_due_timer_source_outage_replaces_timer_without_emitting_request(
    db: Session,
) -> None:
    policy_version_id = _policy(db)
    timer = FakeCollectionsTimer()
    opened = CollectionCaseService.assess(
        db,
        command=_command(),
        policy_version_id=policy_version_id,
        reader=_reader(PositionReadOk(_position(version=1, amount="100.00"))),
        timer=timer,
        assessed_at=NOW,
    )
    assert isinstance(opened, CaseAssessed)
    first_trigger = _trigger(timer, db, opened.case_id)
    retry_at = NOW + timedelta(minutes=5)
    unavailable = _reader(
        PositionUnavailable(reason_code="billing_cutover", retry_after=retry_at)
    )

    result = CollectionCaseService.process_step_due(
        db,
        command=ProcessCollectionStepDueV1(
            scope=SCOPE,
            trigger=first_trigger,
            processed_at=NOW,
        ),
        reader=unavailable,
        timer=timer,
    )
    assert result == StepDueBlocked(
        case_id=opened.case_id,
        reason_code="billing_cutover",
        retry_at=retry_at,
        replayed=False,
    )
    replacement = _trigger(timer, db, opened.case_id)
    assert replacement.generation == first_trigger.generation + 1
    assert replacement.due_at == retry_at
    assert db.scalars(select(CollectionActionRequest)).all() == []
    assert db.scalars(select(CollectionNoticeRequest)).all() == []
    assert db.scalars(select(OutboxEvent)).all() == []


def test_retryable_owner_failure_uses_the_versioned_policy_retry_ladder(
    db: Session,
) -> None:
    policy_version_id = _policy(db)
    timer = FakeCollectionsTimer()
    reader = _reader(PositionReadOk(_position(version=1, amount="100.00")))
    opened = CollectionCaseService.assess(
        db,
        command=_command(),
        policy_version_id=policy_version_id,
        reader=reader,
        timer=timer,
        assessed_at=NOW,
    )
    assert isinstance(opened, CaseAssessed)
    first_trigger = _trigger(timer, db, opened.case_id)
    first = CollectionCaseService.process_step_due(
        db,
        command=ProcessCollectionStepDueV1(
            scope=SCOPE,
            trigger=first_trigger,
            processed_at=NOW,
        ),
        reader=reader,
        timer=timer,
    )
    assert isinstance(first, StepRequestWritten)

    CollectionActionService.record_receipt(
        db,
        scope=SCOPE,
        receipt=ActionFailed(
            request_id=first.request_id,
            owner_code="subscriptions.access",
            owner_receipt_id="receipt:failed-1",
            reason_code="owner_temporarily_unavailable",
            observed_at=NOW + timedelta(minutes=1),
            retryable=True,
        ),
        timer=timer,
    )
    retry_trigger = _trigger(timer, db, opened.case_id)
    assert retry_trigger.generation == first_trigger.generation + 1
    assert retry_trigger.due_at == NOW + timedelta(minutes=6)

    second = CollectionCaseService.process_step_due(
        db,
        command=ProcessCollectionStepDueV1(
            scope=SCOPE,
            trigger=retry_trigger,
            processed_at=retry_trigger.due_at,
        ),
        reader=reader,
        timer=timer,
    )
    assert isinstance(second, StepRequestWritten)
    assert second.policy_step_code == first.policy_step_code
    assert second.attempt_ordinal == 2
    assert second.request_id != first.request_id


def test_service_is_flush_only_and_constructs_no_session() -> None:
    path = (
        Path(__file__).resolve().parents[2]
        / "packages/dotmac-collections/src/dotmac_collections/service.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {"commit", "rollback", "close"}.isdisjoint(calls)
    assert "sessionmaker" not in path.read_text(encoding="utf-8")
