"""Flush-only owner of resumable workflow execution and repair evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_workflow_runtime.contracts import (
    RepairCommand,
    SettleCheckpoint,
    StartExecution,
)
from dotmac_workflow_runtime.models import (
    WorkflowCheckpoint,
    WorkflowExecution,
    WorkflowRepair,
)


class WorkflowError(ValueError):
    """A runtime command cannot be admitted."""


class WorkflowConflict(WorkflowError):
    """A stable runtime identity was reused with different content."""


class ExecutionUnavailable(WorkflowError):
    """A tenant-local execution is missing or terminal."""


class CheckpointUnavailable(WorkflowError):
    """A checkpoint cannot be claimed or settled in its current state."""


@dataclass(frozen=True, slots=True)
class ExecutionReceipt:
    execution: WorkflowExecution
    replayed: bool = False


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _instant(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _execution(db: Session, tenant_id: UUID, execution_id: UUID) -> WorkflowExecution:
    row = db.scalar(
        select(WorkflowExecution)
        .where(
            WorkflowExecution.tenant_id == tenant_id,
            WorkflowExecution.id == execution_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ExecutionUnavailable("workflow execution not found")
    return row


def start_execution(
    db: Session,
    *,
    tenant_id: UUID,
    command: StartExecution,
    started_at: datetime,
) -> ExecutionReceipt:
    _aware("started_at", started_at)
    identity = (
        WorkflowExecution.tenant_id == tenant_id,
        WorkflowExecution.source_owner == command.source_owner,
        WorkflowExecution.source_event_id == command.source_event_id,
    )
    existing = db.scalar(select(WorkflowExecution).where(*identity))
    if existing is not None:
        if existing.request_fingerprint != command.request_fingerprint:
            raise WorkflowConflict(
                "source event was replayed with a different fingerprint"
            )
        return ExecutionReceipt(existing, replayed=True)
    row = WorkflowExecution(
        tenant_id=tenant_id,
        definition_version_ref=command.definition_version_ref,
        definition_digest=command.definition_digest,
        subject_ref=command.subject_ref,
        source_owner=command.source_owner,
        source_event_id=command.source_event_id,
        request_fingerprint=command.request_fingerprint,
        status="pending",
        started_at=started_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        replay = db.scalar(select(WorkflowExecution).where(*identity))
        if (
            replay is not None
            and replay.request_fingerprint == command.request_fingerprint
        ):
            return ExecutionReceipt(replay, replayed=True)
        raise WorkflowConflict("workflow execution identity conflicts") from exc
    db.add_all(
        [
            WorkflowCheckpoint(
                tenant_id=tenant_id,
                execution_id=row.id,
                code=code,
                position=position,
                status="pending",
                attempt_count=0,
                max_attempts=command.max_attempts,
            )
            for position, code in enumerate(command.checkpoint_codes, start=1)
        ]
    )
    db.flush()
    return ExecutionReceipt(row)


def claim_checkpoint(
    db: Session,
    *,
    tenant_id: UUID,
    execution_id: UUID,
    checkpoint_code: str,
    worker_ref: str,
    claimed_at: datetime,
    lease_until: datetime,
) -> WorkflowCheckpoint:
    _aware("claimed_at", claimed_at)
    _aware("lease_until", lease_until)
    if lease_until <= claimed_at:
        raise CheckpointUnavailable("checkpoint lease must be finite and future")
    execution = _execution(db, tenant_id, execution_id)
    if execution.status in {"succeeded", "cancelled"}:
        raise ExecutionUnavailable("workflow execution is terminal")
    checkpoint = db.scalar(
        select(WorkflowCheckpoint)
        .where(
            WorkflowCheckpoint.tenant_id == tenant_id,
            WorkflowCheckpoint.execution_id == execution_id,
            WorkflowCheckpoint.code == checkpoint_code.strip().lower(),
        )
        .with_for_update()
    )
    if checkpoint is None:
        raise CheckpointUnavailable("workflow checkpoint not found")
    earlier = db.scalar(
        select(WorkflowCheckpoint.id).where(
            WorkflowCheckpoint.tenant_id == tenant_id,
            WorkflowCheckpoint.execution_id == execution_id,
            WorkflowCheckpoint.position < checkpoint.position,
            WorkflowCheckpoint.status != "succeeded",
        )
    )
    if earlier is not None:
        raise CheckpointUnavailable("an earlier checkpoint is incomplete")
    if checkpoint.status == "succeeded":
        raise CheckpointUnavailable("checkpoint is already settled")
    if checkpoint.status == "failed":
        raise CheckpointUnavailable("failed checkpoint requires explicit repair")
    if (
        checkpoint.status == "leased"
        and checkpoint.lease_expires_at is not None
        and _instant(checkpoint.lease_expires_at) > _instant(claimed_at)
    ):
        raise CheckpointUnavailable("checkpoint already has an active lease")
    if checkpoint.attempt_count >= checkpoint.max_attempts:
        checkpoint.status = "failed"
        execution.status = "failed"
        execution.completed_at = claimed_at
        db.flush()
        raise CheckpointUnavailable("checkpoint exhausted its attempts")
    checkpoint.status = "leased"
    checkpoint.attempt_count += 1
    checkpoint.lease_owner_ref = worker_ref.strip()
    checkpoint.lease_expires_at = lease_until
    checkpoint.error_code = None
    checkpoint.settled_at = None
    execution.status = "running"
    execution.completed_at = None
    db.flush()
    return checkpoint


def settle_checkpoint(
    db: Session,
    *,
    tenant_id: UUID,
    command: SettleCheckpoint,
    settled_at: datetime,
) -> WorkflowCheckpoint:
    _aware("settled_at", settled_at)
    checkpoint = db.scalar(
        select(WorkflowCheckpoint)
        .where(
            WorkflowCheckpoint.tenant_id == tenant_id,
            WorkflowCheckpoint.id == command.checkpoint_id,
        )
        .with_for_update()
    )
    if checkpoint is None or checkpoint.status != "leased":
        raise CheckpointUnavailable("checkpoint does not have an active lease")
    if checkpoint.lease_owner_ref != command.worker_ref:
        raise CheckpointUnavailable("checkpoint lease belongs to another worker")
    if checkpoint.lease_expires_at is None or _instant(
        checkpoint.lease_expires_at
    ) <= _instant(settled_at):
        raise CheckpointUnavailable("checkpoint lease expired before settlement")
    execution = _execution(db, tenant_id, checkpoint.execution_id)
    checkpoint.lease_owner_ref = None
    checkpoint.lease_expires_at = None
    checkpoint.settled_at = settled_at
    checkpoint.output_ref = command.output_ref
    checkpoint.output_digest = command.output_digest
    checkpoint.error_code = command.error_code
    if command.outcome == "failed":
        if checkpoint.attempt_count < checkpoint.max_attempts:
            checkpoint.status = "retryable"
            execution.status = "running"
        else:
            checkpoint.status = "failed"
            execution.status = "failed"
            execution.completed_at = settled_at
        db.flush()
        return checkpoint
    checkpoint.status = "succeeded"
    remaining = db.scalar(
        select(WorkflowCheckpoint.id).where(
            WorkflowCheckpoint.tenant_id == tenant_id,
            WorkflowCheckpoint.execution_id == execution.id,
            WorkflowCheckpoint.id != checkpoint.id,
            WorkflowCheckpoint.status != "succeeded",
        )
    )
    if remaining is None:
        execution.status = "succeeded"
        execution.completed_at = settled_at
    else:
        execution.status = "running"
    db.flush()
    return checkpoint


def record_repair(
    db: Session,
    *,
    tenant_id: UUID,
    command: RepairCommand,
    repaired_at: datetime,
) -> WorkflowRepair:
    _aware("repaired_at", repaired_at)
    execution = _execution(db, tenant_id, command.execution_id)
    checkpoint = db.scalar(
        select(WorkflowCheckpoint)
        .where(
            WorkflowCheckpoint.tenant_id == tenant_id,
            WorkflowCheckpoint.execution_id == execution.id,
            WorkflowCheckpoint.code == command.checkpoint_code,
        )
        .with_for_update()
    )
    if (
        execution.status != "failed"
        or checkpoint is None
        or checkpoint.status != "failed"
    ):
        raise CheckpointUnavailable("only a terminal failed checkpoint may be repaired")
    row = WorkflowRepair(
        tenant_id=tenant_id,
        execution_id=execution.id,
        checkpoint_id=checkpoint.id,
        reason=command.reason,
        evidence_digest=command.evidence_digest,
        repaired_by_ref=command.repaired_by_ref,
        repaired_at=repaired_at,
    )
    db.add(row)
    checkpoint.status = "pending"
    checkpoint.attempt_count = 0
    checkpoint.error_code = None
    checkpoint.output_ref = None
    checkpoint.output_digest = None
    checkpoint.settled_at = None
    execution.status = "running"
    execution.completed_at = None
    db.flush()
    return row


__all__ = [
    "CheckpointUnavailable",
    "ExecutionReceipt",
    "ExecutionUnavailable",
    "WorkflowConflict",
    "WorkflowError",
    "claim_checkpoint",
    "record_repair",
    "settle_checkpoint",
    "start_execution",
]
