"""Platform-only durable state for approval-bound provisioning execution."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.exceptions import ConflictError
from dotmac_kernel.models import Base, uuid_pk
from dotmac_kernel.namespaces import schema_table_args
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from dotmac_integration.models import SCHEMA

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

PROVISIONING_PLATFORM_TABLES: tuple[str, ...] = (
    "provisioning_commands",
    "provisioning_command_receipts",
    "provisioning_operations",
    "provisioning_steps",
    "provisioning_receipts",
)


class ProvisioningOperation(Base):
    """One accepted command identity and its current derived execution state."""

    __tablename__ = "provisioning_operations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'in_flight', 'observing', 'retryable', "
            "'observe_in_flight', 'observe_retryable', 'cancel_in_flight', "
            "'cancel_retryable', "
            "'succeeded', 'terminal', 'reconciliation_required', 'cancelled')",
            name="ck_provisioning_operations_state",
        ),
        CheckConstraint(
            "attempt_count >= 0", name="ck_provisioning_operations_attempts"
        ),
        CheckConstraint(
            "desired_state_revision >= 1",
            name="ck_provisioning_operations_desired_revision",
        ),
        CheckConstraint(
            "profile_version >= 1 AND profile_schema_version >= 1",
            name="ck_provisioning_operations_profile_versions",
        ),
        CheckConstraint(
            "capability_schema_version >= 1 AND configuration_schema_version >= 1",
            name="ck_provisioning_operations_schema_versions",
        ),
        Index(
            "ix_provisioning_operations_due",
            "state",
            "next_attempt_at",
            "leased_until",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    apply_command_id: Mapped[str] = mapped_column(String(240), nullable=False)
    deployment_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    capability_id: Mapped[str] = mapped_column(String(160), nullable=False)
    capability_instance_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    capability_binding_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.capability_bindings.id", ondelete="RESTRICT"),
        nullable=False,
    )

    desired_state_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    desired_state_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    desired_state_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    saved_plan_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    approval_request_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    approval_request_binding_hash: Mapped[str] = mapped_column(
        String(71), nullable=False
    )
    plan_command_id: Mapped[str] = mapped_column(String(240), nullable=False)
    plan_validation_receipt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    plan_validation_receipt_digest: Mapped[str] = mapped_column(
        String(71), nullable=False
    )
    plan_validation_request_body_digest: Mapped[str] = mapped_column(
        String(71), nullable=False
    )
    module_plan_receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    profile_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    profile_code: Mapped[str] = mapped_column(String(120), nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    command_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_owner_code: Mapped[str] = mapped_column(String(120), nullable=False)
    capability_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    capability_contract_attestation_id: Mapped[UUID] = mapped_column(
        Uuid(), nullable=False
    )
    capability_contract_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    capability_operations_json: Mapped[list[object]] = mapped_column(
        _JSON, nullable=False
    )
    capability_schemas_json: Mapped[list[object]] = mapped_column(_JSON, nullable=False)
    prerequisite_evidence_bindings_json: Mapped[list[object]] = mapped_column(
        _JSON, nullable=False
    )
    prerequisite_receipt_pins_json: Mapped[list[object]] = mapped_column(
        _JSON, nullable=False
    )
    installation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.connector_installations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    installation_ref: Mapped[str] = mapped_column(String(160), nullable=False)

    expected_plan_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    approval_grant_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    approval_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    approval_verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approval_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    artifact_digest: Mapped[str] = mapped_column(String(128), nullable=False)

    connector_key: Mapped[str] = mapped_column(String(120), nullable=False)
    connector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    config_revision_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.connector_config_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    config_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    configuration_snapshot_ref: Mapped[str] = mapped_column(String(320), nullable=False)
    configuration_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    component_artifact_digest: Mapped[str | None] = mapped_column(
        String(71), nullable=True
    )
    execution_policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    approved_command_template_digest: Mapped[str] = mapped_column(
        String(71), nullable=False
    )

    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    leased_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProvisioningStep(Base):
    """One approved DAG step with its durable retry/observation state."""

    __tablename__ = "provisioning_steps"
    __table_args__ = (
        UniqueConstraint("operation_id", "step_key", name="uq_provisioning_steps_key"),
        UniqueConstraint(
            "operation_id", "ordinal", name="uq_provisioning_steps_ordinal"
        ),
        CheckConstraint("ordinal >= 1", name="ck_provisioning_steps_ordinal"),
        CheckConstraint(
            "state IN ('pending', 'in_flight', 'observing', 'retryable', "
            "'observe_in_flight', 'observe_retryable', 'cancel_in_flight', "
            "'cancel_retryable', "
            "'succeeded', 'terminal', 'reconciliation_required', 'cancelled')",
            name="ck_provisioning_steps_state",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_provisioning_steps_attempts"),
        Index(
            "ix_provisioning_steps_due",
            "operation_id",
            "state",
            "next_attempt_at",
            "leased_until",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    operation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.provisioning_operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_key: Mapped[str] = mapped_column(String(160), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    endpoint_code: Mapped[str] = mapped_column(String(160), nullable=False)
    depends_on_json: Mapped[list[object]] = mapped_column(_JSON, nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    input_json: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    resolved_input_digest: Mapped[str | None] = mapped_column(String(71), nullable=True)

    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    leased_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    provider_operation_ref: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProvisioningReceipt(Base):
    """Immutable structured evidence in a per-operation hash chain."""

    __tablename__ = "provisioning_receipts"
    __table_args__ = (
        UniqueConstraint(
            "operation_id", "sequence", name="uq_provisioning_receipts_sequence"
        ),
        UniqueConstraint("receipt_hash", name="uq_provisioning_receipts_hash"),
        CheckConstraint("sequence >= 1", name="ck_provisioning_receipts_sequence"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    operation_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.provisioning_operations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    step_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.provisioning_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    receipt_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    step_key: Mapped[str | None] = mapped_column(String(160), nullable=True)
    provider_operation_ref: Mapped[str | None] = mapped_column(
        String(320), nullable=True
    )
    previous_receipt_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False)

    plan_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    capability_instance_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    connector_key: Mapped[str] = mapped_column(String(120), nullable=False)
    connector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    artifact_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    config_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    approval_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    evidence_json: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _immutable_receipt(
    _mapper: Mapper[ProvisioningReceipt],
    _connection: sa.Connection,
    target: ProvisioningReceipt,
) -> None:
    raise ConflictError(f"provisioning receipt {target.id} is immutable")


event.listen(ProvisioningReceipt, "before_update", _immutable_receipt)
event.listen(ProvisioningReceipt, "before_delete", _immutable_receipt)


class ProvisioningCommandRecord(Base):
    """One durable command identity shared by all four provisioning actions."""

    __tablename__ = "provisioning_commands"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_provisioning_commands_command_id"),
        CheckConstraint(
            "command_kind IN ('plan', 'apply', 'observe', 'cancel')",
            name="ck_provisioning_commands_kind",
        ),
        CheckConstraint(
            "state IN ('accepted', 'in_flight', 'settled')",
            name="ck_provisioning_commands_state",
        ),
        Index("ix_provisioning_commands_operation", "operation_id", "created_at"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    command_id: Mapped[str] = mapped_column(String(240), nullable=False)
    command_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    operation_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.provisioning_operations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    step_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.provisioning_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    request_json: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="accepted"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProvisioningCommandReceipt(Base):
    """Immutable result evidence for commands that do not own an operation.

    PLAN runs before approval and therefore cannot borrow an APPLY operation's
    receipt chain. This one-row receipt binds its exact signed request body to
    the result the connector validated, without giving the assembly a second
    ledger.
    """

    __tablename__ = "provisioning_command_receipts"
    __table_args__ = schema_table_args(SCHEMA)

    id: Mapped[UUID] = uuid_pk()
    command_record_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey(f"{SCHEMA}.provisioning_commands.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    command_id: Mapped[str] = mapped_column(String(240), nullable=False, unique=True)
    command_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_instance_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    request_body_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    result_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    receipt_hash: Mapped[str] = mapped_column(String(71), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def _immutable_command_receipt(
    _mapper: Mapper[ProvisioningCommandReceipt],
    _connection: sa.Connection,
    target: ProvisioningCommandReceipt,
) -> None:
    raise ConflictError(f"provisioning command receipt {target.id} is immutable")


event.listen(ProvisioningCommandReceipt, "before_update", _immutable_command_receipt)
event.listen(ProvisioningCommandReceipt, "before_delete", _immutable_command_receipt)


__all__ = [
    "PROVISIONING_PLATFORM_TABLES",
    "ProvisioningCommandReceipt",
    "ProvisioningCommandRecord",
    "ProvisioningOperation",
    "ProvisioningReceipt",
    "ProvisioningStep",
]
