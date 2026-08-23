"""Tenant-only persistence for the Dotmac hosting-service owner."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    DateTime,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

SCHEMA = module_schema("hosting")


def _tenant_id() -> MappedColumn[uuid.UUID]:
    return mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class HostingSpecification(Base):
    __tablename__ = "hosting_specifications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_specifications_tenant_id_id"),
        UniqueConstraint("tenant_id", "specification_code", name="uq_hosting_specifications_tenant_code"),
        UniqueConstraint(
            "tenant_id",
            "id",
            "specification_code",
            name="uq_hosting_specifications_tenant_id_code",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    specification_code: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HostingSpecificationVersion(Base):
    __tablename__ = "hosting_specification_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_specification_versions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "specification_code",
            "version",
            name="uq_hosting_specification_versions_tenant_code_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "specification_code",
            "version",
            "content_digest",
            name="uq_hosting_specification_versions_chain_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "specification_id", "specification_code"],
            [
                f"{SCHEMA}.hosting_specifications.tenant_id",
                f"{SCHEMA}.hosting_specifications.id",
                f"{SCHEMA}.hosting_specifications.specification_code",
            ],
            ondelete="RESTRICT",
            name="fk_hosting_specification_versions_specification",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "specification_code",
                "previous_version",
                "previous_content_digest",
            ],
            [
                f"{SCHEMA}.hosting_specification_versions.tenant_id",
                f"{SCHEMA}.hosting_specification_versions.specification_code",
                f"{SCHEMA}.hosting_specification_versions.version",
                f"{SCHEMA}.hosting_specification_versions.content_digest",
            ],
            ondelete="RESTRICT",
            name="fk_hosting_specification_versions_previous",
        ),
        CheckConstraint(
            "package_rank >= 0",
            name="ck_hosting_specification_versions_package_rank",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_hosting_specification_versions_version",
        ),
        CheckConstraint(
            "jsonb_typeof(change_rules) = 'object' "
            "AND change_rules ?& ARRAY['upgrade_allowed','downgrade_allowed',"
            "'downgrade_requires_review','same_level_allowed'] "
            "AND (change_rules - ARRAY['upgrade_allowed','downgrade_allowed',"
            "'downgrade_requires_review','same_level_allowed']) = '{}'::jsonb "
            "AND jsonb_typeof(change_rules->'upgrade_allowed') = 'boolean' "
            "AND jsonb_typeof(change_rules->'downgrade_allowed') = 'boolean' "
            "AND jsonb_typeof(change_rules->'downgrade_requires_review') = 'boolean' "
            "AND jsonb_typeof(change_rules->'same_level_allowed') = 'boolean'",
            name="ck_hosting_specification_versions_change_rules_shape",
        ),
        CheckConstraint(
            "(version = 1 AND previous_version IS NULL AND previous_content_digest IS NULL) "
            "OR (version > 1 AND previous_version = version - 1 "
            "AND previous_content_digest IS NOT NULL)",
            name="ck_hosting_specification_versions_previous_link",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    specification_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    specification_code: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    package_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    package_rank: Mapped[int] = mapped_column(Integer, nullable=False)
    allowances: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    included_artifacts: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    capability_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    change_rules: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    previous_version: Mapped[int | None] = mapped_column(Integer)
    previous_content_digest: Mapped[str | None] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HostingService(Base):
    __tablename__ = "hosting_services"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_services_tenant_id_id"),
        UniqueConstraint("tenant_id", "order_line_ref", name="uq_hosting_services_order_line"),
        UniqueConstraint(
            "tenant_id",
            "capability_binding_ref",
            "provider_account_ref",
            name="uq_hosting_services_binding_account",
        ),
        CheckConstraint(
            "(capability_binding_ref IS NULL) = (provider_account_ref IS NULL)",
            name="ck_hosting_services_provider_pair",
        ),
        CheckConstraint(
            "row_version >= 0",
            name="ck_hosting_services_row_version",
        ),
        Index("ix_hosting_services_tenant_state", "tenant_id", "lifecycle_state"),
        ForeignKeyConstraint(
            ["tenant_id", "specification_code", "specification_version"],
            [
                f"{SCHEMA}.hosting_specification_versions.tenant_id",
                f"{SCHEMA}.hosting_specification_versions.specification_code",
                f"{SCHEMA}.hosting_specification_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_hosting_services_specification_version",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    customer_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    order_line_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    offer_version_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    specification_code: Mapped[str] = mapped_column(String(120), nullable=False)
    specification_version: Mapped[int] = mapped_column(Integer, nullable=False)
    primary_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    account_label: Mapped[str] = mapped_column(String(160), nullable=False)
    administrative_email: Mapped[str] = mapped_column(String(254), nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    capability_binding_ref: Mapped[str | None] = mapped_column(String(255))
    provider_account_ref: Mapped[str | None] = mapped_column(String(255))
    lifecycle_state: Mapped[str] = mapped_column(String(48), nullable=False)
    state_effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HostingDesiredRevision(Base):
    __tablename__ = "hosting_desired_revisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_desired_revisions_tenant_id_id"),
        UniqueConstraint("tenant_id", "hosting_service_id", "version", name="uq_hosting_desired_revisions_service_version"),
        ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            [f"{SCHEMA}.hosting_services.tenant_id", f"{SCHEMA}.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_desired_revisions_service",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "specification_code", "specification_version"],
            [
                f"{SCHEMA}.hosting_specification_versions.tenant_id",
                f"{SCHEMA}.hosting_specification_versions.specification_code",
                f"{SCHEMA}.hosting_specification_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_hosting_desired_revisions_specification_version",
        ),
        Index("ix_hosting_desired_revisions_tenant_service", "tenant_id", "hosting_service_id"),
        CheckConstraint(
            "version > 0",
            name="ck_hosting_desired_revisions_version",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    hosting_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    desired_account_state: Mapped[str] = mapped_column(String(32), nullable=False)
    specification_code: Mapped[str] = mapped_column(String(120), nullable=False)
    specification_version: Mapped[int] = mapped_column(Integer, nullable=False)
    package_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HostingCommand(Base):
    __tablename__ = "hosting_commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_commands_tenant_id_id"),
        UniqueConstraint("tenant_id", "idempotency_scope", "idempotency_key", name="uq_hosting_commands_tenant_scope_key"),
        ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            [f"{SCHEMA}.hosting_services.tenant_id", f"{SCHEMA}.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_commands_service",
        ),
        Index("ix_hosting_commands_tenant_service", "tenant_id", "hosting_service_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    hosting_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    command_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class HostingCommandOutcome(Base):
    __tablename__ = "hosting_command_outcomes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_command_outcomes_tenant_id_id"),
        UniqueConstraint("tenant_id", "hosting_command_id", "evidence_key", name="uq_hosting_command_outcomes_command_evidence"),
        ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            [f"{SCHEMA}.hosting_services.tenant_id", f"{SCHEMA}.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_command_outcomes_service",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "hosting_command_id"],
            [f"{SCHEMA}.hosting_commands.tenant_id", f"{SCHEMA}.hosting_commands.id"],
            ondelete="RESTRICT",
            name="fk_hosting_command_outcomes_command",
        ),
        Index("ix_hosting_command_outcomes_tenant_service", "tenant_id", "hosting_service_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    hosting_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    hosting_command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    evidence_key: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome_class: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(255))
    reason_code: Mapped[str | None] = mapped_column(String(160))
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class HostingObservation(Base):
    __tablename__ = "hosting_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_observations_tenant_id_id"),
        UniqueConstraint("tenant_id", "capability_binding_ref", "provider_event_id", name="uq_hosting_observations_binding_event"),
        ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            [f"{SCHEMA}.hosting_services.tenant_id", f"{SCHEMA}.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_observations_service",
        ),
        Index("ix_hosting_observations_tenant_service_time", "tenant_id", "hosting_service_id", "observed_at"),
        Index(
            "ix_hosting_observations_tenant_operation",
            "tenant_id",
            "operation_reference",
            "observed_at",
        ),
        CheckConstraint(
            "source_mode IN ('ingress', 'poll')",
            name="ck_hosting_observations_source_mode",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    hosting_service_id: Mapped[uuid.UUID | None] = mapped_column()
    operation_reference: Mapped[str | None] = mapped_column(String(120))
    provider_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    capability_binding_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    observation_kind: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_statuses: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    observed_package_ref: Mapped[str | None] = mapped_column(String(255))
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HostingObservationResource(Base):
    __tablename__ = "hosting_observation_resources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_observation_resources_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "hosting_observation_id",
            "resource_kind",
            "unit",
            "period_identity",
            name="uq_hosting_observation_resources_fact",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "hosting_observation_id"],
            [f"{SCHEMA}.hosting_observations.tenant_id", f"{SCHEMA}.hosting_observations.id"],
            ondelete="RESTRICT",
            name="fk_hosting_observation_resources_observation",
        ),
        Index("ix_hosting_observation_resources_tenant_observation", "tenant_id", "hosting_observation_id"),
        CheckConstraint(
            "quantity >= 0",
            name="ck_hosting_observation_resources_quantity",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    hosting_observation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    resource_kind: Mapped[str] = mapped_column(String(120), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    unit: Mapped[str] = mapped_column(String(48), nullable=False)
    period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_identity: Mapped[str] = mapped_column(String(130), nullable=False)


class HostingSuspensionLock(Base):
    __tablename__ = "hosting_suspension_locks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_suspension_locks_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            [f"{SCHEMA}.hosting_services.tenant_id", f"{SCHEMA}.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_suspension_locks_service",
        ),
        Index(
            "uq_hosting_suspension_locks_one_active",
            "tenant_id",
            "hosting_service_id",
            "reason_code",
            unique=True,
            postgresql_where=text("cleared_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    hosting_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    allowed_restorer_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_by: Mapped[str | None] = mapped_column(String(120))


class HostingRetentionHold(Base):
    __tablename__ = "hosting_retention_holds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_retention_holds_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            [f"{SCHEMA}.hosting_services.tenant_id", f"{SCHEMA}.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_retention_holds_service",
        ),
        Index(
            "uq_hosting_retention_holds_one_active",
            "tenant_id",
            "hosting_service_id",
            "hold_code",
            unique=True,
            postgresql_where=text("cleared_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    hosting_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    hold_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(160), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_reason: Mapped[str | None] = mapped_column(String(160))


class HostingTerminationApprovalEvidence(Base):
    __tablename__ = "hosting_termination_approval_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_hosting_termination_approval_evidence_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "request_id",
            name="uq_hosting_termination_approval_evidence_request",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_event_id",
            name="uq_hosting_termination_approval_evidence_source_event",
        ),
        CheckConstraint(
            "subject_type = 'hosting_service'",
            name="ck_hosting_termination_approval_evidence_subject",
        ),
        CheckConstraint(
            "policy_code = 'hosting.termination.v1' AND policy_version = 1",
            name="ck_hosting_termination_approval_evidence_policy",
        ),
        CheckConstraint(
            "state = 'approved'",
            name="ck_hosting_termination_approval_evidence_state",
        ),
        CheckConstraint(
            "event_type = 'approval.approved' AND state = 'approved'",
            name="ck_hosting_termination_approval_evidence_event_state",
        ),
        CheckConstraint(
            "content_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_hosting_termination_approval_evidence_content_digest",
        ),
        CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name="ck_hosting_termination_approval_evidence_event_digest",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    request_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    subject_type: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    policy_code: Mapped[str] = mapped_column(String(160), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    event_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class HostingAttentionCondition(Base):
    __tablename__ = "hosting_attention_conditions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_hosting_attention_conditions_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            [f"{SCHEMA}.hosting_services.tenant_id", f"{SCHEMA}.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_attention_conditions_service",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_command_id"],
            [f"{SCHEMA}.hosting_commands.tenant_id", f"{SCHEMA}.hosting_commands.id"],
            ondelete="RESTRICT",
            name="fk_hosting_attention_conditions_command",
        ),
        Index(
            "uq_hosting_attention_conditions_one_open",
            "tenant_id",
            "hosting_service_id",
            "condition_code",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    hosting_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_command_id: Mapped[uuid.UUID | None] = mapped_column()
    condition_code: Mapped[str] = mapped_column(String(120), nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(160), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_code: Mapped[str | None] = mapped_column(String(160))


ALL_MODELS = (
    HostingSpecification,
    HostingSpecificationVersion,
    HostingService,
    HostingDesiredRevision,
    HostingCommand,
    HostingCommandOutcome,
    HostingObservation,
    HostingObservationResource,
    HostingSuspensionLock,
    HostingRetentionHold,
    HostingTerminationApprovalEvidence,
    HostingAttentionCondition,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)


__all__ = [
    "ALL_MODELS",
    "SCHEMA",
    "TABLES",
    "HostingAttentionCondition",
    "HostingCommand",
    "HostingCommandOutcome",
    "HostingDesiredRevision",
    "HostingObservation",
    "HostingObservationResource",
    "HostingRetentionHold",
    "HostingService",
    "HostingSpecification",
    "HostingSpecificationVersion",
    "HostingSuspensionLock",
    "HostingTerminationApprovalEvidence",
]
