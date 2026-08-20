"""Collector-neutral network observation persistence in ``mod_netobs``."""

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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("netobs")


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netobs_observations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_netobs_observation_fingerprint",
        ),
        Index(
            "ix_netobs_observations_subject", "tenant_id", "subject_ref", "observed_at"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    attributes: Mapped[list[list[str]]] = mapped_column(JSON, nullable=False)


class Measurement(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netobs_measurements_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_netobs_measurement_fingerprint",
        ),
        Index(
            "ix_netobs_measurements_subject_metric",
            "tenant_id",
            "subject_ref",
            "metric_code",
            "observed_at",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    dimensions: Mapped[list[list[str]]] = mapped_column(JSON, nullable=False)


class AvailabilityFact(Base):
    __tablename__ = "availability_facts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netobs_availability_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "subject_ref",
            "source_ref",
            "observed_at",
            name="uq_netobs_availability_observation",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason_code: Mapped[str | None] = mapped_column(String(120))


class HealthProjection(Base):
    __tablename__ = "health_projections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netobs_health_tenant_id_id"),
        UniqueConstraint("tenant_id", "subject_ref", name="uq_netobs_health_subject"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(120))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_observation_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    rebuilt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netobs_alerts_tenant_id_id"),
        Index("ix_netobs_alerts_subject_state", "tenant_id", "subject_ref", "state"),
        Index(
            "uq_netobs_open_alert",
            "tenant_id",
            "subject_ref",
            "rule_ref",
            unique=True,
            postgresql_where=text("state = 'open'"),
            sqlite_where=text("state = 'open'"),
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    rule_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)


class AlertEvidence(Base):
    __tablename__ = "alert_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_netobs_alert_evidence_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "alert_id",
            "evidence_ref",
            "event_type",
            name="uq_netobs_alert_evidence_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "alert_id"],
            [f"{SCHEMA}.alerts.tenant_id", f"{SCHEMA}.alerts.id"],
            name="fk_netobs_alert_evidence_alert",
            ondelete="CASCADE",
        ),
        Index("ix_netobs_alert_evidence_alert", "tenant_id", "alert_id", "observed_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    alert_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    Observation,
    Measurement,
    AvailabilityFact,
    HealthProjection,
    Alert,
    AlertEvidence,
)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)
__all__ = [
    "ALL_MODELS",
    "Alert",
    "AlertEvidence",
    "AvailabilityFact",
    "HealthProjection",
    "Measurement",
    "Observation",
    "SCHEMA",
    "TENANT_TABLES",
]
