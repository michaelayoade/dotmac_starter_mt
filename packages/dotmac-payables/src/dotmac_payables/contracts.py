"""Pure commands and provider-neutral consequences for ``dotmac-payables``."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID


class PayablesError(ValueError):
    """Base for supplier-liability refusals."""


class NotFound(PayablesError):
    pass


class Conflict(PayablesError):
    pass


class InvalidAmount(PayablesError):
    pass


class InvalidTransition(PayablesError):
    pass


class InvoiceStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    VOID = "VOID"


class CreditNoteStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    VOID = "VOID"


class ObligationStatus(enum.StrEnum):
    OPEN = "OPEN"
    PARTIALLY_SETTLED = "PARTIALLY_SETTLED"
    SETTLED = "SETTLED"


def _required(value: str, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise PayablesError(f"{label} is required")
    if len(normalized) > maximum:
        raise PayablesError(f"{label} must be at most {maximum} characters")
    return normalized


def _currency(value: str) -> str:
    currency = _required(value, "currency code", 3).upper()
    if len(currency) != 3:
        raise PayablesError("currency code must contain exactly three characters")
    return currency


def _fingerprint(value: str, label: str = "fingerprint") -> str:
    result = value.strip().lower()
    if len(result) != 64 or any(c not in "0123456789abcdef" for c in result):
        raise PayablesError(f"{label} must be a 64-character SHA-256")
    return result


@dataclass(frozen=True, slots=True)
class PayableLineInput:
    description: str
    quantity: Decimal
    unit_price: Decimal
    posting_account_ref: str
    tax_amount: Decimal = Decimal("0")
    tax_account_ref: str | None = None
    dimension_refs: tuple[tuple[str, str], ...] = ()

    def validate(self) -> None:
        if self.quantity <= 0:
            raise InvalidAmount("line quantity must be positive")
        if self.unit_price < 0 or self.tax_amount < 0:
            raise InvalidAmount("line price and tax cannot be negative")
        _required(self.description, "line description", 2000)
        _required(self.posting_account_ref, "posting account reference", 255)
        if self.tax_amount > 0 and not self.tax_account_ref:
            raise InvalidAmount("a taxed line requires a tax account reference")
        codes: list[str] = []
        for code, value in self.dimension_refs:
            codes.append(_required(code, "dimension code", 40).upper())
            _required(value, "dimension value", 120)
        if len(codes) != len(set(codes)):
            raise PayablesError("a line may supply one value per accounting dimension")

    @property
    def line_amount(self) -> Decimal:
        return self.quantity * self.unit_price

    @property
    def total_amount(self) -> Decimal:
        return self.line_amount + self.tax_amount

    def payload(self) -> dict[str, object]:
        self.validate()
        return {
            "description": self.description.strip(),
            "quantity": str(self.quantity),
            "unit_price": str(self.unit_price),
            "posting_account_ref": self.posting_account_ref.strip(),
            "tax_amount": str(self.tax_amount),
            "tax_account_ref": self.tax_account_ref.strip()
            if self.tax_account_ref
            else None,
            "dimension_refs": sorted(
                (code.strip().upper(), value.strip())
                for code, value in self.dimension_refs
            ),
        }


@dataclass(frozen=True, slots=True)
class ObligationSchedule:
    due_date: date
    amount: Decimal

    def payload(self) -> dict[str, str]:
        if self.amount <= 0:
            raise InvalidAmount("payment obligation amount must be positive")
        return {"due_date": self.due_date.isoformat(), "amount": str(self.amount)}


@dataclass(frozen=True, slots=True)
class CreateSupplierInvoice:
    number: str
    supplier_ref: str
    supplier_name_snapshot: str
    supplier_document_number: str
    invoice_date: date
    received_date: date
    currency_code: str
    exchange_rate: Decimal
    liability_account_ref: str
    lines: tuple[PayableLineInput, ...]
    schedule: tuple[ObligationSchedule, ...]
    procurement_ref: str | None = None
    receipt_evidence_fingerprint: str | None = None

    def fingerprint_payload(self) -> dict[str, object]:
        if not self.lines:
            raise InvalidAmount("supplier invoice requires at least one line")
        if not self.schedule:
            raise InvalidAmount("supplier invoice requires a payment schedule")
        if self.exchange_rate <= 0:
            raise InvalidAmount("exchange rate must be greater than zero")
        total = sum((line.total_amount for line in self.lines), Decimal("0"))
        if total <= 0:
            raise InvalidAmount("standard supplier invoice total must be positive")
        schedule_total = sum((item.amount for item in self.schedule), Decimal("0"))
        if schedule_total != total:
            raise InvalidAmount("payment schedule must equal the invoice total")
        if any(item.due_date < self.invoice_date for item in self.schedule):
            raise InvalidAmount(
                "payment obligation due date cannot precede invoice date"
            )
        receipt_fingerprint = None
        if self.receipt_evidence_fingerprint is not None:
            receipt_fingerprint = _fingerprint(
                self.receipt_evidence_fingerprint, "receipt evidence fingerprint"
            )
        procurement_ref = (
            _required(self.procurement_ref, "procurement reference", 255)
            if self.procurement_ref is not None
            else None
        )
        return {
            "number": _required(self.number, "invoice number", 50).upper(),
            "supplier_ref": _required(self.supplier_ref, "supplier reference", 255),
            "supplier_name_snapshot": _required(
                self.supplier_name_snapshot, "supplier name snapshot", 255
            ),
            "supplier_document_number": _required(
                self.supplier_document_number, "supplier document number", 120
            ),
            "invoice_date": self.invoice_date.isoformat(),
            "received_date": self.received_date.isoformat(),
            "currency_code": _currency(self.currency_code),
            "exchange_rate": str(self.exchange_rate),
            "liability_account_ref": _required(
                self.liability_account_ref, "liability account reference", 255
            ),
            "procurement_ref": procurement_ref,
            "receipt_evidence_fingerprint": receipt_fingerprint,
            "lines": [line.payload() for line in self.lines],
            "schedule": [item.payload() for item in self.schedule],
        }


@dataclass(frozen=True, slots=True)
class CreateCreditNote:
    number: str
    supplier_ref: str
    supplier_name_snapshot: str
    supplier_document_number: str
    credit_date: date
    currency_code: str
    exchange_rate: Decimal
    liability_account_ref: str
    lines: tuple[PayableLineInput, ...]
    original_invoice_id: UUID | None = None

    def fingerprint_payload(self) -> dict[str, object]:
        if not self.lines:
            raise InvalidAmount("supplier credit note requires at least one line")
        if self.exchange_rate <= 0:
            raise InvalidAmount("exchange rate must be greater than zero")
        total = sum((line.total_amount for line in self.lines), Decimal("0"))
        if total <= 0:
            raise InvalidAmount("supplier credit note total must be positive")
        return {
            "number": _required(self.number, "credit note number", 50).upper(),
            "supplier_ref": _required(self.supplier_ref, "supplier reference", 255),
            "supplier_name_snapshot": _required(
                self.supplier_name_snapshot, "supplier name snapshot", 255
            ),
            "supplier_document_number": _required(
                self.supplier_document_number, "supplier document number", 120
            ),
            "credit_date": self.credit_date.isoformat(),
            "currency_code": _currency(self.currency_code),
            "exchange_rate": str(self.exchange_rate),
            "liability_account_ref": _required(
                self.liability_account_ref, "liability account reference", 255
            ),
            "original_invoice_id": str(self.original_invoice_id)
            if self.original_invoice_id
            else None,
            "lines": [line.payload() for line in self.lines],
        }


@dataclass(frozen=True, slots=True)
class ApplyCredit:
    credit_note_id: UUID
    obligation_id: UUID
    amount: Decimal
    applied_by: str


@dataclass(frozen=True, slots=True)
class SettlementObservationInput:
    obligation_id: UUID
    source_owner: str
    source_reference: str
    source_version: str
    source_fingerprint: str
    amount: Decimal
    occurred_at: datetime
    currency_code: str

    def fingerprint_payload(self) -> dict[str, object]:
        if self.amount <= 0:
            raise InvalidAmount("settlement amount must be positive")
        return {
            "obligation_id": str(self.obligation_id),
            "source_owner": _required(
                self.source_owner, "settlement source owner", 120
            ),
            "source_reference": _required(
                self.source_reference, "settlement source reference", 255
            ),
            "source_version": _required(
                self.source_version, "settlement source version", 120
            ),
            "source_fingerprint": _fingerprint(
                self.source_fingerprint, "settlement source fingerprint"
            ),
            "amount": str(self.amount),
            "currency_code": _currency(self.currency_code),
            "occurred_at": self.occurred_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class AccountingReceiptInput:
    document_kind: str
    document_id: UUID
    consequence_fingerprint: str
    accounting_reference: str
    accounting_evidence_fingerprint: str


@dataclass(frozen=True, slots=True)
class ConsequenceSource:
    owner: str
    document_kind: str
    document_id: str
    version: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class AccountingEntry:
    account_ref: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str | None = None
    dimension_refs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class AccountingConsequence:
    source: ConsequenceSource
    posting_date: date
    description: str
    currency_code: str
    exchange_rate: Decimal
    entries: tuple[AccountingEntry, ...]
    fingerprint: str


__all__ = [
    "AccountingConsequence",
    "AccountingEntry",
    "AccountingReceiptInput",
    "ApplyCredit",
    "Conflict",
    "ConsequenceSource",
    "CreateCreditNote",
    "CreateSupplierInvoice",
    "CreditNoteStatus",
    "InvalidAmount",
    "InvalidTransition",
    "InvoiceStatus",
    "NotFound",
    "ObligationSchedule",
    "ObligationStatus",
    "PayableLineInput",
    "PayablesError",
    "SettlementObservationInput",
]
