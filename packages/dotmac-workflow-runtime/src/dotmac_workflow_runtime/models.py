"""Tenant-only persistence for resumable workflow execution."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("workflow")


def tenant_id_column() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class WorkflowExecution(Base):
    __tablename__ = "workflow_executions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workflow_executions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_workflow_executions_source_event",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_executions_status",
        ),
        Index("ix_workflow_executions_tenant_subject", "tenant_id", "subject_ref"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    definition_version_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    definition_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkflowCheckpoint(Base):
    __tablename__ = "workflow_checkpoints"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_workflow_checkpoints_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "execution_id",
            "code",
            name="uq_workflow_checkpoints_execution_code",
        ),
        UniqueConstraint(
            "tenant_id",
            "execution_id",
            "position",
            name="uq_workflow_checkpoints_execution_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "execution_id"],
            [
                f"{SCHEMA}.workflow_executions.tenant_id",
                f"{SCHEMA}.workflow_executions.id",
            ],
            ondelete="CASCADE",
            name="fk_workflow_checkpoints_execution",
        ),
        CheckConstraint(
            "status IN ('pending', 'leased', 'retryable', 'succeeded', 'failed')",
            name="ck_workflow_checkpoints_status",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_workflow_checkpoints_attempts",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    execution_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    lease_owner_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    output_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    output_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WorkflowRepair(Base):
    __tablename__ = "workflow_repairs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workflow_repairs_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "execution_id"],
            [
                f"{SCHEMA}.workflow_executions.tenant_id",
                f"{SCHEMA}.workflow_executions.id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_repairs_execution",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "checkpoint_id"],
            [
                f"{SCHEMA}.workflow_checkpoints.tenant_id",
                f"{SCHEMA}.workflow_checkpoints.id",
            ],
            ondelete="RESTRICT",
            name="fk_workflow_repairs_checkpoint",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    execution_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    checkpoint_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    repaired_by_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    repaired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_MODELS = (WorkflowExecution, WorkflowCheckpoint, WorkflowRepair)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "WorkflowCheckpoint",
    "WorkflowExecution",
    "WorkflowRepair",
]
