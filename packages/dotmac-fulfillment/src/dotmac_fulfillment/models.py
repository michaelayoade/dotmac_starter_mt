"""Tenant-scoped, append-only fulfillment saga evidence."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
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
    func,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("fulfillment")

TENANT_TABLES: tuple[str, ...] = (
    "fulfillment_runs",
    "fulfillment_steps",
    "fulfillment_attempts",
    "fulfillment_outcome_receipts",
    "fulfillment_compensation_requests",
    "fulfillment_compensation_receipts",
)

# The engine is event-sourced: even run/step definitions are immutable after
# creation. Progress is derived from attempts and receipts, never maintained by
# rewriting a summary column.
APPEND_ONLY_TABLES: tuple[str, ...] = TENANT_TABLES

_JSON = JSON().with_variant(postgresql.JSONB(), "postgresql")


def _tenant_id() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class FulfillmentRun(Base):
    __tablename__ = "fulfillment_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fulfillment_runs_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "intent_ref", name="uq_fulfillment_runs_tenant_intent"
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_fulfillment_runs_tenant_idempotency",
        ),
        Index("ix_fulfillment_runs_tenant_created", "tenant_id", "created_at"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    intent_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FulfillmentStep(Base):
    __tablename__ = "fulfillment_steps"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [f"{SCHEMA}.fulfillment_runs.tenant_id", f"{SCHEMA}.fulfillment_runs.id"],
            name="fk_fulfillment_steps_run",
            ondelete="CASCADE",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_fulfillment_steps_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "id",
            name="uq_fulfillment_steps_tenant_run_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            name="uq_fulfillment_steps_run_step",
        ),
        UniqueConstraint(
            "tenant_id", "run_id", "sequence", name="uq_fulfillment_steps_run_sequence"
        ),
        Index("ix_fulfillment_steps_participant", "tenant_id", "participant_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    run_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    step_id: Mapped[str] = mapped_column(String(120), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    participant_code: Mapped[str] = mapped_column(String(120), nullable=False)
    command_type: Mapped[str] = mapped_column(String(120), nullable=False)
    line_ref: Mapped[str | None] = mapped_column(String(255))
    spec: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    spec_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


class FulfillmentAttempt(Base):
    __tablename__ = "fulfillment_attempts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id", "step_id"],
            [
                f"{SCHEMA}.fulfillment_steps.tenant_id",
                f"{SCHEMA}.fulfillment_steps.run_id",
                f"{SCHEMA}.fulfillment_steps.id",
            ],
            name="fk_fulfillment_attempts_step",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_attempts_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            "sequence",
            name="uq_fulfillment_attempts_step_sequence",
        ),
        UniqueConstraint(
            "tenant_id", "command_id", name="uq_fulfillment_attempts_command"
        ),
        UniqueConstraint(
            "tenant_id", "idempotency_key", name="uq_fulfillment_attempts_idempotency"
        ),
        Index(
            "ix_fulfillment_attempts_latest",
            "tenant_id",
            "run_id",
            "step_id",
            "sequence",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    run_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    step_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    command_id: Mapped[str] = mapped_column(String(200), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String(200))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class FulfillmentOutcomeReceipt(Base):
    __tablename__ = "fulfillment_outcome_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "attempt_id"],
            [
                f"{SCHEMA}.fulfillment_attempts.tenant_id",
                f"{SCHEMA}.fulfillment_attempts.id",
            ],
            name="fk_fulfillment_outcomes_attempt",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_outcomes_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "attempt_id", name="uq_fulfillment_outcomes_attempt"
        ),
        UniqueConstraint(
            "tenant_id", "outcome_id", name="uq_fulfillment_outcomes_identity"
        ),
        UniqueConstraint(
            "tenant_id",
            "participant_code",
            "command_id",
            name="uq_fulfillment_outcomes_participant_command",
        ),
        CheckConstraint(
            "(reviewed_by_type IS NULL) = (reviewed_by_id IS NULL)",
            name="ck_fulfillment_outcomes_reviewer_pair",
        ),
        Index("ix_fulfillment_outcomes_run", "tenant_id", "run_id", "step_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    run_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    step_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    attempt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    outcome_id: Mapped[str] = mapped_column(String(200), nullable=False)
    participant_code: Mapped[str] = mapped_column(String(120), nullable=False)
    command_id: Mapped[str] = mapped_column(String(200), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    classification: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_status: Mapped[str | None] = mapped_column(String(120))
    error_class: Mapped[str | None] = mapped_column(String(120))
    reason_code: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    reviewed_by_type: Mapped[str | None] = mapped_column(String(32))
    reviewed_by_id: Mapped[str | None] = mapped_column(String(120))


class FulfillmentCompensationRequest(Base):
    __tablename__ = "fulfillment_compensation_requests"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "original_attempt_id"],
            [
                f"{SCHEMA}.fulfillment_attempts.tenant_id",
                f"{SCHEMA}.fulfillment_attempts.id",
            ],
            name="fk_fulfillment_comp_requests_attempt",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_comp_requests_tenant_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "original_attempt_id",
            name="uq_fulfillment_comp_requests_attempt",
        ),
        UniqueConstraint(
            "tenant_id", "command_id", name="uq_fulfillment_comp_requests_command"
        ),
        UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_fulfillment_comp_requests_idempotency",
        ),
        Index("ix_fulfillment_comp_requests_run", "tenant_id", "run_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    run_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    step_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    original_attempt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    participant_code: Mapped[str] = mapped_column(String(120), nullable=False)
    command_id: Mapped[str] = mapped_column(String(200), nullable=False)
    operation_id: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class FulfillmentCompensationReceipt(Base):
    __tablename__ = "fulfillment_compensation_receipts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                f"{SCHEMA}.fulfillment_compensation_requests.tenant_id",
                f"{SCHEMA}.fulfillment_compensation_requests.id",
            ],
            name="fk_fulfillment_comp_receipts_request",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_comp_receipts_tenant_id"
        ),
        UniqueConstraint(
            "tenant_id", "request_id", name="uq_fulfillment_comp_receipts_request"
        ),
        UniqueConstraint(
            "tenant_id", "outcome_id", name="uq_fulfillment_comp_receipts_outcome"
        ),
        UniqueConstraint(
            "tenant_id",
            "participant_code",
            "command_id",
            name="uq_fulfillment_comp_receipts_participant_command",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    request_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    outcome_id: Mapped[str] = mapped_column(String(200), nullable=False)
    participant_code: Mapped[str] = mapped_column(String(120), nullable=False)
    command_id: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(120))
    detail: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


TENANT_MODELS = (
    FulfillmentRun,
    FulfillmentStep,
    FulfillmentAttempt,
    FulfillmentOutcomeReceipt,
    FulfillmentCompensationRequest,
    FulfillmentCompensationReceipt,
)


__all__ = [
    "APPEND_ONLY_TABLES",
    "FulfillmentAttempt",
    "FulfillmentCompensationReceipt",
    "FulfillmentCompensationRequest",
    "FulfillmentOutcomeReceipt",
    "FulfillmentRun",
    "FulfillmentStep",
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
]
