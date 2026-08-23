"""Tenant-only persistence for the Dotmac domain-service owner."""

from __future__ import annotations

import uuid
from datetime import datetime

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

SCHEMA = module_schema("domains")


def _tenant_id() -> MappedColumn[uuid.UUID]:
    return mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class DomainService(Base):
    __tablename__ = "domain_services"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_domain_services_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "registered_name",
            name="uq_domain_services_tenant_name",
        ),
        Index("ix_domain_services_tenant_state", "tenant_id", "lifecycle_state"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    registered_name: Mapped[str] = mapped_column(String(253), nullable=False)
    lifecycle_state: Mapped[str] = mapped_column(String(48), nullable=False)
    state_effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    order_line_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    offer_version_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    commercial_renewal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DomainCommand(Base):
    __tablename__ = "domain_commands"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_domain_commands_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "idempotency_scope",
            "idempotency_key",
            name="uq_domain_commands_tenant_scope_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            [
                f"{SCHEMA}.domain_services.tenant_id",
                f"{SCHEMA}.domain_services.id",
            ],
            ondelete="RESTRICT",
            name="fk_domain_commands_service",
        ),
        Index(
            "ix_domain_commands_tenant_service",
            "tenant_id",
            "domain_service_id",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    domain_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    command_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_scope: Mapped[str] = mapped_column(String(120), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DomainCommandOutcome(Base):
    __tablename__ = "domain_command_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_domain_command_outcomes_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "domain_command_id",
            "evidence_key",
            name="uq_domain_command_outcomes_command_evidence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            [
                f"{SCHEMA}.domain_services.tenant_id",
                f"{SCHEMA}.domain_services.id",
            ],
            ondelete="RESTRICT",
            name="fk_domain_command_outcomes_service",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "domain_command_id"],
            [f"{SCHEMA}.domain_commands.tenant_id", f"{SCHEMA}.domain_commands.id"],
            ondelete="RESTRICT",
            name="fk_domain_command_outcomes_command",
        ),
        Index(
            "ix_domain_command_outcomes_tenant_service",
            "tenant_id",
            "domain_service_id",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    domain_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    domain_command_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    evidence_key: Mapped[str] = mapped_column(String(255), nullable=False)
    outcome_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome_class: Mapped[str] = mapped_column(String(40), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(255))
    reason_code: Mapped[str | None] = mapped_column(String(160))
    details: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DomainObservation(Base):
    __tablename__ = "domain_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_domain_observations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "capability_binding_ref",
            "provider_event_id",
            name="uq_domain_observations_binding_event",
        ),
        Index(
            "ix_domain_observations_tenant_name_time",
            "tenant_id",
            "registered_name",
            "observed_at",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    # Deliberately no required service FK: a callback may arrive before local
    # command correlation exists.  Reconciliation joins by canonical name.
    domain_service_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    registered_name: Mapped[str] = mapped_column(String(253), nullable=False)
    capability_binding_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    observation_kind: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_statuses: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    redemption_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_nameservers: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    observed_contact_digest: Mapped[str | None] = mapped_column(String(64))
    source_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class DomainIntent(Base):
    __tablename__ = "domain_intents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_domain_intents_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "domain_service_id",
            "intent_kind",
            "version",
            name="uq_domain_intents_service_kind_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            [
                f"{SCHEMA}.domain_services.tenant_id",
                f"{SCHEMA}.domain_services.id",
            ],
            ondelete="RESTRICT",
            name="fk_domain_intents_service",
        ),
        Index(
            "ix_domain_intents_tenant_service_kind",
            "tenant_id",
            "domain_service_id",
            "intent_kind",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    domain_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    intent_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class DomainHold(Base):
    __tablename__ = "domain_holds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_domain_holds_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            [
                f"{SCHEMA}.domain_services.tenant_id",
                f"{SCHEMA}.domain_services.id",
            ],
            ondelete="RESTRICT",
            name="fk_domain_holds_service",
        ),
        Index(
            "uq_domain_holds_one_active",
            "tenant_id",
            "domain_service_id",
            "hold_code",
            unique=True,
            postgresql_where=text("cleared_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    domain_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    hold_code: Mapped[str] = mapped_column(String(120), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(160), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_reason: Mapped[str | None] = mapped_column(String(160))


class DomainAttentionCondition(Base):
    __tablename__ = "domain_attention_conditions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_domain_attention_conditions_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "domain_service_id"],
            [
                f"{SCHEMA}.domain_services.tenant_id",
                f"{SCHEMA}.domain_services.id",
            ],
            ondelete="RESTRICT",
            name="fk_domain_attention_conditions_service",
        ),
        Index(
            "uq_domain_attention_one_open",
            "tenant_id",
            "domain_service_id",
            "condition_code",
            unique=True,
            postgresql_where=text("resolved_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = _tenant_id()
    domain_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_command_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    condition_code: Mapped[str] = mapped_column(String(120), nullable=False)
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(160), nullable=False)
    details: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_code: Mapped[str | None] = mapped_column(String(160))


ALL_MODELS = (
    DomainService,
    DomainCommand,
    DomainCommandOutcome,
    DomainObservation,
    DomainIntent,
    DomainHold,
    DomainAttentionCondition,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)


__all__ = [
    "ALL_MODELS",
    "SCHEMA",
    "TABLES",
    "DomainAttentionCondition",
    "DomainCommand",
    "DomainCommandOutcome",
    "DomainHold",
    "DomainIntent",
    "DomainObservation",
    "DomainService",
]
