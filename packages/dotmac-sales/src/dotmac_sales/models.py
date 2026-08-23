"""Tenant-plane persistence for sales through accepted Quote handoff."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

SCHEMA = module_schema("sales")
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _tenant_id() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), sa.ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class Pipeline(Base, TimestampMixin):
    __tablename__ = "pipelines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sales_pipelines_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_sales_pipelines_tenant_name"),
        Index("ix_sales_pipelines_tenant", "tenant_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text())
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    stages: Mapped[list[PipelineStage]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineStage.order_index",
    )


class PipelineStage(Base, TimestampMixin):
    __tablename__ = "pipeline_stages"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_sales_pipeline_stages_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "pipeline_id",
            "id",
            name="uq_sales_pipeline_stages_tenant_pipeline_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "pipeline_id",
            "name",
            name="uq_sales_pipeline_stages_tenant_pipeline_name",
        ),
        UniqueConstraint(
            "tenant_id",
            "pipeline_id",
            "order_index",
            name="uq_sales_pipeline_stages_tenant_pipeline_order",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pipeline_id"],
            [f"{SCHEMA}.pipelines.tenant_id", f"{SCHEMA}.pipelines.id"],
            ondelete="CASCADE",
            name="fk_sales_pipeline_stages_pipeline",
        ),
        Index("ix_sales_pipeline_stages_tenant_pipeline", "tenant_id", "pipeline_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    pipeline_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer(), nullable=False)
    default_probability: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0
    )
    is_active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    pipeline: Mapped[Pipeline] = relationship(back_populates="stages")


class Lead(Base, TimestampMixin):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sales_leads_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "pipeline_id"],
            [f"{SCHEMA}.pipelines.tenant_id", f"{SCHEMA}.pipelines.id"],
            ondelete="RESTRICT",
            name="fk_sales_leads_pipeline",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pipeline_id", "stage_id"],
            [
                f"{SCHEMA}.pipeline_stages.tenant_id",
                f"{SCHEMA}.pipeline_stages.pipeline_id",
                f"{SCHEMA}.pipeline_stages.id",
            ],
            ondelete="RESTRICT",
            name="fk_sales_leads_stage",
        ),
        Index("ix_sales_leads_tenant_status", "tenant_id", "status"),
        Index("ix_sales_leads_tenant_pipeline", "tenant_id", "pipeline_id"),
        Index(
            "ix_sales_leads_tenant_subject",
            "tenant_id",
            "subject_kind",
            "subject_opaque_id",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    subject_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    subject_opaque_id: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_version: Mapped[str | None] = mapped_column(String(120))
    subject_label: Mapped[str] = mapped_column(String(240), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    pipeline_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    stage_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    probability: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    expected_close_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text())
    won_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lost_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    origins: Mapped[list[LeadOrigin]] = relationship(
        back_populates="lead", cascade="all, delete-orphan"
    )
    quotes: Mapped[list[Quote]] = relationship(back_populates="lead")


class LeadOrigin(Base):
    __tablename__ = "lead_origins"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sales_lead_origins_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "lead_id",
            "capture_method",
            "source_kind",
            "source_ref",
            name="uq_sales_lead_origins_source_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "lead_id"],
            [f"{SCHEMA}.leads.tenant_id", f"{SCHEMA}.leads.id"],
            ondelete="CASCADE",
            name="fk_sales_lead_origins_lead",
        ),
        Index("ix_sales_lead_origins_tenant_lead", "tenant_id", "lead_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    lead_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    capture_method: Mapped[str] = mapped_column(String(60), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(120), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    source_interaction_id: Mapped[str | None] = mapped_column(String(240))
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evidence: Mapped[dict[str, object]] = mapped_column(
        _JSON, nullable=False, default=dict
    )
    lead: Mapped[Lead] = relationship(back_populates="origins")


class Quote(Base, TimestampMixin):
    __tablename__ = "quotes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sales_quotes_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "accepted_event_id",
            name="uq_sales_quotes_tenant_accepted_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "lead_id"],
            [f"{SCHEMA}.leads.tenant_id", f"{SCHEMA}.leads.id"],
            ondelete="RESTRICT",
            name="fk_sales_quotes_lead",
        ),
        sa.CheckConstraint(
            "currency_minor_units BETWEEN 0 AND 6",
            name="ck_sales_quotes_currency_minor_units",
        ),
        Index("ix_sales_quotes_tenant_status", "tenant_id", "status"),
        Index("ix_sales_quotes_tenant_lead", "tenant_id", "lead_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    lead_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    currency_minor_units: Mapped[int] = mapped_column(Integer(), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    discount_type: Mapped[str | None] = mapped_column(String(30))
    discount_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    discount_revision: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    total: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    fulfillment_eligibility_requirement_refs: Mapped[list[str]] = mapped_column(
        _JSON, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text())
    authored_by_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    authored_by_opaque_id: Mapped[str] = mapped_column(String(200), nullable=False)
    authored_by_label: Mapped[str] = mapped_column(String(240), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    accepted_by_kind: Mapped[str | None] = mapped_column(String(60))
    accepted_by_opaque_id: Mapped[str | None] = mapped_column(String(200))
    accepted_by_label: Mapped[str | None] = mapped_column(String(240))
    accepted_event_id: Mapped[UUID | None] = mapped_column(Uuid())
    accepted_snapshot_sha256: Mapped[str | None] = mapped_column(String(64))
    accepted_handoff: Mapped[dict[str, object] | None] = mapped_column(_JSON)
    lead: Mapped[Lead] = relationship(back_populates="quotes")
    lines: Mapped[list[QuoteLine]] = relationship(
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteLine.position",
    )
    discount_revisions: Mapped[list[QuoteDiscountRevision]] = relationship(
        back_populates="quote",
        cascade="all, delete-orphan",
        order_by="QuoteDiscountRevision.revision",
    )


class QuoteLine(Base, TimestampMixin):
    __tablename__ = "quote_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sales_quote_lines_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "quote_id",
            "position",
            name="uq_sales_quote_lines_tenant_quote_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "quote_id"],
            [f"{SCHEMA}.quotes.tenant_id", f"{SCHEMA}.quotes.id"],
            ondelete="CASCADE",
            name="fk_sales_quote_lines_quote",
        ),
        Index("ix_sales_quote_lines_tenant_quote", "tenant_id", "quote_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    quote_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    position: Mapped[int] = mapped_column(Integer(), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    catalogue_ref: Mapped[str | None] = mapped_column(String(240))
    price_version_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    terms_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    terms_snapshot: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    specification_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    tax_rates: Mapped[list[dict[str, object]]] = mapped_column(_JSON, nullable=False)
    tax_components: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON, nullable=False
    )
    quote: Mapped[Quote] = relationship(back_populates="lines")


class QuoteDiscountRevision(Base):
    __tablename__ = "quote_discount_revisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_sales_discount_revisions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "quote_id",
            "revision",
            name="uq_sales_discount_revisions_tenant_quote_revision",
        ),
        UniqueConstraint(
            "tenant_id",
            "quote_id",
            "command_id",
            name="uq_sales_discount_revisions_tenant_quote_command",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "quote_id"],
            [f"{SCHEMA}.quotes.tenant_id", f"{SCHEMA}.quotes.id"],
            ondelete="CASCADE",
            name="fk_sales_discount_revisions_quote",
        ),
        Index("ix_sales_discount_revisions_tenant_quote", "tenant_id", "quote_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    quote_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    command_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    revision: Mapped[int] = mapped_column(Integer(), nullable=False)
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    discount_type: Mapped[str | None] = mapped_column(String(30))
    discount_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(60), nullable=False)
    actor_opaque_id: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_label: Mapped[str] = mapped_column(String(240), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    quote: Mapped[Quote] = relationship(back_populates="discount_revisions")


TENANT_TABLES = (
    "pipelines",
    "pipeline_stages",
    "leads",
    "lead_origins",
    "quotes",
    "quote_lines",
    "quote_discount_revisions",
)

__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "Lead",
    "LeadOrigin",
    "Pipeline",
    "PipelineStage",
    "Quote",
    "QuoteDiscountRevision",
    "QuoteLine",
]
