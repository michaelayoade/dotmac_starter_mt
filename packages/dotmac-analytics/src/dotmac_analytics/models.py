"""Tenant-plane persistence for analytical evidence and projections."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("analytics")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _tenant_column() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), sa.ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class MetricCatalogEntry(Base):
    """Immutable declaration snapshot captured before the first point."""

    __tablename__ = "metric_catalog_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_metric_catalog_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "metric_code",
            "schema_version",
            name="uq_metric_catalog_identity",
        ),
        Index("ix_metric_catalog_owner", "tenant_id", "owner_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    metric_code: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    owner_code: Mapped[str] = mapped_column(String(120), nullable=False)
    declaration_fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    value_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_code: Mapped[str] = mapped_column(String(120), nullable=False)
    granularities_json: Mapped[list[str]] = mapped_column(_JSON, nullable=False)
    dimensions_json: Mapped[list[list[str | int]]] = mapped_column(
        _JSON, nullable=False
    )


class MetricIngestReceipt(Base):
    """Immutable domain evidence for one kernel-guarded accepted batch."""

    __tablename__ = "metric_ingest_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_metric_receipts_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_metric_receipts_source_event",
        ),
        Index("ix_metric_receipts_received", "tenant_id", "received_at"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    adapter_code: Mapped[str] = mapped_column(String(120), nullable=False)
    delivery_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    point_count: Mapped[int] = mapped_column(Integer, nullable=False)


class MetricObservation(Base):
    """Append-only exact aggregate point from one accepted source event."""

    __tablename__ = "metric_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_metric_observations_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "receipt_id",
            "metric_code",
            "schema_version",
            "period_start",
            "period_end",
            "granularity",
            "selector_digest",
            name="uq_metric_observations_receipt_coordinate",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "receipt_id"],
            [
                f"{SCHEMA}.metric_ingest_receipts.tenant_id",
                f"{SCHEMA}.metric_ingest_receipts.id",
            ],
            ondelete="RESTRICT",
            name="fk_metric_observations_receipt",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metric_code", "schema_version"],
            [
                f"{SCHEMA}.metric_catalog_entries.tenant_id",
                f"{SCHEMA}.metric_catalog_entries.metric_code",
                f"{SCHEMA}.metric_catalog_entries.schema_version",
            ],
            ondelete="RESTRICT",
            name="fk_metric_observations_declaration",
        ),
        Index(
            "ix_metric_observations_series",
            "tenant_id",
            "metric_code",
            "schema_version",
            "period_start",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    receipt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    metric_code: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    granularity: Mapped[str] = mapped_column(String(16), nullable=False)
    selector_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    dimensions_json: Mapped[list[list[str]]] = mapped_column(_JSON, nullable=False)
    value_numeric: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MetricPoint(Base):
    """The one mutable, fully rebuildable winning-point projection."""

    __tablename__ = "metric_points"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_metric_points_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "metric_code",
            "schema_version",
            "period_start",
            "period_end",
            "granularity",
            "selector_digest",
            name="uq_metric_points_coordinate",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metric_code", "schema_version"],
            [
                f"{SCHEMA}.metric_catalog_entries.tenant_id",
                f"{SCHEMA}.metric_catalog_entries.metric_code",
                f"{SCHEMA}.metric_catalog_entries.schema_version",
            ],
            ondelete="RESTRICT",
            name="fk_metric_points_declaration",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                f"{SCHEMA}.metric_observations.tenant_id",
                f"{SCHEMA}.metric_observations.id",
            ],
            ondelete="RESTRICT",
            name="fk_metric_points_observation",
        ),
        Index(
            "ix_metric_points_latest",
            "tenant_id",
            "metric_code",
            "schema_version",
            "granularity",
            "period_start",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    metric_code: Mapped[str] = mapped_column(String(120), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    granularity: Mapped[str] = mapped_column(String(16), nullable=False)
    selector_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    dimensions_json: Mapped[list[list[str]]] = mapped_column(_JSON, nullable=False)
    value_numeric: Mapped[Decimal] = mapped_column(Numeric(38, 12), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MetricProjectionRebuild(Base):
    __tablename__ = "metric_projection_rebuilds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_metric_rebuilds_tenant_id"),
        Index("ix_metric_rebuilds_time", "tenant_id", "rebuilt_at"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    before_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    after_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    point_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rebuilt_by: Mapped[str] = mapped_column(String(255), nullable=False)
    rebuilt_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_MODELS = (
    MetricCatalogEntry,
    MetricIngestReceipt,
    MetricObservation,
    MetricPoint,
    MetricProjectionRebuild,
)
APPEND_ONLY_MODELS = (
    MetricCatalogEntry,
    MetricIngestReceipt,
    MetricObservation,
    MetricProjectionRebuild,
)
TENANT_TABLES: tuple[str, ...] = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "APPEND_ONLY_MODELS",
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "MetricCatalogEntry",
    "MetricIngestReceipt",
    "MetricObservation",
    "MetricPoint",
    "MetricProjectionRebuild",
]
