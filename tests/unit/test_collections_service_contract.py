"""Canonical Collections writers over persisted tenant facts."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from dotmac_collections.actions import ActionApplied, ActionReceiptConflict
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
)
from dotmac_collections.policies import (
    PolicyPublicationV1,
    PolicyStepDraftV1,
    PolicyVersionDraftV1,
)
from dotmac_collections.receivables import (
    FakeReceivablesReader,
    PositionReadOk,
    PositionUnavailable,
    ReceivablePositionV1,
)
from dotmac_collections.service import (
    AssessmentBlocked,
    CaseAssessed,
    CollectionActionService,
    CollectionCaseService,
    CollectionPolicyService,
    CreateCollectionPolicyV1,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.money import Currency, Money
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)
TENANT_ID = UUID("d0a99cb7-721d-4a14-8238-46ef3df0068b")
SCOPE = TenantScope(TENANT_ID)
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


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_coll": None}},
    )
    IdempotencyRecord.__table__.create(engine)
    for model in COLLECTION_TABLES:
        model.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _policy(db: Session) -> UUID:
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
                    request_kind="action",
                    action_code="restrict_service",
                    receipt_required=True,
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
    *, version: int, amount: str, resolution: str = "open"
) -> ReceivablePositionV1:
    return ReceivablePositionV1(
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
        available_credit=Money.zero(NGN),
        funding_available=Money.zero(NGN),
        due_at=NOW - timedelta(days=1),
        coverage_start_at=None,
        resolution=resolution,  # type: ignore[arg-type]
        authority="authoritative",
        completeness="complete",
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
            PositionReadOk(_position(version=2, amount="0.00", resolution="resolved"))
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
    from dotmac_collections.actions import CollectionActionRequestedV1

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
