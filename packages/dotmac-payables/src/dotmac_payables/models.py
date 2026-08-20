"""Tenant payables persistence in the allocated ``mod_payables`` schema."""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
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

from dotmac_payables.contracts import (
    CreditNoteStatus,
    InvoiceStatus,
    ObligationStatus,
)

SCHEMA = module_schema("payables")
MONEY = Numeric(20, 6)
RATE = Numeric(20, 10)


def _enum(enum_type: type[enum.Enum], name: str) -> sa.Enum:
    return sa.Enum(
        enum_type,
        name=name,
        native_enum=False,
        values_callable=lambda cls: [member.value for member in cls],
        create_constraint=True,
    )


def _tenant_id() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class SupplierInvoice(Base, TimestampMixin):
    __tablename__ = "supplier_invoices"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_supplier_invoices_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "number", name="uq_supplier_invoices_tenant_number"
        ),
        UniqueConstraint(
            "tenant_id",
            "supplier_ref",
            "supplier_document_number",
            name="uq_supplier_invoices_supplier_document",
        ),
        CheckConstraint(
            "length(currency_code) = 3", name="ck_supplier_invoices_currency"
        ),
        CheckConstraint("exchange_rate > 0", name="ck_supplier_invoices_rate_positive"),
        CheckConstraint(
            "subtotal >= 0 AND tax_amount >= 0 AND total_amount > 0",
            name="ck_supplier_invoices_amounts",
        ),
        CheckConstraint(
            "total_amount = subtotal + tax_amount",
            name="ck_supplier_invoices_total",
        ),
        Index("ix_supplier_invoices_tenant_supplier", "tenant_id", "supplier_ref"),
        Index("ix_supplier_invoices_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_document_number: Mapped[str] = mapped_column(String(120), nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    received_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    liability_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    payment_schedule: Mapped[list[dict[str, str]]] = mapped_column(JSON, nullable=False)
    procurement_ref: Mapped[str | None] = mapped_column(String(255))
    receipt_evidence_fingerprint: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[InvoiceStatus] = mapped_column(
        _enum(InvoiceStatus, "payables_invoice_status"),
        nullable=False,
        default=InvoiceStatus.DRAFT,
        server_default=InvoiceStatus.DRAFT.value,
    )
    submitted_by: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reference: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)


class SupplierInvoiceLine(Base):
    __tablename__ = "supplier_invoice_lines"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_supplier_invoice_lines_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "invoice_id",
            "line_number",
            name="uq_supplier_invoice_lines_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            ondelete="CASCADE",
            name="fk_supplier_invoice_lines_tenant_invoice",
        ),
        CheckConstraint("quantity > 0", name="ck_supplier_invoice_lines_quantity"),
        CheckConstraint(
            "unit_price >= 0 AND line_amount >= 0 AND tax_amount >= 0",
            name="ck_supplier_invoice_lines_amounts",
        ),
        Index("ix_supplier_invoice_lines_tenant_invoice", "tenant_id", "invoice_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    invoice_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    posting_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_account_ref: Mapped[str | None] = mapped_column(String(255))
    dimension_refs: Mapped[list[list[str]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CreditNote(Base, TimestampMixin):
    __tablename__ = "credit_notes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_credit_notes_tenant_id_id"),
        UniqueConstraint("tenant_id", "number", name="uq_credit_notes_tenant_number"),
        UniqueConstraint(
            "tenant_id",
            "supplier_ref",
            "supplier_document_number",
            name="uq_credit_notes_supplier_document",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "original_invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            ondelete="RESTRICT",
            name="fk_credit_notes_tenant_original_invoice",
        ),
        CheckConstraint("length(currency_code) = 3", name="ck_credit_notes_currency"),
        CheckConstraint("exchange_rate > 0", name="ck_credit_notes_rate_positive"),
        CheckConstraint(
            "subtotal >= 0 AND tax_amount >= 0 AND total_amount > 0",
            name="ck_credit_notes_amounts",
        ),
        CheckConstraint(
            "total_amount = subtotal + tax_amount",
            name="ck_credit_notes_total",
        ),
        CheckConstraint(
            "available_amount >= 0 AND available_amount <= total_amount",
            name="ck_credit_notes_available",
        ),
        Index("ix_credit_notes_tenant_supplier", "tenant_id", "supplier_ref"),
        Index("ix_credit_notes_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    original_invoice_id: Mapped[UUID | None] = mapped_column(Uuid())
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_document_number: Mapped[str] = mapped_column(String(120), nullable=False)
    credit_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    liability_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    available_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, default=0)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CreditNoteStatus] = mapped_column(
        _enum(CreditNoteStatus, "payables_credit_note_status"),
        nullable=False,
        default=CreditNoteStatus.DRAFT,
        server_default=CreditNoteStatus.DRAFT.value,
    )
    submitted_by: Mapped[str | None] = mapped_column(String(255))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reference: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)


class CreditNoteLine(Base):
    __tablename__ = "credit_note_lines"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_credit_note_lines_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "credit_note_id",
            "line_number",
            name="uq_credit_note_lines_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credit_note_id"],
            [f"{SCHEMA}.credit_notes.tenant_id", f"{SCHEMA}.credit_notes.id"],
            ondelete="CASCADE",
            name="fk_credit_note_lines_tenant_credit_note",
        ),
        CheckConstraint("quantity > 0", name="ck_credit_note_lines_quantity"),
        CheckConstraint(
            "unit_price >= 0 AND line_amount >= 0 AND tax_amount >= 0",
            name="ck_credit_note_lines_amounts",
        ),
        Index("ix_credit_note_lines_tenant_credit", "tenant_id", "credit_note_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    credit_note_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    line_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    posting_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    tax_account_ref: Mapped[str | None] = mapped_column(String(255))
    dimension_refs: Mapped[list[list[str]]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class PaymentObligation(Base, TimestampMixin):
    __tablename__ = "payment_obligations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_payment_obligations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "invoice_id",
            "sequence",
            name="uq_payment_obligations_invoice_sequence",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            ondelete="RESTRICT",
            name="fk_payment_obligations_tenant_invoice",
        ),
        CheckConstraint(
            "original_amount > 0", name="ck_payment_obligations_original_positive"
        ),
        CheckConstraint(
            "outstanding_amount >= 0 AND outstanding_amount <= original_amount",
            name="ck_payment_obligations_outstanding",
        ),
        Index("ix_payment_obligations_tenant_due", "tenant_id", "due_date", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    invoice_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    outstanding_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    status: Mapped[ObligationStatus] = mapped_column(
        _enum(ObligationStatus, "payables_obligation_status"),
        nullable=False,
        default=ObligationStatus.OPEN,
        server_default=ObligationStatus.OPEN.value,
    )


class LiabilityEvent(Base):
    __tablename__ = "liability_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_liability_events_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "event_kind",
            "document_kind",
            "document_id",
            "source_reference",
            name="uq_liability_events_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                f"{SCHEMA}.payment_obligations.tenant_id",
                f"{SCHEMA}.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_liability_events_tenant_obligation",
        ),
        CheckConstraint("amount <> 0", name="ck_liability_events_amount_nonzero"),
        Index(
            "ix_liability_events_tenant_supplier",
            "tenant_id",
            "supplier_ref",
            "currency_code",
        ),
        Index(
            "ix_liability_events_tenant_document",
            "tenant_id",
            "document_kind",
            "document_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    obligation_id: Mapped[UUID | None] = mapped_column(Uuid())
    event_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    document_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    supplier_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CreditApplication(Base):
    __tablename__ = "credit_applications"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_credit_applications_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "credit_note_id"],
            [f"{SCHEMA}.credit_notes.tenant_id", f"{SCHEMA}.credit_notes.id"],
            ondelete="RESTRICT",
            name="fk_credit_applications_tenant_credit",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                f"{SCHEMA}.payment_obligations.tenant_id",
                f"{SCHEMA}.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_credit_applications_tenant_obligation",
        ),
        CheckConstraint("amount > 0", name="ck_credit_applications_amount_positive"),
        Index("ix_credit_applications_tenant_credit", "tenant_id", "credit_note_id"),
        Index("ix_credit_applications_tenant_obligation", "tenant_id", "obligation_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    credit_note_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    applied_by: Mapped[str] = mapped_column(String(255), nullable=False)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SettlementObservation(Base):
    __tablename__ = "settlement_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_settlement_observations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_reference",
            "source_version",
            name="uq_settlement_observations_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                f"{SCHEMA}.payment_obligations.tenant_id",
                f"{SCHEMA}.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_settlement_observations_tenant_obligation",
        ),
        CheckConstraint(
            "amount > 0", name="ck_settlement_observations_amount_positive"
        ),
        Index(
            "ix_settlement_observations_tenant_obligation", "tenant_id", "obligation_id"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    obligation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    source_version: Mapped[str] = mapped_column(String(120), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AccountingReceipt(Base):
    __tablename__ = "accounting_receipts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_accounting_receipts_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "document_kind",
            "document_id",
            name="uq_accounting_receipts_document",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    document_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    document_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    consequence_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    accounting_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    accounting_evidence_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    SupplierInvoice,
    SupplierInvoiceLine,
    CreditNote,
    CreditNoteLine,
    PaymentObligation,
    LiabilityEvent,
    CreditApplication,
    SettlementObservation,
    AccountingReceipt,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)


def metadata_table(name: str) -> sa.Table:
    return Base.metadata.tables[f"{SCHEMA}.{name}"]


__all__ = [
    "ALL_MODELS",
    "TABLES",
    "AccountingReceipt",
    "CreditApplication",
    "CreditNote",
    "CreditNoteLine",
    "LiabilityEvent",
    "PaymentObligation",
    "SettlementObservation",
    "SupplierInvoice",
    "SupplierInvoiceLine",
    "metadata_table",
]
