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
    Boolean,
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
    SupplierPaymentStatus,
)

SCHEMA = module_schema("payables")
MONEY = Numeric(20, 6)
RATE = Numeric(20, 10)

# Lifecycle evidence and separation of duties are database invariants as well as
# service rules, so a writer that bypasses the service still cannot forge them.
_NO_REVERSAL = (
    "reversed_by IS NULL AND reversed_at IS NULL AND reversal_reason IS NULL"
    " AND reversal_date IS NULL"
)
_APPROVED_EVIDENCE = (
    "approved_by IS NOT NULL AND approved_at IS NOT NULL"
    " AND approval_reference IS NOT NULL"
)
_RECORDED_EVIDENCE = (
    "recorded_by IS NOT NULL AND recorded_at IS NOT NULL"
    " AND bank_reference IS NOT NULL"
)
_NO_RECORDING = "recorded_by IS NULL AND recorded_at IS NULL AND bank_reference IS NULL"
_NO_CANCEL = "cancelled_by IS NULL AND cancelled_at IS NULL AND cancel_reason IS NULL"
SUPPLIER_PAYMENT_EVIDENCE_CHECK = (
    "(status = 'DRAFT' AND approved_by IS NULL AND approved_at IS NULL"
    " AND approval_reference IS NULL AND "
    + _NO_RECORDING
    + " AND "
    + _NO_CANCEL
    + " AND "
    + _NO_REVERSAL
    + ")"
    " OR (status = 'APPROVED' AND "
    + _APPROVED_EVIDENCE
    + " AND "
    + _NO_RECORDING
    + " AND "
    + _NO_CANCEL
    + " AND "
    + _NO_REVERSAL
    + ")"
    " OR (status = 'RECORDED' AND "
    + _APPROVED_EVIDENCE
    + " AND "
    + _RECORDED_EVIDENCE
    + " AND "
    + _NO_CANCEL
    + " AND "
    + _NO_REVERSAL
    + ")"
    " OR (status = 'CANCELLED' AND "
    + _NO_RECORDING
    + " AND cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL"
    " AND cancel_reason IS NOT NULL AND " + _NO_REVERSAL + ")"
    " OR (status = 'REVERSED' AND "
    + _APPROVED_EVIDENCE
    + " AND "
    + _RECORDED_EVIDENCE
    + " AND "
    + _NO_CANCEL
    + " AND reversed_by IS NOT NULL AND reversed_at IS NOT NULL"
    " AND reversal_reason IS NOT NULL AND reversal_date IS NOT NULL)"
)
# D-9: separation of duties is an optional per-tenant policy. The policy in force
# is stamped on each row at preparation (`separation_enforced`, immutable); the
# database relaxes the check for a row only when that stamp is false.
SUPPLIER_PAYMENT_SOD_CHECK = (
    "NOT separation_enforced OR ("
    "(approved_by IS NULL OR lower(approved_by) <> lower(prepared_by))"
    " AND (recorded_by IS NULL OR (lower(recorded_by) <> lower(prepared_by)"
    " AND lower(recorded_by) <> lower(approved_by)))"
    " AND (reversed_by IS NULL OR (lower(reversed_by) <> lower(recorded_by)"
    " AND lower(reversed_by) <> lower(approved_by)))"
    ")"
)


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
    supplier_document_number: Mapped[str | None] = mapped_column(String(120))
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
    supplier_document_number: Mapped[str | None] = mapped_column(String(120))
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


class HistoricalInvoiceReceipt(Base):
    """Permanent original-recognition evidence; never a contemporary GL receipt."""

    __tablename__ = "historical_invoice_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_historical_invoice_receipts_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "invoice_id", name="uq_historical_invoice_receipts_invoice"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_invoice_id",
            name="uq_historical_invoice_receipts_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            name="fk_historical_invoice_receipts_invoice",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                f"{SCHEMA}.payment_obligations.tenant_id",
                f"{SCHEMA}.payment_obligations.id",
            ],
            name="fk_historical_invoice_receipts_obligation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [f"{SCHEMA}.liability_events.tenant_id", f"{SCHEMA}.liability_events.id"],
            name="fk_historical_invoice_receipts_event",
            ondelete="RESTRICT",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    invoice_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_invoice_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    facts_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    accounting_evidence_fingerprint: Mapped[str] = mapped_column(
        String(68), nullable=False
    )
    source_journal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_journal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    original_posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SupplierPayment(Base, TimestampMixin):
    """A governed proposal to settle supplier obligations from one cash account.

    DRAFT -> APPROVED -> RECORDED, or CANCELLED from DRAFT/APPROVED. RECORDED
    asserts that money left through an external channel; it is not an execution.
    """

    __tablename__ = "supplier_payments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_supplier_payments_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "number", name="uq_supplier_payments_tenant_number"
        ),
        UniqueConstraint(
            "tenant_id",
            "payer_instrument_ref",
            "bank_reference",
            name="uq_supplier_payments_bank_reference",
        ),
        CheckConstraint(
            "length(currency_code) = 3", name="ck_supplier_payments_currency"
        ),
        CheckConstraint("exchange_rate > 0", name="ck_supplier_payments_rate_positive"),
        CheckConstraint(
            "gross_amount > 0 AND wht_amount >= 0 AND wht_amount < gross_amount"
            " AND net_amount = gross_amount - wht_amount",
            name="ck_supplier_payments_amounts",
        ),
        CheckConstraint(
            SUPPLIER_PAYMENT_EVIDENCE_CHECK, name="ck_supplier_payments_evidence"
        ),
        CheckConstraint(SUPPLIER_PAYMENT_SOD_CHECK, name="ck_supplier_payments_sod"),
        Index("ix_supplier_payments_tenant_supplier", "tenant_id", "supplier_ref"),
        Index("ix_supplier_payments_tenant_status", "tenant_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    number: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    supplier_name_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    value_date: Mapped[date] = mapped_column(Date, nullable=False)
    cash_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    payer_instrument_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    wht_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    separation_enforced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )
    status: Mapped[SupplierPaymentStatus] = mapped_column(
        _enum(SupplierPaymentStatus, "payables_supplier_payment_status"),
        nullable=False,
        default=SupplierPaymentStatus.DRAFT,
        server_default=SupplierPaymentStatus.DRAFT.value,
    )
    prepared_by: Mapped[str] = mapped_column(String(255), nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    approval_reference: Mapped[str | None] = mapped_column(String(255))
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bank_reference: Mapped[str | None] = mapped_column(String(255))
    recorded_by: Mapped[str | None] = mapped_column(String(255))
    recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[str | None] = mapped_column(String(255))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    reversed_by: Mapped[str | None] = mapped_column(String(255))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[str | None] = mapped_column(Text)
    reversal_date: Mapped[date | None] = mapped_column(Date)


class SupplierPaymentAllocation(Base):
    __tablename__ = "supplier_payment_allocations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_supplier_payment_allocations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "payment_id",
            "line_number",
            name="uq_supplier_payment_allocations_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "payment_id",
            "obligation_id",
            name="uq_supplier_payment_allocations_obligation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            [f"{SCHEMA}.supplier_payments.tenant_id", f"{SCHEMA}.supplier_payments.id"],
            ondelete="RESTRICT",
            name="fk_supplier_payment_allocations_payment",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                f"{SCHEMA}.payment_obligations.tenant_id",
                f"{SCHEMA}.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_supplier_payment_allocations_obligation",
        ),
        CheckConstraint("amount > 0", name="ck_supplier_payment_allocations_amount"),
        Index(
            "ix_supplier_payment_allocations_tenant_obligation",
            "tenant_id",
            "obligation_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    payment_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SupplierPaymentWithholding(Base):
    __tablename__ = "supplier_payment_withholdings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_supplier_payment_withholdings_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "payment_id",
            "line_number",
            name="uq_supplier_payment_withholdings_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            [f"{SCHEMA}.supplier_payments.tenant_id", f"{SCHEMA}.supplier_payments.id"],
            ondelete="RESTRICT",
            name="fk_supplier_payment_withholdings_payment",
        ),
        CheckConstraint("amount > 0", name="ck_supplier_payment_withholdings_amount"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    payment_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255))
    certificate_ref: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class InvoiceRecognition(Base):
    """Permanent record that an imported APPROVED invoice was recognized."""

    __tablename__ = "invoice_recognitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_invoice_recognitions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "invoice_id", name="uq_invoice_recognitions_invoice"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_invoice_id",
            name="uq_invoice_recognitions_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            ondelete="RESTRICT",
            name="fk_invoice_recognitions_invoice",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    invoice_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    recognized_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approval_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_invoice_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    facts_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    recognized_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HistoricalSettlementRecord(Base):
    """One immutable legacy settlement of an admitted historical invoice."""

    __tablename__ = "historical_settlements"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_historical_settlements_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "receipt_id",
            "line_number",
            name="uq_historical_settlements_number",
        ),
        # A legacy allocation settles exactly one invoice, across all admissions.
        UniqueConstraint(
            "tenant_id",
            "source_settlement_id",
            name="uq_historical_settlements_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "receipt_id"],
            [
                f"{SCHEMA}.historical_invoice_receipts.tenant_id",
                f"{SCHEMA}.historical_invoice_receipts.id",
            ],
            ondelete="RESTRICT",
            name="fk_historical_settlements_receipt",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                f"{SCHEMA}.payment_obligations.tenant_id",
                f"{SCHEMA}.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_historical_settlements_obligation",
        ),
        CheckConstraint(
            "applied_amount > 0 AND withholding_amount >= 0"
            " AND withholding_amount <= applied_amount",
            name="ck_historical_settlements_amounts",
        ),
        CheckConstraint("exchange_rate > 0", name="ck_historical_settlements_rate"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    receipt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    obligation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_settlement_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payment_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    settled_on: Mapped[date] = mapped_column(Date, nullable=False)
    applied_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    withholding_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    source_row_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    source_journal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_journal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class TaxDeferralPolicy(Base):
    """Owner fact: tax booked to one account is deferred and released on payment."""

    __tablename__ = "tax_deferral_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_tax_deferral_policies_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "deferral_account_ref",
            name="uq_tax_deferral_policies_deferral",
        ),
        CheckConstraint(
            "deferral_account_ref <> release_account_ref",
            name="ck_tax_deferral_policies_accounts",
        ),
        CheckConstraint(
            "minor_units >= 0 AND minor_units <= 6",
            name="ck_tax_deferral_policies_minor_units",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    deferral_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    release_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, nullable=False)
    declared_by: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class SupplierPaymentTaxRelease(Base):
    """Deferred input tax released by one payment against one invoice.

    The account refs are copied from the policy at the moment of release, so the
    consequence is rebuilt from frozen rows and never from policy read later.
    """

    __tablename__ = "supplier_payment_tax_releases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_supplier_payment_tax_releases_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "payment_id",
            "line_number",
            name="uq_supplier_payment_tax_releases_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "payment_id",
            "invoice_id",
            "deferral_account_ref",
            name="uq_supplier_payment_tax_releases_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "payment_id"],
            [f"{SCHEMA}.supplier_payments.tenant_id", f"{SCHEMA}.supplier_payments.id"],
            ondelete="RESTRICT",
            name="fk_supplier_payment_tax_releases_payment",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            ondelete="RESTRICT",
            name="fk_supplier_payment_tax_releases_invoice",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                f"{SCHEMA}.tax_deferral_policies.tenant_id",
                f"{SCHEMA}.tax_deferral_policies.id",
            ],
            ondelete="RESTRICT",
            name="fk_supplier_payment_tax_releases_policy",
        ),
        CheckConstraint("amount > 0", name="ck_supplier_payment_tax_releases_amount"),
        CheckConstraint(
            "deferral_account_ref <> release_account_ref",
            name="ck_supplier_payment_tax_releases_accounts",
        ),
        Index(
            "ix_supplier_payment_tax_releases_tenant_invoice", "tenant_id", "invoice_id"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    payment_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    deferral_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    release_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HistoricalTaxDeferralRecord(Base):
    """Permanent, verifier-bound record of the deferral released before admission."""

    __tablename__ = "historical_tax_deferrals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_historical_tax_deferrals_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "invoice_id",
            "deferral_account_ref",
            name="uq_historical_tax_deferrals_invoice",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            ondelete="RESTRICT",
            name="fk_historical_tax_deferrals_invoice",
        ),
        CheckConstraint(
            "deferred_tax_total > 0 AND released_before_admission >= 0"
            " AND remaining >= 0"
            " AND remaining = deferred_tax_total - released_before_admission",
            name="ck_historical_tax_deferrals_amounts",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    invoice_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    deferral_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_invoice_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    facts_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    accounting_evidence_fingerprint: Mapped[str] = mapped_column(
        String(68), nullable=False
    )
    deferred_tax_total: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    released_before_admission: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    remaining: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HistoricalCreditReceipt(Base):
    """Permanent evidence that a historical overpayment was admitted as a credit."""

    __tablename__ = "historical_credit_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_historical_credit_receipts_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "credit_note_id", name="uq_historical_credit_receipts_credit"
        ),
        UniqueConstraint(
            "tenant_id", "invoice_id", name="uq_historical_credit_receipts_invoice"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_invoice_id",
            name="uq_historical_credit_receipts_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credit_note_id"],
            [f"{SCHEMA}.credit_notes.tenant_id", f"{SCHEMA}.credit_notes.id"],
            ondelete="RESTRICT",
            name="fk_historical_credit_receipts_credit",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            ondelete="RESTRICT",
            name="fk_historical_credit_receipts_invoice",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [f"{SCHEMA}.liability_events.tenant_id", f"{SCHEMA}.liability_events.id"],
            ondelete="RESTRICT",
            name="fk_historical_credit_receipts_event",
        ),
        CheckConstraint(
            "credit_amount > 0", name="ck_historical_credit_receipts_credit"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    credit_note_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    invoice_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_invoice_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    facts_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    accounting_evidence_fingerprint: Mapped[str] = mapped_column(
        String(68), nullable=False
    )
    source_journal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_journal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    carried_evidence_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    evidence_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    admitted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    credit_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    carried_control_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    original_posted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class HistoricalCreditSettlementRecord(Base):
    """One immutable legacy settlement of an overpaid historical invoice."""

    __tablename__ = "historical_credit_settlements"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_historical_credit_settlements_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "receipt_id",
            "line_number",
            name="uq_historical_credit_settlements_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_settlement_id",
            name="uq_historical_credit_settlements_source",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "receipt_id"],
            [
                f"{SCHEMA}.historical_credit_receipts.tenant_id",
                f"{SCHEMA}.historical_credit_receipts.id",
            ],
            ondelete="RESTRICT",
            name="fk_historical_credit_settlements_receipt",
        ),
        CheckConstraint(
            "applied_amount > 0 AND withholding_amount >= 0"
            " AND withholding_amount <= applied_amount",
            name="ck_historical_credit_settlements_amounts",
        ),
        CheckConstraint(
            "exchange_rate > 0", name="ck_historical_credit_settlements_rate"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    receipt_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_settlement_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payment_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    settled_on: Mapped[date] = mapped_column(Date, nullable=False)
    applied_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    withholding_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(RATE, nullable=False)
    source_row_fingerprint: Mapped[str] = mapped_column(String(68), nullable=False)
    source_journal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_journal_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CreditNoteTaxSplit(Base):
    """How one credit-note line's deferral-account tax splits at approval.

    ``deferred_amount`` reverses the deferral account; ``released_amount`` reverses
    the release account (the share of the invoice's VAT already claimed). Frozen, so
    the credit consequence is rebuilt from rows and never from later policy or state.
    """

    __tablename__ = "credit_note_tax_splits"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_credit_note_tax_splits_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "credit_note_id",
            "line_number",
            name="uq_credit_note_tax_splits_line",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credit_note_id"],
            [f"{SCHEMA}.credit_notes.tenant_id", f"{SCHEMA}.credit_notes.id"],
            ondelete="RESTRICT",
            name="fk_credit_note_tax_splits_credit",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            ondelete="RESTRICT",
            name="fk_credit_note_tax_splits_invoice",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                f"{SCHEMA}.tax_deferral_policies.tenant_id",
                f"{SCHEMA}.tax_deferral_policies.id",
            ],
            ondelete="RESTRICT",
            name="fk_credit_note_tax_splits_policy",
        ),
        CheckConstraint(
            "deferred_amount >= 0 AND released_amount >= 0"
            " AND deferred_amount + released_amount > 0",
            name="ck_credit_note_tax_splits_amounts",
        ),
        CheckConstraint(
            "deferral_account_ref <> release_account_ref",
            name="ck_credit_note_tax_splits_accounts",
        ),
        Index("ix_credit_note_tax_splits_tenant_invoice", "tenant_id", "invoice_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    credit_note_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    line_number: Mapped[int] = mapped_column(Integer, nullable=False)
    invoice_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    deferral_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    release_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    deferred_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    released_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class CreditApplicationTaxRelease(Base):
    """Deferred input tax released by one credit application (a sibling of the
    payment release rows, so payment rows and their keys stay untouched)."""

    __tablename__ = "credit_application_tax_releases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_credit_application_tax_releases_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "application_id",
            "deferral_account_ref",
            name="uq_credit_application_tax_releases_release",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "application_id"],
            [
                f"{SCHEMA}.credit_applications.tenant_id",
                f"{SCHEMA}.credit_applications.id",
            ],
            ondelete="RESTRICT",
            name="fk_credit_application_tax_releases_application",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [f"{SCHEMA}.supplier_invoices.tenant_id", f"{SCHEMA}.supplier_invoices.id"],
            ondelete="RESTRICT",
            name="fk_credit_application_tax_releases_invoice",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                f"{SCHEMA}.tax_deferral_policies.tenant_id",
                f"{SCHEMA}.tax_deferral_policies.id",
            ],
            ondelete="RESTRICT",
            name="fk_credit_application_tax_releases_policy",
        ),
        CheckConstraint("amount > 0", name="ck_credit_application_tax_releases_amount"),
        CheckConstraint(
            "deferral_account_ref <> release_account_ref",
            name="ck_credit_application_tax_releases_accounts",
        ),
        Index(
            "ix_credit_application_tax_releases_tenant_invoice",
            "tenant_id",
            "invoice_id",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    application_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    invoice_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    deferral_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    release_account_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    CreditNoteTaxSplit,
    CreditApplicationTaxRelease,
    TaxDeferralPolicy,
    SupplierPaymentTaxRelease,
    HistoricalTaxDeferralRecord,
    HistoricalCreditReceipt,
    HistoricalCreditSettlementRecord,
    InvoiceRecognition,
    HistoricalSettlementRecord,
    HistoricalInvoiceReceipt,
    SupplierInvoice,
    SupplierInvoiceLine,
    CreditNote,
    CreditNoteLine,
    PaymentObligation,
    LiabilityEvent,
    CreditApplication,
    SettlementObservation,
    AccountingReceipt,
    SupplierPayment,
    SupplierPaymentAllocation,
    SupplierPaymentWithholding,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)


def metadata_table(name: str) -> sa.Table:
    return Base.metadata.tables[f"{SCHEMA}.{name}"]


__all__ = [
    "ALL_MODELS",
    "CreditApplicationTaxRelease",
    "CreditNoteTaxSplit",
    "HistoricalCreditReceipt",
    "HistoricalCreditSettlementRecord",
    "HistoricalTaxDeferralRecord",
    "SupplierPaymentTaxRelease",
    "TaxDeferralPolicy",
    "HistoricalInvoiceReceipt",
    "HistoricalSettlementRecord",
    "InvoiceRecognition",
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
    "SupplierPayment",
    "SupplierPaymentAllocation",
    "SupplierPaymentWithholding",
    "SUPPLIER_PAYMENT_EVIDENCE_CHECK",
    "SUPPLIER_PAYMENT_SOD_CHECK",
    "metadata_table",
]
