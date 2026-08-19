"""Network incident, impact, maintenance, and SLA evidence in ``mod_netassure``."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("netassure")


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netassure_incidents_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "code", name="uq_netassure_incidents_tenant_code"
        ),
        Index("ix_netassure_incidents_state", "tenant_id", "state", "severity"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    detection_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    source_observation_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_code: Mapped[str | None] = mapped_column(String(120))
    resolution_summary: Mapped[str | None] = mapped_column(Text)


class IncidentEvent(Base):
    __tablename__ = "incident_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netassure_events_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "incident_id"],
            [f"{SCHEMA}.incidents.tenant_id", f"{SCHEMA}.incidents.id"],
            name="fk_netassure_events_incident",
            ondelete="CASCADE",
        ),
        Index(
            "ix_netassure_events_incident", "tenant_id", "incident_id", "occurred_at"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Impact(Base):
    __tablename__ = "impacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netassure_impacts_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "incident_id"],
            [f"{SCHEMA}.incidents.tenant_id", f"{SCHEMA}.incidents.id"],
            name="fk_netassure_impacts_incident",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id",
            "incident_id",
            "subject_ref",
            name="uq_netassure_impact_subject",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(24), nullable=False)
    topology_path_ref: Mapped[str | None] = mapped_column(String(200))
    reason_code: Mapped[str] = mapped_column(String(120), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_netassure_maintenance_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "code", name="uq_netassure_maintenance_tenant_code"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(String(240), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope_refs: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    change_ref: Mapped[str | None] = mapped_column(String(200))


class NotificationEvidence(Base):
    __tablename__ = "notification_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_netassure_notifications_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "incident_id"],
            [f"{SCHEMA}.incidents.tenant_id", f"{SCHEMA}.incidents.id"],
            name="fk_netassure_notifications_incident",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "delivery_ref", name="uq_netassure_notification_delivery"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    delivery_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SlaEvidence(Base):
    __tablename__ = "sla_evidence"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netassure_sla_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "subject_ref",
            "period_start",
            "period_end",
            "source_ref",
            name="uq_netassure_sla_period",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    available_seconds: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unavailable_seconds: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    availability_ratio: Mapped[Decimal] = mapped_column(Numeric(12, 9), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)


ALL_MODELS = (
    Incident,
    IncidentEvent,
    Impact,
    MaintenanceWindow,
    NotificationEvidence,
    SlaEvidence,
)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)
__all__ = [
    "ALL_MODELS",
    "Impact",
    "Incident",
    "IncidentEvent",
    "MaintenanceWindow",
    "NotificationEvidence",
    "SCHEMA",
    "SlaEvidence",
    "TENANT_TABLES",
]
