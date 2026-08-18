"""Tenant-only persistence for the reusable Orders owner."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
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
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, MappedColumn, mapped_column

SCHEMA = module_schema("orders")
MONEY = Numeric(20, 6)
QUANTITY = Numeric(20, 6)
FX_RATE = Numeric(38, 18)
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _tenant_column() -> MappedColumn[UUID]:
    return mapped_column(
        Uuid(),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )


class Order(Base, TimestampMixin):
    """Stable customer-order identity and its guarded lifecycle."""

    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_orders_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "order_reference", name="uq_orders_tenant_reference"
        ),
        CheckConstraint(
            "currency_code = upper(currency_code)",
            name="ck_orders_currency_upper",
        ),
        CheckConstraint(
            "currency_minor_units BETWEEN 0 AND 6",
            name="ck_orders_currency_minor_units",
        ),
        CheckConstraint("fx_rate IS NULL OR fx_rate > 0", name="ck_orders_fx_rate"),
        CheckConstraint("subtotal_amount >= 0", name="ck_orders_subtotal"),
        CheckConstraint("discount_amount >= 0", name="ck_orders_discount"),
        CheckConstraint("tax_amount >= 0", name="ck_orders_tax"),
        CheckConstraint("total_amount >= 0", name="ck_orders_total"),
        CheckConstraint(
            "subtotal_amount - discount_amount + tax_amount = total_amount",
            name="ck_orders_totals_balance",
        ),
        CheckConstraint(
            "(fx_rate IS NULL AND fx_base_currency_code IS NULL "
            "AND fx_rate_ref IS NULL AND fx_source IS NULL AND fx_as_of IS NULL) "
            "OR (fx_rate IS NOT NULL AND fx_base_currency_code IS NOT NULL "
            "AND fx_rate_ref IS NOT NULL AND fx_source IS NOT NULL "
            "AND fx_as_of IS NOT NULL)",
            name="ck_orders_fx_snapshot_complete",
        ),
        CheckConstraint(
            "fx_base_currency_code IS NULL "
            "OR (fx_base_currency_code = upper(fx_base_currency_code) "
            "AND fx_base_currency_code <> currency_code)",
            name="ck_orders_fx_pair",
        ),
        CheckConstraint(
            "(source_ref IS NULL AND source_version IS NULL) "
            "OR (source_ref IS NOT NULL AND source_version IS NOT NULL)",
            name="ck_orders_source_provenance_complete",
        ),
        CheckConstraint(
            "(accepted_at IS NULL AND accepted_actor_type IS NULL "
            "AND accepted_actor_id IS NULL AND accepted_actor_label IS NULL) "
            "OR (accepted_at IS NOT NULL AND accepted_actor_type IS NOT NULL)",
            name="ck_orders_acceptance_evidence_complete",
        ),
        CheckConstraint(
            "(cancelled_at IS NULL AND cancelled_actor_type IS NULL "
            "AND cancelled_actor_id IS NULL AND cancelled_actor_label IS NULL "
            "AND cancellation_reason IS NULL) "
            "OR (cancelled_at IS NOT NULL AND cancelled_actor_type IS NOT NULL "
            "AND cancellation_reason IS NOT NULL)",
            name="ck_orders_cancellation_evidence_complete",
        ),
        Index("ix_orders_tenant_state", "tenant_id", "state"),
        Index("ix_orders_tenant_customer", "tenant_id", "customer_ref"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    order_reference: Mapped[str] = mapped_column(String(120), nullable=False)
    customer_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(80), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    currency_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    subtotal_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_frozen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(120), nullable=True)

    fx_rate: Mapped[Decimal | None] = mapped_column(FX_RATE, nullable=True)
    fx_base_currency_code: Mapped[str | None] = mapped_column(String(3), nullable=True)
    fx_rate_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fx_source: Mapped[str | None] = mapped_column(String(255), nullable=True)
    fx_as_of: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    submitted_actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    submitted_actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    submitted_actor_label: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_actor_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    accepted_actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    accepted_actor_label: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    covered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_actor_type: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    cancelled_actor_id: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    cancelled_actor_label: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class OrderLineSnapshot(Base):
    """One write-once commercial line snapshot."""

    __tablename__ = "order_line_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_order_line_snapshots_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "order_id",
            "id",
            name="uq_order_line_snapshots_tenant_order_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "order_id",
            "line_key",
            name="uq_order_line_snapshots_tenant_order_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            [f"{SCHEMA}.orders.tenant_id", f"{SCHEMA}.orders.id"],
            name="fk_order_line_snapshots_order",
            ondelete="RESTRICT",
        ),
        CheckConstraint("quantity > 0", name="ck_order_line_snapshots_quantity"),
        CheckConstraint(
            "currency_code = upper(currency_code)",
            name="ck_order_line_snapshots_currency_upper",
        ),
        CheckConstraint(
            "currency_minor_units BETWEEN 0 AND 6",
            name="ck_order_line_snapshots_currency_minor_units",
        ),
        CheckConstraint(
            "unit_price >= 0",
            name="ck_order_line_snapshots_unit_price",
        ),
        CheckConstraint(
            "extended_price >= 0",
            name="ck_order_line_snapshots_extended_price",
        ),
        CheckConstraint(
            "discount_amount >= 0", name="ck_order_line_snapshots_discount"
        ),
        CheckConstraint("tax_amount >= 0", name="ck_order_line_snapshots_tax"),
        CheckConstraint("line_total >= 0", name="ck_order_line_snapshots_total"),
        CheckConstraint(
            "extended_price - discount_amount + tax_amount = line_total",
            name="ck_order_line_snapshots_totals_balance",
        ),
        CheckConstraint(
            "(source_ref IS NULL AND source_version IS NULL) "
            "OR (source_ref IS NOT NULL AND source_version IS NOT NULL)",
            name="ck_order_line_snapshots_source_provenance_complete",
        ),
        Index(
            "ix_order_line_snapshots_tenant_order", "tenant_id", "order_id"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    order_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_key: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    currency_minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    extended_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_snapshot: Mapped[list[dict[str, object]]] = mapped_column(
        _JSON, nullable=False
    )
    line_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    price_version_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    terms_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    terms_snapshot: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    specification_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class CoverageGate(Base, TimestampMixin):
    """The finite obligation set and its one-way satisfaction decision."""

    __tablename__ = "coverage_gates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_coverage_gates_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "order_id", name="uq_coverage_gates_tenant_order"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            [f"{SCHEMA}.orders.tenant_id", f"{SCHEMA}.orders.id"],
            name="fk_coverage_gates_order",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "obligation_count > 0",
            name="ck_coverage_gates_obligation_count",
        ),
        CheckConstraint(
            "resolved_count >= 0 AND resolved_count <= obligation_count",
            name="ck_coverage_gates_resolved_count",
        ),
        Index("ix_coverage_gates_tenant_state", "tenant_id", "state"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    order_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    obligation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    resolved_count: Mapped[int] = mapped_column(Integer, nullable=False)
    satisfied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CoverageObligation(Base):
    """One immutable member of the finite coverage set."""

    __tablename__ = "coverage_obligations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_coverage_obligations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "gate_id",
            "obligation_ref",
            name="uq_coverage_obligations_tenant_gate_ref",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "gate_id"],
            [f"{SCHEMA}.coverage_gates.tenant_id", f"{SCHEMA}.coverage_gates.id"],
            name="fk_coverage_obligations_gate",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_coverage_obligations_tenant_gate", "tenant_id", "gate_id"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    gate_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class CoverageResolutionReceipt(Base):
    """One append-only satisfying receipt for a registered obligation."""

    __tablename__ = "coverage_resolution_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_coverage_resolution_receipts_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "gate_id",
            "obligation_ref",
            name="uq_coverage_resolution_receipts_tenant_gate_obligation",
        ),
        UniqueConstraint(
            "tenant_id",
            "resolution_ref",
            name="uq_coverage_resolution_receipts_tenant_resolution",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "gate_id", "obligation_ref"],
            [
                f"{SCHEMA}.coverage_obligations.tenant_id",
                f"{SCHEMA}.coverage_obligations.gate_id",
                f"{SCHEMA}.coverage_obligations.obligation_ref",
            ],
            name="fk_coverage_resolution_receipts_obligation",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_coverage_resolution_receipts_tenant_gate", "tenant_id", "gate_id"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    gate_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    resolution_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    resolution_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(120), nullable=False)
    receipt_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


class FulfillmentRequest(Base, TimestampMixin):
    """One stable per-line request identity and its delivery evidence."""

    __tablename__ = "fulfillment_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_requests_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "order_id",
            "line_snapshot_id",
            name="uq_fulfillment_requests_tenant_order_line",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            [f"{SCHEMA}.orders.tenant_id", f"{SCHEMA}.orders.id"],
            name="fk_fulfillment_requests_order",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "order_id", "line_snapshot_id"],
            [
                f"{SCHEMA}.order_line_snapshots.tenant_id",
                f"{SCHEMA}.order_line_snapshots.order_id",
                f"{SCHEMA}.order_line_snapshots.id",
            ],
            name="fk_fulfillment_requests_line",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_fulfillment_requests_tenant_order", "tenant_id", "order_id"
        ),
        Index("ix_fulfillment_requests_tenant_state", "tenant_id", "state"),
        CheckConstraint(
            "publication_count >= 1",
            name="ck_fulfillment_requests_publications",
        ),
        CheckConstraint(
            "(acceptance_ref IS NULL AND accepted_at IS NULL) "
            "OR (acceptance_ref IS NOT NULL AND accepted_at IS NOT NULL)",
            name="ck_fulfillment_requests_acceptance_complete",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    order_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_snapshot_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    state: Mapped[str] = mapped_column(String(40), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    publication_count: Mapped[int] = mapped_column(Integer, nullable=False)
    last_outbox_event_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    last_published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    acceptance_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class OrderEvent(Base):
    """Append-only domain trail used to reconcile lifecycle consequences."""

    __tablename__ = "order_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_order_events_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "event_ref", name="uq_order_events_tenant_event_ref"
        ),
        UniqueConstraint(
            "tenant_id",
            "order_id",
            "event_sequence",
            name="uq_order_events_tenant_order_sequence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            [f"{SCHEMA}.orders.tenant_id", f"{SCHEMA}.orders.id"],
            name="fk_order_events_order",
            ondelete="RESTRICT",
        ),
        Index("ix_order_events_tenant_order", "tenant_id", "order_id"),
        CheckConstraint("event_sequence > 0", name="ck_order_events_sequence"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_column()
    order_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    from_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    to_state: Mapped[str | None] = mapped_column(String(80), nullable=True)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    actor_label: Mapped[str | None] = mapped_column(String(160), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    details: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa.func.now()
    )


TENANT_TABLES: tuple[str, ...] = (
    "orders",
    "order_line_snapshots",
    "coverage_gates",
    "coverage_obligations",
    "coverage_resolution_receipts",
    "fulfillment_requests",
    "order_events",
)

ALL_MODELS: tuple[type[Base], ...] = (
    Order,
    OrderLineSnapshot,
    CoverageGate,
    CoverageObligation,
    CoverageResolutionReceipt,
    FulfillmentRequest,
    OrderEvent,
)

IMMUTABLE_MODELS: tuple[type[Base], ...] = (
    OrderLineSnapshot,
    CoverageObligation,
    CoverageResolutionReceipt,
    OrderEvent,
)

IMMUTABLE_TABLES: tuple[str, ...] = tuple(
    model.__tablename__ for model in IMMUTABLE_MODELS
)

__all__ = [
    "ALL_MODELS",
    "IMMUTABLE_MODELS",
    "IMMUTABLE_TABLES",
    "MONEY",
    "QUANTITY",
    "SCHEMA",
    "TENANT_TABLES",
    "CoverageGate",
    "CoverageObligation",
    "CoverageResolutionReceipt",
    "FulfillmentRequest",
    "Order",
    "OrderEvent",
    "OrderLineSnapshot",
]
