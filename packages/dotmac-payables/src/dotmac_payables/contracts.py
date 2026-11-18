"""Pure commands and provider-neutral consequences for ``dotmac-payables``."""

from __future__ import annotations

import enum
from dataclasses import dataclass, fields
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.semantic_encoding import (
    ABSENT,
    digest_of,
    encode_fields,
    encode_ordered,
)
from sqlalchemy.orm import Session


class PayablesError(ValueError):
    """Base for supplier-liability refusals.

    ``code`` is an optional machine-readable refusal code (a6, additive). Every
    refusal introduced in a6 sets it and also carries it in parentheses in the
    message, as the a5 refusals do in text; existing raises are unchanged.
    """

    code: str | None

    def __init__(self, *args: object, code: str | None = None) -> None:
        super().__init__(*args)
        self.code = code


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


class SupplierPaymentStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    RECORDED = "RECORDED"
    CANCELLED = "CANCELLED"
    REVERSED = "REVERSED"


def _required(value: str, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise PayablesError(f"{label} is required")
    if len(normalized) > maximum:
        raise PayablesError(f"{label} must be at most {maximum} characters")
    return normalized


_MONEY_QUANTUM = Decimal("0.000001")


def _exact_money(value: Decimal, label: str) -> None:
    if value != value.quantize(_MONEY_QUANTUM):
        raise InvalidAmount(f"{label} must have at most 6 decimal places")


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


@dataclass(frozen=True, slots=True)
class HistoricalUnpaidInvoiceCommand:
    source_owner: str
    source_invoice_id: str
    invoice_id: UUID
    source_snapshot_sha256: str
    evidence_fingerprint: str

    def validate(self) -> None:
        for value, label, maximum in (
            (self.source_owner, "source owner", 120),
            (self.source_invoice_id, "source invoice id", 255),
        ):
            if _required(value, label, maximum) != value:
                raise PayablesError(f"{label} must be canonical")
        if not isinstance(self.invoice_id, UUID):
            raise PayablesError("invoice id must be UUID")
        if _fingerprint(self.source_snapshot_sha256) != self.source_snapshot_sha256:
            raise PayablesError("snapshot digest must be canonical")
        _historical_digest(self.evidence_fingerprint)


def _historical_digest(value: str) -> str:
    if not value.startswith("cv1:") or _fingerprint(value[4:]) != value[4:]:
        raise PayablesError("historical evidence requires a canonical cv1 fingerprint")
    return value


@dataclass(frozen=True, slots=True)
class HistoricalUnpaidInvoiceEvidence:
    tenant_id: UUID
    source_owner: str
    source_invoice_id: str
    source_snapshot_sha256: str
    invoice_id: UUID
    source_status: str
    invoice_type: str
    paid_amount: Decimal
    allocation_count: int
    withholding_tax_amount: Decimal
    prepayment_amount: Decimal
    number: str
    supplier_ref: str
    supplier_name_snapshot: str
    supplier_document_number: str | None
    invoice_date: date
    received_date: date
    due_date: date
    currency_code: str
    exchange_rate: Decimal
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    liability_account_ref: str
    procurement_ref: str | None
    receipt_evidence_fingerprint: str | None
    submitted_by: str | None
    approved_by: str | None
    approval_reference: str | None
    submitted_at: datetime | None
    approved_at: datetime | None
    original_posted_at: datetime
    source_journal_ref: str
    destination_journal_ref: str
    accounting_evidence_fingerprint: str
    facts_fingerprint: str

    def fingerprint(self) -> str:
        values: list[tuple[str, object]] = []
        for field in fields(self):
            value = getattr(self, field.name)
            if value is None:
                value = ABSENT
            elif isinstance(value, UUID):
                value = str(value)
            elif isinstance(value, date) and not isinstance(value, datetime):
                value = value.isoformat()
            values.append((field.name, value))
        return digest_of("payables.historical-unpaid-invoice.v1", encode_fields(values))


class HistoricalUnpaidInvoiceVerifier(Protocol):
    def verify(
        self,
        *,
        scope: TenantScope,
        command: HistoricalUnpaidInvoiceCommand,
        destination: Session,
    ) -> HistoricalUnpaidInvoiceEvidence: ...


__all__ += [
    "HistoricalUnpaidInvoiceCommand",
    "HistoricalUnpaidInvoiceEvidence",
    "HistoricalUnpaidInvoiceVerifier",
]


@dataclass(frozen=True, slots=True)
class SupplierPaymentAllocationInput:
    """One slice of a supplier payment applied to one open obligation."""

    obligation_id: UUID
    amount: Decimal

    def payload(self) -> dict[str, str]:
        if self.amount <= 0:
            raise InvalidAmount("payment allocation amount must be positive")
        _exact_money(self.amount, "payment allocation amount")
        return {"obligation_id": str(self.obligation_id), "amount": str(self.amount)}


@dataclass(frozen=True, slots=True)
class SupplierPaymentWithholdingInput:
    """Optional withholding tax deducted from the cash paid to the supplier."""

    account_ref: str
    amount: Decimal
    description: str | None = None
    certificate_ref: str | None = None

    def payload(self) -> dict[str, object]:
        if self.amount <= 0:
            raise InvalidAmount("withholding amount must be positive")
        _exact_money(self.amount, "withholding amount")
        return {
            "account_ref": _required(
                self.account_ref, "withholding account reference", 255
            ),
            "amount": str(self.amount),
            "description": _required(self.description, "withholding description", 255)
            if self.description is not None
            else None,
            "certificate_ref": _required(
                self.certificate_ref, "withholding certificate reference", 255
            )
            if self.certificate_ref is not None
            else None,
        }


@dataclass(frozen=True, slots=True)
class CreateSupplierPayment:
    """Proposal to pay one supplier from one cash account against open obligations.

    The payment is a statement of intent until it is RECORDED, and RECORDED is an
    assertion by a human that money left through an external channel. Nothing here
    moves money or calls a provider.
    """

    number: str
    supplier_ref: str
    supplier_name_snapshot: str
    currency_code: str
    exchange_rate: Decimal
    value_date: date
    cash_account_ref: str
    payer_instrument_ref: str
    allocations: tuple[SupplierPaymentAllocationInput, ...]
    prepared_by: str
    withholdings: tuple[SupplierPaymentWithholdingInput, ...] = ()

    def fingerprint_payload(self) -> dict[str, object]:
        if not self.allocations:
            raise InvalidAmount("supplier payment requires at least one allocation")
        if self.exchange_rate <= 0:
            raise InvalidAmount("exchange rate must be greater than zero")
        allocation_payloads = [item.payload() for item in self.allocations]
        withholding_payloads = [item.payload() for item in self.withholdings]
        obligation_ids = [item.obligation_id for item in self.allocations]
        if len(obligation_ids) != len(set(obligation_ids)):
            raise InvalidAmount("an obligation may be allocated once per payment")
        gross = sum((item.amount for item in self.allocations), Decimal("0"))
        withheld = sum((item.amount for item in self.withholdings), Decimal("0"))
        if withheld >= gross:
            raise InvalidAmount("withholding must be less than the gross payment")
        return {
            "number": _required(self.number, "payment number", 50).upper(),
            "supplier_ref": _required(self.supplier_ref, "supplier reference", 255),
            "supplier_name_snapshot": _required(
                self.supplier_name_snapshot, "supplier name snapshot", 255
            ),
            "currency_code": _currency(self.currency_code),
            "exchange_rate": str(self.exchange_rate),
            "value_date": self.value_date.isoformat(),
            "cash_account_ref": _required(
                self.cash_account_ref, "cash account reference", 255
            ),
            "payer_instrument_ref": _required(
                self.payer_instrument_ref, "payer instrument reference", 255
            ),
            "prepared_by": _required(self.prepared_by, "prepared by", 255),
            "allocations": allocation_payloads,
            "withholdings": withholding_payloads,
        }


__all__ += [
    "CreateSupplierPayment",
    "SupplierPaymentAllocationInput",
    "SupplierPaymentStatus",
    "SupplierPaymentWithholdingInput",
]


@dataclass(frozen=True, slots=True)
class RecognizeApprovedInvoice:
    """Recognize an imported APPROVED invoice that never reached the owner ledger.

    ``evidence_reference`` (the assembly's reviewed cohort), ``recognized_by`` and
    ``approval_reference`` are recorded permanently; the assembly enforces who may
    act. The source fields bind the command to the trusted verifier's snapshot
    evidence (``ApprovedInvoiceRecognitionEvidence``), which is what proves the
    legacy invoice was never POSTED.
    """

    invoice_id: UUID
    evidence_reference: str
    recognized_by: str
    approval_reference: str
    source_owner: str
    source_invoice_id: str
    source_snapshot_sha256: str
    evidence_fingerprint: str

    def validate(self) -> None:
        if not isinstance(self.invoice_id, UUID):
            raise PayablesError("invoice id must be UUID")
        _required(self.evidence_reference, "evidence reference", 255)
        _required(self.recognized_by, "recognized by", 255)
        _required(self.approval_reference, "approval reference", 255)
        for value, label in (
            (self.source_owner, "source owner"),
            (self.source_invoice_id, "source invoice id"),
        ):
            if _required(value, label, 255) != value:
                raise PayablesError(f"{label} must be canonical")
        if _fingerprint(self.source_snapshot_sha256) != self.source_snapshot_sha256:
            raise PayablesError("snapshot digest must be canonical")
        _historical_digest(self.evidence_fingerprint)


@dataclass(frozen=True, slots=True)
class ApprovedInvoiceRecognitionEvidence:
    """Verifier proof that a legacy invoice was APPROVED and never POSTED.

    The trusted verifier reads a restored legacy snapshot read-only and returns
    the legacy status, the absence of any legacy journal entry, posting date and
    GL lines for the invoice, plus the destination facts it matched.
    """

    tenant_id: UUID
    source_owner: str
    source_invoice_id: str
    source_snapshot_sha256: str
    invoice_id: UUID
    source_status: str
    source_journal_entry_id: str | None
    source_posted_at: datetime | None
    source_gl_line_count: int
    number: str
    supplier_ref: str
    currency_code: str
    total_amount: Decimal
    facts_fingerprint: str

    def fingerprint(self) -> str:
        values: list[tuple[str, object]] = []
        for field in fields(self):
            value = getattr(self, field.name)
            if value is None:
                value = ABSENT
            elif isinstance(value, UUID):
                value = str(value)
            values.append((field.name, value))
        return digest_of(
            "payables.approved-invoice-recognition.v1", encode_fields(values)
        )


class ApprovedInvoiceRecognitionVerifier(Protocol):
    def verify(
        self,
        *,
        scope: TenantScope,
        command: RecognizeApprovedInvoice,
        destination: Session,
    ) -> ApprovedInvoiceRecognitionEvidence: ...


@dataclass(frozen=True, slots=True)
class HistoricalSettlement:
    """One legacy settlement of a historical invoice, proven by the verifier.

    Verifier contract (the ERP assembly implements it): read the restored
    flip-window legacy snapshot read-only; a settlement is one legacy payment
    allocation joined to a payment whose status is posted, whose payment journal
    is POSTED in the legacy GL (``source_journal_ref``) and whose imported journal
    exists in the destination (``destination_journal_ref``);
    ``source_row_fingerprint`` is the sha256 of the canonical legacy allocation
    row; the residual is cross-checked against the legacy GL control-account
    lines for the invoice. Money is exact two-decimal (correct for NGN); a
    currency with other minor units is refused until this contract is widened.
    """

    source_settlement_id: str
    payment_ref: str
    settled_on: date
    applied_amount: Decimal
    withholding_amount: Decimal
    currency_code: str
    exchange_rate: Decimal
    source_row_fingerprint: str
    source_journal_ref: str
    destination_journal_ref: str

    def validate(self) -> None:
        _required(self.source_settlement_id, "source settlement id", 255)
        _required(self.payment_ref, "legacy payment reference", 255)
        _required(self.source_journal_ref, "settlement source journal reference", 255)
        _required(
            self.destination_journal_ref,
            "settlement destination journal reference",
            255,
        )
        _currency(self.currency_code)
        if not isinstance(self.settled_on, date) or isinstance(
            self.settled_on, datetime
        ):
            raise PayablesError("settlement date must be a date")
        _historical_digest(self.source_row_fingerprint)
        if not self.exchange_rate.is_finite() or self.exchange_rate <= 0:
            raise InvalidAmount("settlement exchange rate must be positive")
        for value in (self.applied_amount, self.withholding_amount):
            if (
                not isinstance(value, Decimal)
                or not value.is_finite()
                or value != value.quantize(Decimal("0.01"))
            ):
                raise InvalidAmount("settlement money must be exact two-decimal")
        if self.applied_amount <= 0:
            raise InvalidAmount("settlement amount must be positive")
        if self.withholding_amount < 0 or self.withholding_amount > self.applied_amount:
            raise InvalidAmount("settlement withholding must lie within the amount")


@dataclass(frozen=True, slots=True)
class HistoricalSettledInvoiceCommand:
    source_owner: str
    source_invoice_id: str
    invoice_id: UUID
    source_snapshot_sha256: str
    evidence_fingerprint: str

    def validate(self) -> None:
        HistoricalUnpaidInvoiceCommand(
            self.source_owner,
            self.source_invoice_id,
            self.invoice_id,
            self.source_snapshot_sha256,
            self.evidence_fingerprint,
        ).validate()


@dataclass(frozen=True, slots=True)
class HistoricalSettledInvoiceEvidence:
    """Verifier proof for a POSTED STANDARD invoice with legacy settlements.

    ``settled_amount`` is the legacy total settled, counting withholding tax as
    settled; ``residual_amount`` must equal the face less every settlement.
    """

    tenant_id: UUID
    source_owner: str
    source_invoice_id: str
    source_snapshot_sha256: str
    invoice_id: UUID
    source_status: str
    invoice_type: str
    settlements: tuple[HistoricalSettlement, ...]
    settled_amount: Decimal
    residual_amount: Decimal
    withholding_tax_amount: Decimal
    prepayment_amount: Decimal
    number: str
    supplier_ref: str
    supplier_name_snapshot: str
    supplier_document_number: str | None
    invoice_date: date
    received_date: date
    due_date: date
    currency_code: str
    exchange_rate: Decimal
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    liability_account_ref: str
    procurement_ref: str | None
    receipt_evidence_fingerprint: str | None
    submitted_by: str | None
    approved_by: str | None
    approval_reference: str | None
    submitted_at: datetime | None
    approved_at: datetime | None
    original_posted_at: datetime
    source_journal_ref: str
    destination_journal_ref: str
    accounting_evidence_fingerprint: str
    facts_fingerprint: str

    def fingerprint(self) -> str:
        def scalar(value: object) -> object:
            if value is None:
                return ABSENT
            if isinstance(value, UUID):
                return str(value)
            if isinstance(value, date) and not isinstance(value, datetime):
                return value.isoformat()
            return value

        values: list[tuple[str, object]] = []
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "settlements":
                value = encode_ordered(
                    encode_fields(
                        [(f.name, scalar(getattr(item, f.name))) for f in fields(item)]
                    )
                    for item in value
                )
            values.append((field.name, scalar(value)))
        return digest_of(
            "payables.historical-settled-invoice.v1", encode_fields(values)
        )


class HistoricalSettledInvoiceVerifier(Protocol):
    def verify(
        self,
        *,
        scope: TenantScope,
        command: HistoricalSettledInvoiceCommand,
        destination: Session,
    ) -> HistoricalSettledInvoiceEvidence: ...


__all__ += [
    "ApprovedInvoiceRecognitionEvidence",
    "ApprovedInvoiceRecognitionVerifier",
    "HistoricalSettledInvoiceCommand",
    "HistoricalSettledInvoiceEvidence",
    "HistoricalSettledInvoiceVerifier",
    "HistoricalSettlement",
    "RecognizeApprovedInvoice",
]


# --- a6: cash-basis deferred-VAT release, historical deferral, historical credit ---


def _scalar(value: object) -> object:
    if value is None:
        return ABSENT
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    return value


def _two_decimal(value: object, label: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value != value.quantize(Decimal("0.01"))
    ):
        raise InvalidAmount(f"{label} must be exact finite two-decimal money")


@dataclass(frozen=True, slots=True)
class DeclareTaxDeferral:
    """Owner fact: tax booked to ``deferral_account_ref`` is released to
    ``release_account_ref`` in proportion to the outstanding balance paid.

    A line is deferred exactly when its ``tax_account_ref`` equals a declared
    deferral account. The declaration is immutable and never metadata.
    """

    deferral_account_ref: str
    release_account_ref: str
    minor_units: int
    declared_by: str
    evidence_reference: str

    def validate(self) -> None:
        deferral = _required(self.deferral_account_ref, "deferral account", 255)
        release = _required(self.release_account_ref, "release account", 255)
        _required(self.declared_by, "declared by", 255)
        _required(self.evidence_reference, "evidence reference", 255)
        if type(self.minor_units) is not int or not 0 <= self.minor_units <= 6:
            raise InvalidAmount("minor units must be an integer from 0 to 6")
        if deferral == release:
            raise Conflict(
                "deferral and release account must differ (tax_deferral_same_account)",
                code="tax_deferral_same_account",
            )


def deferred_tax_release(
    remaining: Decimal, allocated: Decimal, outstanding: Decimal, minor_units: int
) -> Decimal:
    """Deferred tax released by a payment of ``allocated`` against ``outstanding``.

    The final payment (``allocated == outstanding``) releases the exact
    ``remaining``; otherwise the proportional share is rounded half up to
    ``minor_units`` and never exceeds ``remaining``. Pure: no policy is read.
    """
    if remaining < 0:
        raise InvalidAmount(
            "deferred tax position is negative (deferred_tax_position_negative)",
            code="deferred_tax_position_negative",
        )
    if outstanding <= 0 or allocated <= 0 or allocated > outstanding:
        raise InvalidAmount("allocation must lie within the outstanding balance")
    if allocated == outstanding:
        return remaining
    quantum = Decimal(1).scaleb(-minor_units)
    share = (remaining * allocated / outstanding).quantize(
        quantum, rounding=ROUND_HALF_UP
    )
    return min(share, remaining)


@dataclass(frozen=True, slots=True)
class DeferredTaxPosition:
    """Deferred-tax position of one invoice against one deferral account."""

    deferral_account_ref: str
    deferred: Decimal
    released_before_admission: Decimal
    credited: Decimal
    released: Decimal
    remaining: Decimal


@dataclass(frozen=True, slots=True)
class HistoricalTaxDeferralCommand:
    source_owner: str
    source_invoice_id: str
    invoice_id: UUID
    deferral_account_ref: str
    source_snapshot_sha256: str
    evidence_fingerprint: str

    def validate(self) -> None:
        for value, label, maximum in (
            (self.source_owner, "source owner", 120),
            (self.source_invoice_id, "source invoice id", 255),
            (self.deferral_account_ref, "deferral account", 255),
        ):
            if _required(value, label, maximum) != value:
                raise PayablesError(f"{label} must be canonical")
        if not isinstance(self.invoice_id, UUID):
            raise PayablesError("invoice id must be UUID")
        if _fingerprint(self.source_snapshot_sha256) != self.source_snapshot_sha256:
            raise PayablesError("snapshot digest must be canonical")
        _historical_digest(self.evidence_fingerprint)


@dataclass(frozen=True, slots=True)
class HistoricalTaxDeferralRelease:
    """One opaque piece of evidence for a release before admission.

    The verifier defines what ``evidence_ref`` names (a legacy reclass journal, a
    reviewed opening-schedule row, ...); the owner only checks the amounts add up.
    """

    evidence_ref: str
    released_amount: Decimal
    evidence_fingerprint: str

    def validate(self) -> None:
        _required(self.evidence_ref, "release evidence reference", 255)
        _two_decimal(self.released_amount, "released amount")
        if self.released_amount <= 0:
            raise InvalidAmount("a release evidence amount must be positive")
        _historical_digest(self.evidence_fingerprint)


@dataclass(frozen=True, slots=True)
class HistoricalTaxDeferralEvidence:
    """Verifier proof of the deferral already released before admission.

    Basis-neutral (a6, spec 5b): ``released_before_admission`` is the deferred tax
    that left the deferral account before the invoice reached this owner, and
    ``releases`` is opaque evidence whose meaning the verifier defines.
    """

    tenant_id: UUID
    source_owner: str
    source_invoice_id: str
    source_snapshot_sha256: str
    invoice_id: UUID
    deferral_account_ref: str
    deferred_tax_total: Decimal
    releases: tuple[HistoricalTaxDeferralRelease, ...]
    released_before_admission: Decimal
    remaining: Decimal
    accounting_evidence_fingerprint: str
    facts_fingerprint: str

    def fingerprint(self) -> str:
        values: list[tuple[str, object]] = []
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "releases":
                value = encode_ordered(
                    encode_fields(
                        [(f.name, _scalar(getattr(item, f.name))) for f in fields(item)]
                    )
                    for item in value
                )
            values.append((field.name, _scalar(value)))
        return digest_of("payables.historical-tax-deferral.v1", encode_fields(values))


class HistoricalTaxDeferralVerifier(Protocol):
    def verify(
        self,
        *,
        scope: TenantScope,
        command: HistoricalTaxDeferralCommand,
        destination: Session,
    ) -> HistoricalTaxDeferralEvidence: ...


@dataclass(frozen=True, slots=True)
class HistoricalSupplierCreditCommand:
    source_owner: str
    source_invoice_id: str
    invoice_id: UUID
    credit_number: str
    source_snapshot_sha256: str
    evidence_fingerprint: str
    evidence_reference: str
    admitted_by: str

    def validate(self) -> None:
        for value, label, maximum in (
            (self.source_owner, "source owner", 120),
            (self.source_invoice_id, "source invoice id", 255),
            (self.credit_number, "credit number", 50),
        ):
            if _required(value, label, maximum) != value:
                raise PayablesError(f"{label} must be canonical")
        if not isinstance(self.invoice_id, UUID):
            raise PayablesError("invoice id must be UUID")
        if _fingerprint(self.source_snapshot_sha256) != self.source_snapshot_sha256:
            raise PayablesError("snapshot digest must be canonical")
        _historical_digest(self.evidence_fingerprint)
        _required(self.evidence_reference, "evidence reference", 255)
        _required(self.admitted_by, "admitted by", 255)


@dataclass(frozen=True, slots=True)
class HistoricalSupplierCreditEvidence:
    """Verifier proof that a settled legacy invoice was overpaid.

    ``credit_amount`` is ``settled_amount - total_amount`` (> 0).
    ``carried_control_amount`` is the supplier control-account net, in the
    credit-positive convention, carried for the invoice and its settlements: an
    overpayment leaves a net DEBIT, so it must equal ``-credit_amount`` exactly.
    ``carried_evidence_ref`` names where that figure was read (basis-neutral).
    """

    tenant_id: UUID
    source_owner: str
    source_invoice_id: str
    source_snapshot_sha256: str
    invoice_id: UUID
    source_status: str
    invoice_type: str
    number: str
    supplier_ref: str
    supplier_name_snapshot: str
    currency_code: str
    exchange_rate: Decimal
    total_amount: Decimal
    liability_account_ref: str
    settlements: tuple[HistoricalSettlement, ...]
    settled_amount: Decimal
    withholding_tax_amount: Decimal
    credit_amount: Decimal
    carried_control_amount: Decimal
    carried_evidence_ref: str
    original_posted_at: datetime
    source_journal_ref: str
    destination_journal_ref: str
    accounting_evidence_fingerprint: str
    facts_fingerprint: str

    def fingerprint(self) -> str:
        values: list[tuple[str, object]] = []
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "settlements":
                value = encode_ordered(
                    encode_fields(
                        [(f.name, _scalar(getattr(item, f.name))) for f in fields(item)]
                    )
                    for item in value
                )
            values.append((field.name, _scalar(value)))
        return digest_of(
            "payables.historical-supplier-credit.v1", encode_fields(values)
        )


class HistoricalSupplierCreditVerifier(Protocol):
    def verify(
        self,
        *,
        scope: TenantScope,
        command: HistoricalSupplierCreditCommand,
        destination: Session,
    ) -> HistoricalSupplierCreditEvidence: ...


__all__ += [
    "DeclareTaxDeferral",
    "DeferredTaxPosition",
    "HistoricalSupplierCreditCommand",
    "HistoricalSupplierCreditEvidence",
    "HistoricalSupplierCreditVerifier",
    "HistoricalTaxDeferralCommand",
    "HistoricalTaxDeferralEvidence",
    "HistoricalTaxDeferralRelease",
    "HistoricalTaxDeferralVerifier",
    "deferred_tax_release",
]
