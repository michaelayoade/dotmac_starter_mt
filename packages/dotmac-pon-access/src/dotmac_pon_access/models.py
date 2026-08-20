"""Provider-neutral OLT/ONT/PON persistence in ``mod_pon``."""

from __future__ import annotations

import uuid
from datetime import datetime

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("pon")


class Olt(Base):
    __tablename__ = "olts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pon_olts_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_pon_olts_tenant_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    management_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    vendor_family: Mapped[str] = mapped_column(String(120), nullable=False)
    capability_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    node_ref: Mapped[str | None] = mapped_column(String(200))
    asset_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PonPort(Base):
    __tablename__ = "pon_ports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pon_ports_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "olt_id", "slot", "port", name="uq_pon_port_position"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "olt_id"],
            [f"{SCHEMA}.olts.tenant_id", f"{SCHEMA}.olts.id"],
            name="fk_pon_ports_olt",
            ondelete="CASCADE",
        ),
        CheckConstraint("slot >= 0 AND port >= 0", name="ck_pon_port_position"),
        CheckConstraint("capacity > 0", name="ck_pon_port_capacity"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    olt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    fiber_endpoint_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Ont(Base):
    __tablename__ = "onts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pon_onts_tenant_id_id"),
        UniqueConstraint("tenant_id", "serial_number", name="uq_pon_ont_serial_number"),
        UniqueConstraint(
            "tenant_id", "registration_ref", name="uq_pon_ont_registration_ref"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pon_port_id"],
            [f"{SCHEMA}.pon_ports.tenant_id", f"{SCHEMA}.pon_ports.id"],
            name="fk_pon_onts_port",
            ondelete="RESTRICT",
        ),
        Index("ix_pon_onts_port_state", "tenant_id", "pon_port_id", "state"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    serial_number: Mapped[str] = mapped_column(String(160), nullable=False)
    vendor_family: Mapped[str] = mapped_column(String(120), nullable=False)
    pon_port_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    service_subject_ref: Mapped[str | None] = mapped_column(String(200))
    assignment_ref: Mapped[str | None] = mapped_column(String(240))
    registration_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    asset_ref: Mapped[str | None] = mapped_column(String(200))
    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    commissioned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commissioned_profile_code: Mapped[str | None] = mapped_column(String(120))
    desired_config_ref: Mapped[str | None] = mapped_column(String(240))
    operation_ref: Mapped[str | None] = mapped_column(String(240))


class DesiredService(Base):
    __tablename__ = "desired_services"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_pon_desired_services_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "ont_id",
            "service_ref",
            name="uq_pon_desired_service",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "ont_id"],
            [f"{SCHEMA}.onts.tenant_id", f"{SCHEMA}.onts.id"],
            name="fk_pon_desired_services_ont",
            ondelete="CASCADE",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    ont_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    service_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    profile_code: Mapped[str] = mapped_column(String(120), nullable=False)
    vlan_ref: Mapped[str | None] = mapped_column(String(200))
    ip_assignment_ref: Mapped[str | None] = mapped_column(String(200))
    desired_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    observed_fingerprint: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    decision_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PonObservation(Base):
    __tablename__ = "pon_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pon_observations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_pon_observation_fingerprint",
        ),
        Index(
            "ix_pon_observations_subject",
            "tenant_id",
            "subject_ref",
            "observed_at",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    observation_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    value: Mapped[str] = mapped_column(String(240), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(40))
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)


class PonReconciliation(Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pon_reconciliation_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "desired_service_id"],
            [f"{SCHEMA}.desired_services.tenant_id", f"{SCHEMA}.desired_services.id"],
            name="fk_pon_reconciliation_desired_service",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_pon_reconciliation_desired",
            "tenant_id",
            "desired_service_id",
            "reconciled_at",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    desired_service_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    observed_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    drifted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(120))
    reconciled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class BackupEvidenceRow(Base):
    __tablename__ = "backup_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pon_backup_evidence_tenant_id_id"),
        UniqueConstraint("tenant_id", "backup_ref", name="uq_pon_backup_evidence_ref"),
        ForeignKeyConstraint(
            ["tenant_id", "olt_id"],
            [f"{SCHEMA}.olts.tenant_id", f"{SCHEMA}.olts.id"],
            name="fk_pon_backup_evidence_olt",
            ondelete="RESTRICT",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    olt_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    backup_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    configuration_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)


class PonEvent(Base):
    __tablename__ = "pon_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_pon_events_tenant_id_id"),
        Index(
            "ix_pon_events_aggregate",
            "tenant_id",
            "aggregate_ref",
            "occurred_at",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    aggregate_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    Olt,
    PonPort,
    Ont,
    DesiredService,
    PonObservation,
    PonReconciliation,
    BackupEvidenceRow,
    PonEvent,
)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "BackupEvidenceRow",
    "DesiredService",
    "Ont",
    "Olt",
    "PonEvent",
    "PonObservation",
    "PonPort",
    "PonReconciliation",
    "SCHEMA",
    "TENANT_TABLES",
]
