"""Resumable checkpoint behavior extracted from ERP's workflow execution owner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_workflow_runtime import (
    CheckpointUnavailable,
    RepairCommand,
    SettleCheckpoint,
    StartExecution,
    WorkflowConflict,
    claim_checkpoint,
    record_repair,
    settle_checkpoint,
    start_execution,
)
from dotmac_workflow_runtime.models import TENANT_MODELS, WorkflowRepair
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

DIGEST = "a" * 64


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_workflow": None}},
    )
    Base.metadata.create_all(
        engine, tables=[Tenant.__table__, *(m.__table__ for m in TENANT_MODELS)]
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tenant(db: Session) -> Tenant:
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(row)
    db.flush()
    return row


def _start(db: Session, tenant_id):
    return start_execution(
        db,
        tenant_id=tenant_id,
        command=StartExecution(
            definition_version_ref="workflow:expense:v3",
            definition_digest=DIGEST,
            subject_ref="expense:42",
            source_owner="expenses",
            source_event_id="expense-submitted:42",
            request_fingerprint="b" * 64,
            checkpoint_codes=("validate", "request_approval"),
            max_attempts=2,
        ),
        started_at=datetime(2026, 8, 21, 8, tzinfo=UTC),
    )


def test_execution_identity_replays_only_with_the_same_fingerprint(db: Session) -> None:
    tenant = _tenant(db)
    first = _start(db, tenant.id)
    replay = _start(db, tenant.id)
    assert replay.replayed is True
    assert replay.execution.id == first.execution.id

    with pytest.raises(WorkflowConflict, match="different fingerprint"):
        start_execution(
            db,
            tenant_id=tenant.id,
            command=StartExecution(
                definition_version_ref="workflow:expense:v3",
                definition_digest=DIGEST,
                subject_ref="expense:42",
                source_owner="expenses",
                source_event_id="expense-submitted:42",
                request_fingerprint="c" * 64,
                checkpoint_codes=("validate",),
            ),
            started_at=datetime(2026, 8, 21, 8, tzinfo=UTC),
        )


def test_checkpoints_are_claimed_in_order_and_complete_the_execution(
    db: Session,
) -> None:
    tenant = _tenant(db)
    receipt = _start(db, tenant.id)
    at = datetime(2026, 8, 21, 8, 1, tzinfo=UTC)
    with pytest.raises(CheckpointUnavailable, match="earlier"):
        claim_checkpoint(
            db,
            tenant_id=tenant.id,
            execution_id=receipt.execution.id,
            checkpoint_code="request_approval",
            worker_ref="worker:1",
            claimed_at=at,
            lease_until=at + timedelta(minutes=5),
        )

    first = claim_checkpoint(
        db,
        tenant_id=tenant.id,
        execution_id=receipt.execution.id,
        checkpoint_code="validate",
        worker_ref="worker:1",
        claimed_at=at,
        lease_until=at + timedelta(minutes=5),
    )
    settle_checkpoint(
        db,
        tenant_id=tenant.id,
        command=SettleCheckpoint(
            first.id,
            "worker:1",
            "succeeded",
            output_ref="validation:ok",
            output_digest="d" * 64,
        ),
        settled_at=at + timedelta(seconds=1),
    )
    second = claim_checkpoint(
        db,
        tenant_id=tenant.id,
        execution_id=receipt.execution.id,
        checkpoint_code="request_approval",
        worker_ref="worker:2",
        claimed_at=at + timedelta(seconds=2),
        lease_until=at + timedelta(minutes=5),
    )
    settle_checkpoint(
        db,
        tenant_id=tenant.id,
        command=SettleCheckpoint(
            second.id,
            "worker:2",
            "succeeded",
            output_ref="approval:intent:42",
            output_digest="e" * 64,
        ),
        settled_at=at + timedelta(seconds=3),
    )
    assert receipt.execution.status == "succeeded"
    assert receipt.execution.completed_at == at + timedelta(seconds=3)


def test_failed_checkpoint_retries_then_requires_explicit_repair(db: Session) -> None:
    tenant = _tenant(db)
    receipt = _start(db, tenant.id)
    at = datetime(2026, 8, 21, 8, 1, tzinfo=UTC)
    checkpoint = claim_checkpoint(
        db,
        tenant_id=tenant.id,
        execution_id=receipt.execution.id,
        checkpoint_code="validate",
        worker_ref="worker:1",
        claimed_at=at,
        lease_until=at + timedelta(minutes=5),
    )
    settle_checkpoint(
        db,
        tenant_id=tenant.id,
        command=SettleCheckpoint(
            checkpoint.id, "worker:1", "failed", error_code="invalid"
        ),
        settled_at=at + timedelta(seconds=1),
    )
    assert checkpoint.status == "retryable"
    checkpoint = claim_checkpoint(
        db,
        tenant_id=tenant.id,
        execution_id=receipt.execution.id,
        checkpoint_code="validate",
        worker_ref="worker:2",
        claimed_at=at + timedelta(seconds=2),
        lease_until=at + timedelta(minutes=5),
    )
    settle_checkpoint(
        db,
        tenant_id=tenant.id,
        command=SettleCheckpoint(
            checkpoint.id, "worker:2", "failed", error_code="still-invalid"
        ),
        settled_at=at + timedelta(seconds=3),
    )
    assert receipt.execution.status == "failed"

    repaired = record_repair(
        db,
        tenant_id=tenant.id,
        command=RepairCommand(
            receipt.execution.id,
            "validate",
            "source evidence corrected",
            "f" * 64,
            "operator:7",
        ),
        repaired_at=at + timedelta(minutes=1),
    )
    assert checkpoint.status == "pending"
    assert receipt.execution.status == "running"
    assert (
        db.scalar(select(WorkflowRepair).where(WorkflowRepair.id == repaired.id))
        is repaired
    )
