"""FX-policy persistence contract."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("fx_policy")


class FXRateType(Base, TimestampMixin):
    __tablename__ = "fx_rate_types"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fx_rate_types_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_fx_rate_types_tenant_code"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)


class FXRateSource(Base, TimestampMixin):
    __tablename__ = "fx_rate_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_fx_rate_sources_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_fx_rate_sources_tenant_code"),
        CheckConstraint("priority >= 0", name="ck_fx_rate_sources_priority"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    priority: Mapped[int] = mapped_column(Integer(), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)


class FXSelectionPolicy(Base, TimestampMixin):
    __tablename__ = "fx_selection_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_fx_selection_policies_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "rate_type_id",
            "base_currency",
            "quote_currency",
            "effective_from",
            name="uq_fx_selection_policy_tenant_pair_effective",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rate_type_id"],
            [f"{SCHEMA}.fx_rate_types.tenant_id", f"{SCHEMA}.fx_rate_types.id"],
            ondelete="CASCADE",
            name="fk_fx_selection_policies_tenant_rate_type",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "preferred_source_id"],
            [f"{SCHEMA}.fx_rate_sources.tenant_id", f"{SCHEMA}.fx_rate_sources.id"],
            name="fk_fx_selection_policies_tenant_source",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_fx_selection_policies_window",
        ),
        Index(
            "ix_fx_selection_policies_tenant_lookup",
            "tenant_id",
            "rate_type_id",
            "base_currency",
            "quote_currency",
            "effective_from",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    rate_type_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    preferred_source_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    allow_inverse: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)


class FXRateObservation(Base, TimestampMixin):
    __tablename__ = "fx_rate_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_fx_rate_observations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "source_event_reference",
            name="uq_fx_rate_observations_tenant_source_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rate_type_id"],
            [f"{SCHEMA}.fx_rate_types.tenant_id", f"{SCHEMA}.fx_rate_types.id"],
            name="fk_fx_rate_observations_tenant_rate_type",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            [f"{SCHEMA}.fx_rate_sources.tenant_id", f"{SCHEMA}.fx_rate_sources.id"],
            name="fk_fx_rate_observations_tenant_source",
        ),
        CheckConstraint("rate > 0", name="ck_fx_rate_observations_positive"),
        CheckConstraint(
            "base_currency <> quote_currency",
            name="ck_fx_rate_observations_distinct_pair",
        ),
        Index(
            "ix_fx_rate_observations_tenant_lookup",
            "tenant_id",
            "rate_type_id",
            "base_currency",
            "quote_currency",
            "effective_at",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    rate_type_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_event_reference: Mapped[str] = mapped_column(String(180), nullable=False)


class FXRateDetermination(Base, TimestampMixin):
    __tablename__ = "fx_rate_determinations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_fx_rate_determinations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "request_reference",
            name="uq_fx_rate_determinations_tenant_request",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "rate_type_id"],
            [f"{SCHEMA}.fx_rate_types.tenant_id", f"{SCHEMA}.fx_rate_types.id"],
            name="fk_fx_rate_determinations_tenant_rate_type",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                f"{SCHEMA}.fx_selection_policies.tenant_id",
                f"{SCHEMA}.fx_selection_policies.id",
            ],
            name="fk_fx_rate_determinations_tenant_policy",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                f"{SCHEMA}.fx_rate_observations.tenant_id",
                f"{SCHEMA}.fx_rate_observations.id",
            ],
            name="fk_fx_rate_determinations_tenant_observation",
        ),
        CheckConstraint("rate > 0", name="ck_fx_rate_determinations_positive"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    request_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    rate_type_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    observation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    inverted: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    determined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_TABLES = (
    "fx_rate_types",
    "fx_rate_sources",
    "fx_selection_policies",
    "fx_rate_observations",
    "fx_rate_determinations",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (
        FXRateType,
        FXRateSource,
        FXSelectionPolicy,
        FXRateObservation,
        FXRateDetermination,
    )
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "FXRateDetermination",
    "FXRateObservation",
    "FXRateSource",
    "FXRateType",
    "FXSelectionPolicy",
    "metadata_table",
]
