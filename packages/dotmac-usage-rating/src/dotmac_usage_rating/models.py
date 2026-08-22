"""Usage-rating persistence contract."""

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
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("usage_rate")


class RatingRule(Base, TimestampMixin):
    __tablename__ = "rating_rules"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_rating_rules_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_rating_rules_tenant_code"),
        Index(
            "ix_rating_rules_tenant_meter_effective",
            "tenant_id",
            "meter_code",
            "effective_from",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    meter_code: Mapped[str] = mapped_column(String(80), nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RatedUsageObligation(Base, TimestampMixin):
    __tablename__ = "rated_usage_obligations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_rated_usage_obligations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "usage_reference",
            "rule_id",
            name="uq_rated_usage_obligations_tenant_usage_rule",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [f"{SCHEMA}.rating_rules.tenant_id", f"{SCHEMA}.rating_rules.id"],
            name="fk_rated_usage_obligations_tenant_rule",
        ),
        Index(
            "ix_rated_usage_obligations_tenant_service_time",
            "tenant_id",
            "service_reference",
            "usage_occurred_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    usage_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    service_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    rule_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(24, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    usage_occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


TENANT_TABLES = ("rating_rules", "rated_usage_obligations")
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (RatingRule, RatedUsageObligation)
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "RatedUsageObligation",
    "RatingRule",
    "metadata_table",
]
