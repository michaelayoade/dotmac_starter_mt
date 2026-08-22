"""Service-change request behavior canaries ported from Sub."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from dotmac_service_changes import models
from dotmac_service_changes.contracts import (
    AdvanceExecution,
    CheckpointDomain,
    Conflict,
    DecideServiceChange,
    ExecutionState,
    OpenServiceChange,
    RecordCheckpoint,
    ServiceChangeStatus,
    ServiceChangeType,
)
from dotmac_service_changes.models import (
    TENANT_TABLES,
    ServiceChangeCheckpointImmutableError,
)
from dotmac_service_changes.service import (
    advance_execution,
    decide_service_change,
    open_service_change,
    record_checkpoint,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
NOW = datetime(2026, 8, 22, tzinfo=UTC)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_servicechanges": None}},
    )
    Tenant.__table__.create(engine)
    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="a", name="A"),
                Tenant(id=TENANT_B, slug="b", name="B"),
            ]
        )
        session.flush()
        yield session
    engine.dispose()


def _open(db: Session, scope: TenantScope, key: str = "confirm-1"):
    return open_service_change(
        db,
        scope=scope,
        command=OpenServiceChange(
            subject_reference="subscription:1",
            change_type=ServiceChangeType.PLAN_CHANGE,
            confirmation_key=key,
            current_offer_reference="offer:basic",
            requested_offer_reference="offer:premium",
        ),
    )


def _approved(db: Session, scope: TenantScope, key: str = "confirm-1"):
    request = _open(db, scope, key)
    return decide_service_change(
        db,
        scope=scope,
        command=DecideServiceChange(
            request_id=request.id,
            approve=True,
            actor="agent:1",
            rationale="customer confirmed the upgrade",
            decided_at=NOW,
        ),
    )


def test_opening_is_idempotent_on_the_confirmation_key(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    first = _open(db, scope)
    assert _open(db, scope).id == first.id
    with pytest.raises(Conflict):
        open_service_change(
            db,
            scope=scope,
            command=OpenServiceChange(
                subject_reference="subscription:2",
                change_type=ServiceChangeType.PLAN_CHANGE,
                confirmation_key="confirm-1",
            ),
        )


def test_approval_starts_the_execution_chain_and_rejection_settles(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    approved = _approved(db, scope)
    assert approved.status is ServiceChangeStatus.APPROVED
    assert approved.execution_state is ExecutionState.AWAITING_PAYMENT

    rejected_request = _open(db, scope, key="confirm-2")
    rejected = decide_service_change(
        db,
        scope=scope,
        command=DecideServiceChange(
            request_id=rejected_request.id,
            approve=False,
            actor="agent:1",
            rationale="not serviceable at the target address",
            decided_at=NOW,
        ),
    )
    assert rejected.status is ServiceChangeStatus.REJECTED
    assert rejected.execution_state is None
    assert rejected.settled_at == NOW


def test_execution_cannot_skip_a_step(db: Session) -> None:
    """The defect this module exists not to repeat: Sub's execution state was
    written by several handlers with no single guard, so a request could reach
    `fulfillment_released` with no settlement ever recorded."""
    scope = TenantScope(TENANT_A)
    request = _approved(db, scope)
    with pytest.raises(Conflict):
        advance_execution(
            db,
            scope=scope,
            command=AdvanceExecution(
                request_id=request.id,
                to_state=ExecutionState.FULFILLMENT_RELEASED,
                reason_code="skipped",
            ),
        )
    advance_execution(
        db,
        scope=scope,
        command=AdvanceExecution(
            request_id=request.id,
            to_state=ExecutionState.PAYMENT_SETTLED,
            reason_code="payment_confirmed",
        ),
    )
    assert request.execution_state is ExecutionState.PAYMENT_SETTLED


def test_completing_the_chain_applies_the_request(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    request = _approved(db, scope)
    for state in (
        ExecutionState.PAYMENT_SETTLED,
        ExecutionState.FULFILLMENT_RELEASED,
        ExecutionState.DELIVERY_IN_PROGRESS,
        ExecutionState.DELIVERY_VERIFIED,
        ExecutionState.COMPLETED,
    ):
        advance_execution(
            db,
            scope=scope,
            command=AdvanceExecution(
                request_id=request.id, to_state=state, reason_code="step", at=NOW
            ),
        )
    assert request.execution_state is ExecutionState.COMPLETED
    assert request.status is ServiceChangeStatus.APPLIED
    assert request.settled_at == NOW


def test_failure_is_reachable_from_anywhere_and_is_terminal(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    request = _approved(db, scope)
    advance_execution(
        db,
        scope=scope,
        command=AdvanceExecution(
            request_id=request.id,
            to_state=ExecutionState.FAILED,
            reason_code="payment_reversed",
            at=NOW,
        ),
    )
    assert request.execution_state is ExecutionState.FAILED
    with pytest.raises(Conflict):
        advance_execution(
            db,
            scope=scope,
            command=AdvanceExecution(
                request_id=request.id,
                to_state=ExecutionState.PAYMENT_SETTLED,
                reason_code="retry",
            ),
        )


def test_checkpoints_are_typed_idempotent_and_immutable(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    request = _approved(db, scope)
    command = RecordCheckpoint(
        request_id=request.id,
        domain=CheckpointDomain.QUALIFICATION,
        evidence_reference="qual:decision-1",
        facts={"outcome": "ELIGIBLE"},
        observed_at=NOW,
    )
    first = record_checkpoint(db, scope=scope, command=command)
    assert record_checkpoint(db, scope=scope, command=command).id == first.id

    second = record_checkpoint(
        db,
        scope=scope,
        command=RecordCheckpoint(
            request_id=request.id,
            domain=CheckpointDomain.QUALIFICATION,
            evidence_reference="qual:decision-2",
            facts={"outcome": "ELIGIBLE"},
            observed_at=NOW,
        ),
    )
    assert second.id != first.id

    first.evidence_reference = "rewritten"
    with pytest.raises(ServiceChangeCheckpointImmutableError):
        db.flush()
    db.expunge_all()


def test_a_checkpoint_needs_facts(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    request = _approved(db, scope)
    with pytest.raises(Conflict):
        record_checkpoint(
            db,
            scope=scope,
            command=RecordCheckpoint(
                request_id=request.id,
                domain=CheckpointDomain.BILLING,
                evidence_reference="invoice:1",
                facts={},
            ),
        )


def test_another_tenants_request_is_not_visible(db: Session) -> None:
    request = _approved(db, TenantScope(TENANT_A))
    with pytest.raises(Conflict):
        record_checkpoint(
            db,
            scope=TenantScope(TENANT_B),
            command=RecordCheckpoint(
                request_id=request.id,
                domain=CheckpointDomain.BILLING,
                evidence_reference="invoice:1",
                facts={"total": "1"},
            ),
        )
