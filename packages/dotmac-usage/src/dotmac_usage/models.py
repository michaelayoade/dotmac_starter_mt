"""Normalized usage persistence contract."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("usage")


class UsageObservation(Base, TimestampMixin):
    __tablename__ = "usage_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_usage_observations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_reference",
            "source_event_id",
            name="uq_usage_observations_tenant_source_event",
        ),
        Index(
            "ix_usage_observations_tenant_service_period",
            "tenant_id",
            "service_reference",
            "period_start",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    service_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    meter_code: Mapped[str] = mapped_column(String(80), nullable=False)
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(180), nullable=False)


class UsageCorrection(Base, TimestampMixin):
    __tablename__ = "usage_corrections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_usage_corrections_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                f"{SCHEMA}.usage_observations.tenant_id",
                f"{SCHEMA}.usage_observations.id",
            ],
            ondelete="CASCADE",
            name="fk_usage_corrections_tenant_observation",
        ),
        Index(
            "ix_usage_corrections_tenant_observation",
            "tenant_id",
            "observation_id",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    delta_quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class UsageAggregate(Base, TimestampMixin):
    __tablename__ = "usage_aggregates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_usage_aggregates_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "service_reference",
            "meter_code",
            "window_start",
            "window_end",
            name="uq_usage_aggregates_tenant_window",
        ),
        Index(
            "ix_usage_aggregates_tenant_service_window",
            "tenant_id",
            "service_reference",
            "window_start",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    service_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    meter_code: Mapped[str] = mapped_column(String(80), nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_TABLES = ("usage_observations", "usage_corrections", "usage_aggregates")
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (UsageObservation, UsageCorrection, UsageAggregate)
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "UsageAggregate",
    "UsageCorrection",
    "UsageObservation",
    "metadata_table",
]
