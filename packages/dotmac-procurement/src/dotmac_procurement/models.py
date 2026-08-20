"""Tenant-only persistence for procurement decisions and evidence."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    CheckConstraint,
    Date,
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

from dotmac_procurement.contracts import (
    BidStatus,
    EvaluationStatus,
    PurchaseOrderStatus,
    RequisitionStatus,
    SourcingMethod,
    SourcingStatus,
)

SCHEMA = module_schema("procurement")
MONEY = Numeric(20, 6)
QUANTITY = Numeric(20, 6)


def _enum(enum_cls: type[Enum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_cls,
        name=name,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        create_constraint=True,
    )


class PurchaseRequisition(Base, TimestampMixin):
    __tablename__ = "purchase_requisitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_purchase_requisitions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "requisition_number",
            name="uq_purchase_requisitions_tenant_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_purchase_requisitions_source",
        ),
        Index("ix_purchase_requisitions_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "total_estimated_amount >= 0",
            name="ck_purchase_requisitions_total_non_negative",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    requisition_number: Mapped[str] = mapped_column(String(80), nullable=False)
    requested_on: Mapped[date] = mapped_column(Date(), nullable=False)
    requester_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    created_by_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    urgency: Mapped[str] = mapped_column(String(40), nullable=False, default="normal")
    justification: Mapped[str | None] = mapped_column(Text(), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    total_estimated_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[RequisitionStatus] = mapped_column(
        _enum(RequisitionStatus, "procurement_requisition_status"),
        nullable=False,
        default=RequisitionStatus.DRAFT,
        server_default=RequisitionStatus.DRAFT.value,
    )
    source_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    budget_authorization_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    budget_authorized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_decision_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PurchaseRequisitionLine(Base, TimestampMixin):
    __tablename__ = "purchase_requisition_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_purchase_requisition_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "requisition_id",
            "line_number",
            name="uq_purchase_requisition_lines_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requisition_id"],
            [
                f"{SCHEMA}.purchase_requisitions.tenant_id",
                f"{SCHEMA}.purchase_requisitions.id",
            ],
            ondelete="CASCADE",
            name="fk_purchase_requisition_lines_tenant_requisition",
        ),
        CheckConstraint("quantity > 0", name="ck_requisition_lines_quantity_positive"),
        CheckConstraint(
            "estimated_unit_cost >= 0",
            name="ck_requisition_lines_unit_cost_non_negative",
        ),
        CheckConstraint(
            "estimated_total >= 0", name="ck_requisition_lines_total_non_negative"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    requisition_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    estimated_unit_cost: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    estimated_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    item_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expense_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cost_center_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date(), nullable=True)


class SourcingEvent(Base, TimestampMixin):
    __tablename__ = "sourcing_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sourcing_events_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "event_number", name="uq_sourcing_events_tenant_number"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_requisition_id"],
            [
                f"{SCHEMA}.purchase_requisitions.tenant_id",
                f"{SCHEMA}.purchase_requisitions.id",
            ],
            ondelete="RESTRICT",
            name="fk_sourcing_events_tenant_requisition",
        ),
        Index("ix_sourcing_events_tenant_status", "tenant_id", "status"),
        CheckConstraint("closes_at > opens_at", name="ck_sourcing_events_window"),
        CheckConstraint(
            "estimated_amount >= 0", name="ck_sourcing_events_estimate_non_negative"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    event_number: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    method: Mapped[SourcingMethod] = mapped_column(
        _enum(SourcingMethod, "procurement_sourcing_method"), nullable=False
    )
    status: Mapped[SourcingStatus] = mapped_column(
        _enum(SourcingStatus, "procurement_sourcing_status"),
        nullable=False,
        default=SourcingStatus.DRAFT,
        server_default=SourcingStatus.DRAFT.value,
    )
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    criteria_json: Mapped[str] = mapped_column(Text(), nullable=False)
    terms: Mapped[str | None] = mapped_column(Text(), nullable=True)
    estimated_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    source_requisition_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    created_by_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    awarded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SourcingEventLine(Base, TimestampMixin):
    __tablename__ = "sourcing_event_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_sourcing_event_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "event_id",
            "id",
            name="uq_sourcing_event_lines_tenant_event_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "event_id",
            "line_number",
            name="uq_sourcing_event_lines_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [f"{SCHEMA}.sourcing_events.tenant_id", f"{SCHEMA}.sourcing_events.id"],
            ondelete="CASCADE",
            name="fk_sourcing_event_lines_tenant_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_requisition_line_id"],
            [
                f"{SCHEMA}.purchase_requisition_lines.tenant_id",
                f"{SCHEMA}.purchase_requisition_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_sourcing_event_lines_tenant_requisition_line",
        ),
        CheckConstraint("quantity > 0", name="ck_sourcing_event_lines_quantity"),
        CheckConstraint(
            "target_unit_cost IS NULL OR target_unit_cost >= 0",
            name="ck_sourcing_event_lines_target_cost",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    source_requisition_line_id: Mapped[UUID | None] = mapped_column(
        Uuid(), nullable=True
    )
    item_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_unit_cost: Mapped[Decimal | None] = mapped_column(MONEY, nullable=True)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date(), nullable=True)


class SourcingInvitation(Base, TimestampMixin):
    __tablename__ = "sourcing_invitations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_sourcing_invitations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "event_id",
            "supplier_ref",
            name="uq_sourcing_invitations_supplier",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [f"{SCHEMA}.sourcing_events.tenant_id", f"{SCHEMA}.sourcing_events.id"],
            ondelete="CASCADE",
            name="fk_sourcing_invitations_tenant_event",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    supplier_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    invited_by_ref: Mapped[str] = mapped_column(String(255), nullable=False)


class BidSubmission(Base, TimestampMixin):
    __tablename__ = "bid_submissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bid_submissions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "event_id", "id", name="uq_bid_submissions_tenant_event_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "event_id",
            "supplier_ref",
            name="uq_bid_submissions_supplier_event",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_bid_submissions_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [f"{SCHEMA}.sourcing_events.tenant_id", f"{SCHEMA}.sourcing_events.id"],
            ondelete="CASCADE",
            name="fk_bid_submissions_tenant_event",
        ),
        CheckConstraint("total_amount >= 0", name="ck_bid_submissions_total"),
        Index("ix_bid_submissions_tenant_event", "tenant_id", "event_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    response_number: Mapped[str] = mapped_column(String(80), nullable=False)
    supplier_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[BidStatus] = mapped_column(
        _enum(BidStatus, "procurement_bid_status"),
        nullable=False,
        default=BidStatus.DRAFT,
        server_default=BidStatus.DRAFT.value,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    validity_days: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    delivery_period_days: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    technical_proposal: Mapped[str | None] = mapped_column(Text(), nullable=True)
    terms: Mapped[str | None] = mapped_column(Text(), nullable=True)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BidLine(Base, TimestampMixin):
    __tablename__ = "bid_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bid_lines_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "bid_id", "line_number", name="uq_bid_lines_position"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id", "bid_id"],
            [
                f"{SCHEMA}.bid_submissions.tenant_id",
                f"{SCHEMA}.bid_submissions.event_id",
                f"{SCHEMA}.bid_submissions.id",
            ],
            ondelete="CASCADE",
            name="fk_bid_lines_tenant_event_bid",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id", "sourcing_line_id"],
            [
                f"{SCHEMA}.sourcing_event_lines.tenant_id",
                f"{SCHEMA}.sourcing_event_lines.event_id",
                f"{SCHEMA}.sourcing_event_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_bid_lines_tenant_event_sourcing_line",
        ),
        CheckConstraint("quantity > 0", name="ck_bid_lines_quantity"),
        CheckConstraint("unit_price >= 0", name="ck_bid_lines_unit_price"),
        CheckConstraint("line_total >= 0", name="ck_bid_lines_total"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    bid_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sourcing_line_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    promised_delivery_date: Mapped[date | None] = mapped_column(Date(), nullable=True)


class BidEvaluation(Base, TimestampMixin):
    __tablename__ = "bid_evaluations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_bid_evaluations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "event_id", name="uq_bid_evaluations_tenant_event"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [f"{SCHEMA}.sourcing_events.tenant_id", f"{SCHEMA}.sourcing_events.id"],
            ondelete="CASCADE",
            name="fk_bid_evaluations_tenant_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id", "selected_bid_id"],
            [
                f"{SCHEMA}.bid_submissions.tenant_id",
                f"{SCHEMA}.bid_submissions.event_id",
                f"{SCHEMA}.bid_submissions.id",
            ],
            ondelete="RESTRICT",
            name="fk_bid_evaluations_tenant_event_bid",
        ),
        CheckConstraint(
            "selected_total_score >= 0 AND selected_total_score <= 100",
            name="ck_bid_evaluations_score",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    selected_bid_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    status: Mapped[EvaluationStatus] = mapped_column(
        _enum(EvaluationStatus, "procurement_evaluation_status"),
        nullable=False,
        default=EvaluationStatus.COMPLETED,
        server_default=EvaluationStatus.COMPLETED.value,
    )
    scores_json: Mapped[str] = mapped_column(Text(), nullable=False)
    selected_total_score: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False
    )
    report: Mapped[str | None] = mapped_column(Text(), nullable=True)
    evaluated_by_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_decision_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PurchaseOrder(Base, TimestampMixin):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_purchase_orders_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "order_number", name="uq_purchase_orders_tenant_number"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_requisition_id",
            name="uq_purchase_orders_tenant_requisition_source",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_evaluation_id",
            name="uq_purchase_orders_tenant_evaluation_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_requisition_id"],
            [
                f"{SCHEMA}.purchase_requisitions.tenant_id",
                f"{SCHEMA}.purchase_requisitions.id",
            ],
            ondelete="RESTRICT",
            name="fk_purchase_orders_tenant_requisition",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_evaluation_id"],
            [
                f"{SCHEMA}.bid_evaluations.tenant_id",
                f"{SCHEMA}.bid_evaluations.id",
            ],
            ondelete="RESTRICT",
            name="fk_purchase_orders_tenant_evaluation",
        ),
        CheckConstraint(
            "subtotal >= 0 AND tax_amount >= 0 AND total_amount >= 0",
            name="ck_purchase_orders_totals",
        ),
        CheckConstraint(
            "source_requisition_id IS NOT NULL OR source_evaluation_id IS NOT NULL",
            name="ck_purchase_orders_source_required",
        ),
        Index("ix_purchase_orders_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    order_number: Mapped[str] = mapped_column(String(80), nullable=False)
    supplier_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    ordered_on: Mapped[date] = mapped_column(Date(), nullable=False)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[PurchaseOrderStatus] = mapped_column(
        _enum(PurchaseOrderStatus, "procurement_purchase_order_status"),
        nullable=False,
        default=PurchaseOrderStatus.DRAFT,
        server_default=PurchaseOrderStatus.DRAFT.value,
    )
    source_requisition_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    source_evaluation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    created_by_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    ship_to_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    terms: Mapped[str | None] = mapped_column(Text(), nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    approval_decision_ref: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PurchaseOrderLine(Base, TimestampMixin):
    __tablename__ = "purchase_order_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_purchase_order_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "order_id",
            "line_number",
            name="uq_purchase_order_lines_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            [f"{SCHEMA}.purchase_orders.tenant_id", f"{SCHEMA}.purchase_orders.id"],
            ondelete="CASCADE",
            name="fk_purchase_order_lines_tenant_order",
        ),
        CheckConstraint(
            "quantity_ordered > 0", name="ck_purchase_order_lines_quantity"
        ),
        CheckConstraint(
            "quantity_received >= 0 AND quantity_received <= quantity_ordered",
            name="ck_purchase_order_lines_received",
        ),
        CheckConstraint(
            "unit_price >= 0 AND line_amount >= 0 AND tax_amount >= 0",
            name="ck_purchase_order_lines_totals",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    order_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer(), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    quantity_ordered: Mapped[Decimal] = mapped_column(QUANTITY, nullable=False)
    quantity_received: Mapped[Decimal] = mapped_column(
        QUANTITY, nullable=False, default=Decimal("0"), server_default="0"
    )
    unit: Mapped[str] = mapped_column(String(40), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    item_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expense_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    asset_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cost_center_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expected_delivery_date: Mapped[date | None] = mapped_column(Date(), nullable=True)


class ReceiptObservationRecord(Base, TimestampMixin):
    __tablename__ = "receipt_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_receipt_observations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_receipt_observations_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "order_id"],
            [f"{SCHEMA}.purchase_orders.tenant_id", f"{SCHEMA}.purchase_orders.id"],
            ondelete="CASCADE",
            name="fk_receipt_observations_tenant_order",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    order_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    lines_json: Mapped[str] = mapped_column(Text(), nullable=False)


class ProcurementEvidence(Base, TimestampMixin):
    __tablename__ = "procurement_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_procurement_evidence_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "aggregate_kind",
            "aggregate_id",
            "sequence",
            name="uq_procurement_evidence_sequence",
        ),
        Index(
            "ix_procurement_evidence_aggregate",
            "tenant_id",
            "aggregate_kind",
            "aggregate_id",
        ),
        CheckConstraint("sequence >= 1", name="ck_procurement_evidence_sequence"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    aggregate_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer(), nullable=False)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[str] = mapped_column(Text(), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    PurchaseRequisition,
    PurchaseRequisitionLine,
    SourcingEvent,
    SourcingEventLine,
    SourcingInvitation,
    BidSubmission,
    BidLine,
    BidEvaluation,
    PurchaseOrder,
    PurchaseOrderLine,
    ReceiptObservationRecord,
    ProcurementEvidence,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "TABLES",
    "BidEvaluation",
    "BidLine",
    "BidSubmission",
    "ProcurementEvidence",
    "PurchaseOrder",
    "PurchaseOrderLine",
    "PurchaseRequisition",
    "PurchaseRequisitionLine",
    "ReceiptObservationRecord",
    "SCHEMA",
    "SourcingEvent",
    "SourcingEventLine",
    "SourcingInvitation",
]
