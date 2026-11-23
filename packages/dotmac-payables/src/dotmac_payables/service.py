"""Supplier-liability lifecycle in the caller's transaction.

Payables owns documents, liabilities and obligations. It produces a typed
accounting consequence but never imports or writes Accounting, and it accepts
settlement only as an immutable observation from the payment owner.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import execute_once, fingerprint_of
from dotmac_kernel.semantic_encoding import canonical_instant
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, scoped_session

from dotmac_payables.contracts import (
    AccountingConsequence,
    AccountingEntry,
    AccountingReceiptInput,
    ApplyCredit,
    ApprovedInvoiceRecognitionEvidence,
    ApprovedInvoiceRecognitionVerifier,
    Conflict,
    ConsequenceSource,
    CreateCreditNote,
    CreateSupplierInvoice,
    CreateSupplierPayment,
    CreditNoteStatus,
    DeclareTaxDeferral,
    DeferredTaxPosition,
    HistoricalSettledInvoiceCommand,
    HistoricalSettledInvoiceEvidence,
    HistoricalSettledInvoiceVerifier,
    HistoricalSupplierCreditCommand,
    HistoricalSupplierCreditEvidence,
    HistoricalSupplierCreditVerifier,
    HistoricalTaxDeferralCommand,
    HistoricalTaxDeferralEvidence,
    HistoricalTaxDeferralVerifier,
    HistoricalUnpaidInvoiceCommand,
    HistoricalUnpaidInvoiceEvidence,
    HistoricalUnpaidInvoiceVerifier,
    InvalidAmount,
    InvalidTransition,
    InvoiceStatus,
    NotFound,
    ObligationStatus,
    PayablesError,
    RecognizeApprovedInvoice,
    SettlementObservationInput,
    SupplierPaymentStatus,
    deferred_tax_release,
)
from dotmac_payables.models import (
    AccountingReceipt,
    CreditApplication,
    CreditApplicationTaxRelease,
    CreditNote,
    CreditNoteLine,
    CreditNoteTaxSplit,
    HistoricalCreditReceipt,
    HistoricalCreditSettlementRecord,
    HistoricalInvoiceReceipt,
    HistoricalSettlementRecord,
    HistoricalTaxDeferralRecord,
    InvoiceRecognition,
    LiabilityEvent,
    PaymentObligation,
    SettlementObservation,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierPayment,
    SupplierPaymentAllocation,
    SupplierPaymentTaxRelease,
    SupplierPaymentWithholding,
    TaxDeferralPolicy,
)

MONEY_QUANTUM = Decimal("0.000001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _required(value: str, label: str, maximum: int) -> str:
    result = value.strip()
    if not result:
        raise InvalidTransition(f"{label} is required")
    if len(result) > maximum:
        raise InvalidTransition(f"{label} must be at most {maximum} characters")
    return result


def _fingerprint(value: str, label: str) -> str:
    result = value.strip().lower()
    if len(result) != 64 or any(c not in "0123456789abcdef" for c in result):
        raise InvalidTransition(f"{label} must be a 64-character SHA-256")
    return result


def _refuse(exc_type: type[PayablesError], message: str, code: str) -> PayablesError:
    """Build an a6 refusal: the code is an attribute and also in the message."""
    return exc_type(f"{message} ({code})", code=code)


def _check_consequence_entries(entries: list[AccountingEntry]) -> None:
    """Backstop for the document builders: never emit an unpostable shape."""
    if (
        not entries
        or any(e.debit < 0 or e.credit < 0 for e in entries)
        or any(e.debit == 0 and e.credit == 0 for e in entries)
        or sum((e.debit for e in entries), Decimal("0"))
        != sum((e.credit for e in entries), Decimal("0"))
    ):
        raise _refuse(
            InvalidAmount,
            "accounting consequence entries are empty, unbalanced or hold a "
            "zero entry",
            "consequence_entry_invalid",
        )


def _lock_settlement_ids(
    session: Session, tenant_id: UUID, settlement_ids: list[str]
) -> None:
    """Serialise admissions that name the same legacy settlement (PostgreSQL).

    A legacy settlement settles one invoice across both admission tables, and the
    cross-table check cannot be a unique key, so two concurrent admissions are made
    to queue on a transaction-scoped advisory lock keyed by (tenant, settlement id),
    taken in sorted order. The lock is released with the transaction.
    """
    if session.get_bind().dialect.name != "postgresql":
        return
    for settlement_id in sorted(set(settlement_ids)):
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"payables.settlement:{tenant_id}:{settlement_id}"},
        )


def _flush_unique(db: Session, row: object, *, label: str) -> None:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(f"{label} already exists") from exc


def _invoice(
    db: Session, tenant_id: UUID, invoice_id: UUID, *, lock: bool = False
) -> SupplierInvoice:
    stmt = select(SupplierInvoice).where(
        SupplierInvoice.tenant_id == tenant_id, SupplierInvoice.id == invoice_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("supplier invoice was not found")
    return row


def _credit(
    db: Session, tenant_id: UUID, credit_note_id: UUID, *, lock: bool = False
) -> CreditNote:
    stmt = select(CreditNote).where(
        CreditNote.tenant_id == tenant_id, CreditNote.id == credit_note_id
    )
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("supplier credit note was not found")
    return row


def _obligation(
    db: Session, tenant_id: UUID, obligation_id: UUID, *, lock: bool = False
) -> PaymentObligation:
    stmt = select(PaymentObligation).where(
        PaymentObligation.tenant_id == tenant_id,
        PaymentObligation.id == obligation_id,
    )
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("payment obligation was not found")
    return row


def _invoice_lines(
    db: Session, tenant_id: UUID, invoice_id: UUID
) -> list[SupplierInvoiceLine]:
    return list(
        db.scalars(
            select(SupplierInvoiceLine)
            .where(
                SupplierInvoiceLine.tenant_id == tenant_id,
                SupplierInvoiceLine.invoice_id == invoice_id,
            )
            .order_by(SupplierInvoiceLine.line_number)
        )
    )


def _credit_lines(
    db: Session, tenant_id: UUID, credit_id: UUID
) -> list[CreditNoteLine]:
    return list(
        db.scalars(
            select(CreditNoteLine)
            .where(
                CreditNoteLine.tenant_id == tenant_id,
                CreditNoteLine.credit_note_id == credit_id,
            )
            .order_by(CreditNoteLine.line_number)
        )
    )


def create_invoice(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateSupplierInvoice,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    recorded_at: datetime,
) -> SupplierInvoice:
    tenant_id = scope.tenant_id
    payload = command.fingerprint_payload()
    request_fingerprint = fingerprint_of(payload)
    normalized_lines = tuple(
        (
            _money(line.quantity),
            _money(line.unit_price),
            _money(_money(line.quantity) * _money(line.unit_price)),
            _money(line.tax_amount),
        )
        for line in command.lines
    )
    subtotal = _money(sum((values[2] for values in normalized_lines), Decimal("0")))
    tax = _money(sum((values[3] for values in normalized_lines), Decimal("0")))
    total = _money(subtotal + tax)
    normalized_schedule = [
        {"due_date": item.due_date.isoformat(), "amount": str(_money(item.amount))}
        for item in command.schedule
    ]
    if (
        sum((Decimal(item["amount"]) for item in normalized_schedule), Decimal("0"))
        != total
    ):
        raise InvalidAmount("rounded payment schedule must equal the invoice total")

    def operation(session: Session) -> Mapping[str, object]:
        existing = session.scalar(
            select(SupplierInvoice).where(
                SupplierInvoice.tenant_id == tenant_id,
                SupplierInvoice.supplier_ref == str(payload["supplier_ref"]),
                SupplierInvoice.supplier_document_number
                == str(payload["supplier_document_number"]),
            )
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise Conflict("supplier document identity names a different invoice")
            return {"invoice_id": str(existing.id)}
        invoice = SupplierInvoice(
            tenant_id=tenant_id,
            number=str(payload["number"]),
            supplier_ref=str(payload["supplier_ref"]),
            supplier_name_snapshot=str(payload["supplier_name_snapshot"]),
            supplier_document_number=str(payload["supplier_document_number"]),
            invoice_date=command.invoice_date,
            received_date=command.received_date,
            currency_code=str(payload["currency_code"]),
            exchange_rate=command.exchange_rate,
            liability_account_ref=str(payload["liability_account_ref"]),
            subtotal=subtotal,
            tax_amount=tax,
            total_amount=total,
            payment_schedule=normalized_schedule,
            procurement_ref=str(payload["procurement_ref"])
            if payload["procurement_ref"] is not None
            else None,
            receipt_evidence_fingerprint=str(payload["receipt_evidence_fingerprint"])
            if payload["receipt_evidence_fingerprint"] is not None
            else None,
            request_fingerprint=request_fingerprint,
            status=InvoiceStatus.DRAFT,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        session.add(invoice)
        session.flush()
        for number, (line, amounts) in enumerate(
            zip(command.lines, normalized_lines, strict=True), start=1
        ):
            quantity, unit_price, line_amount, tax_amount = amounts
            session.add(
                SupplierInvoiceLine(
                    tenant_id=tenant_id,
                    invoice_id=invoice.id,
                    line_number=number,
                    description=line.description.strip(),
                    quantity=quantity,
                    unit_price=unit_price,
                    line_amount=line_amount,
                    tax_amount=tax_amount,
                    posting_account_ref=line.posting_account_ref.strip(),
                    tax_account_ref=line.tax_account_ref.strip()
                    if line.tax_account_ref
                    else None,
                    dimension_refs=[
                        [code.strip().upper(), value.strip()]
                        for code, value in line.dimension_refs
                    ],
                    created_at=recorded_at,
                )
            )
        session.flush()
        return {"invoice_id": str(invoice.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.create_invoice",
        key=idempotency_key,
        operation=operation,
        fingerprint=request_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _invoice(db, tenant_id, UUID(str(outcome.result["invoice_id"])))


def submit_invoice(
    db: Session,
    *,
    scope: TenantScope,
    invoice_id: UUID,
    submitted_by: str,
    submitted_at: datetime,
) -> SupplierInvoice:
    invoice = _invoice(db, scope.tenant_id, invoice_id, lock=True)
    if invoice.status != InvoiceStatus.DRAFT:
        raise InvalidTransition("only a draft supplier invoice can be submitted")
    if not _invoice_lines(db, scope.tenant_id, invoice.id):
        raise InvalidAmount("supplier invoice requires at least one line")
    invoice.status = InvoiceStatus.SUBMITTED
    invoice.submitted_by = _required(submitted_by, "submitted by", 255)
    invoice.submitted_at = submitted_at
    invoice.updated_at = submitted_at
    db.flush()
    return invoice


def approve_invoice(
    db: Session,
    *,
    scope: TenantScope,
    invoice_id: UUID,
    approval_reference: str,
    approved_by: str,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    approved_at: datetime,
) -> SupplierInvoice:
    tenant_id = scope.tenant_id
    approval = _required(approval_reference, "approval reference", 255)
    actor = _required(approved_by, "approved by", 255)
    payload = {
        "invoice_id": str(invoice_id),
        "approval_reference": approval,
        "approved_by": actor,
        "approved_at": approved_at.isoformat(),
    }
    event_fingerprint = fingerprint_of(payload)

    def operation(session: Session) -> Mapping[str, object]:
        invoice = _invoice(session, tenant_id, invoice_id, lock=True)
        if invoice.status != InvoiceStatus.SUBMITTED:
            raise InvalidTransition("only a submitted supplier invoice can be approved")
        invoice.status = InvoiceStatus.APPROVED
        invoice.approval_reference = approval
        invoice.approved_by = actor
        invoice.approved_at = approved_at
        invoice.updated_at = approved_at
        for sequence, item in enumerate(invoice.payment_schedule, start=1):
            amount = _money(Decimal(str(item["amount"])))
            session.add(
                PaymentObligation(
                    tenant_id=tenant_id,
                    invoice_id=invoice.id,
                    sequence=sequence,
                    due_date=date.fromisoformat(str(item["due_date"])),
                    currency_code=invoice.currency_code,
                    original_amount=amount,
                    outstanding_amount=amount,
                    status=ObligationStatus.OPEN,
                    created_at=approved_at,
                    updated_at=approved_at,
                )
            )
        session.add(
            LiabilityEvent(
                tenant_id=tenant_id,
                event_kind="invoice_recognized",
                document_kind="supplier_invoice",
                document_id=invoice.id,
                supplier_ref=invoice.supplier_ref,
                currency_code=invoice.currency_code,
                amount=invoice.total_amount,
                source_reference=approval,
                source_fingerprint=event_fingerprint,
                occurred_at=approved_at,
            )
        )
        session.flush()
        return {"invoice_id": str(invoice.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.approve_invoice",
        key=idempotency_key,
        operation=operation,
        fingerprint=event_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _invoice(db, tenant_id, UUID(str(outcome.result["invoice_id"])))


def void_invoice(
    db: Session,
    *,
    scope: TenantScope,
    invoice_id: UUID,
    reason: str,
    voided_at: datetime,
) -> SupplierInvoice:
    invoice = _invoice(db, scope.tenant_id, invoice_id, lock=True)
    if invoice.status not in {InvoiceStatus.DRAFT, InvoiceStatus.SUBMITTED}:
        raise InvalidTransition("an approved supplier invoice requires a credit note")
    invoice.status = InvoiceStatus.VOID
    invoice.void_reason = _required(reason, "void reason", 2000)
    invoice.updated_at = voided_at
    db.flush()
    return invoice


def create_credit_note(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateCreditNote,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    recorded_at: datetime,
) -> CreditNote:
    tenant_id = scope.tenant_id
    payload = command.fingerprint_payload()
    request_fingerprint = fingerprint_of(payload)
    if command.original_invoice_id is not None:
        original = _invoice(db, tenant_id, command.original_invoice_id)
        if original.status != InvoiceStatus.APPROVED:
            raise InvalidTransition(
                "a credit note can reference only an approved invoice"
            )
        if original.supplier_ref != str(
            payload["supplier_ref"]
        ) or original.currency_code != str(payload["currency_code"]):
            raise InvalidTransition(
                "credit note must match original supplier and currency"
            )
    normalized_lines = tuple(
        (
            _money(line.quantity),
            _money(line.unit_price),
            _money(_money(line.quantity) * _money(line.unit_price)),
            _money(line.tax_amount),
        )
        for line in command.lines
    )
    subtotal = _money(sum((values[2] for values in normalized_lines), Decimal("0")))
    tax = _money(sum((values[3] for values in normalized_lines), Decimal("0")))
    total = _money(subtotal + tax)

    def operation(session: Session) -> Mapping[str, object]:
        existing = session.scalar(
            select(CreditNote).where(
                CreditNote.tenant_id == tenant_id,
                CreditNote.supplier_ref == str(payload["supplier_ref"]),
                CreditNote.supplier_document_number
                == str(payload["supplier_document_number"]),
            )
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise Conflict(
                    "supplier document identity names a different credit note"
                )
            return {"credit_note_id": str(existing.id)}
        credit = CreditNote(
            tenant_id=tenant_id,
            original_invoice_id=command.original_invoice_id,
            number=str(payload["number"]),
            supplier_ref=str(payload["supplier_ref"]),
            supplier_name_snapshot=str(payload["supplier_name_snapshot"]),
            supplier_document_number=str(payload["supplier_document_number"]),
            credit_date=command.credit_date,
            currency_code=str(payload["currency_code"]),
            exchange_rate=command.exchange_rate,
            liability_account_ref=str(payload["liability_account_ref"]),
            subtotal=subtotal,
            tax_amount=tax,
            total_amount=total,
            available_amount=Decimal("0"),
            request_fingerprint=request_fingerprint,
            status=CreditNoteStatus.DRAFT,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        session.add(credit)
        session.flush()
        for number, (line, amounts) in enumerate(
            zip(command.lines, normalized_lines, strict=True), start=1
        ):
            quantity, unit_price, line_amount, tax_amount = amounts
            session.add(
                CreditNoteLine(
                    tenant_id=tenant_id,
                    credit_note_id=credit.id,
                    line_number=number,
                    description=line.description.strip(),
                    quantity=quantity,
                    unit_price=unit_price,
                    line_amount=line_amount,
                    tax_amount=tax_amount,
                    posting_account_ref=line.posting_account_ref.strip(),
                    tax_account_ref=line.tax_account_ref.strip()
                    if line.tax_account_ref
                    else None,
                    dimension_refs=[
                        [code.strip().upper(), value.strip()]
                        for code, value in line.dimension_refs
                    ],
                    created_at=recorded_at,
                )
            )
        session.flush()
        return {"credit_note_id": str(credit.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.create_credit_note",
        key=idempotency_key,
        operation=operation,
        fingerprint=request_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _credit(db, tenant_id, UUID(str(outcome.result["credit_note_id"])))


def submit_credit_note(
    db: Session,
    *,
    scope: TenantScope,
    credit_note_id: UUID,
    submitted_by: str,
    submitted_at: datetime,
) -> CreditNote:
    credit = _credit(db, scope.tenant_id, credit_note_id, lock=True)
    if credit.status != CreditNoteStatus.DRAFT:
        raise InvalidTransition("only a draft supplier credit note can be submitted")
    credit.status = CreditNoteStatus.SUBMITTED
    credit.submitted_by = _required(submitted_by, "submitted by", 255)
    credit.submitted_at = submitted_at
    credit.updated_at = submitted_at
    db.flush()
    return credit


def _split_credit_deferred_tax(
    session: Session, credit: CreditNote, *, created_at: datetime
) -> None:
    """Split a credit note's deferral-account tax between reversing the deferral
    account and reversing the input VAT already claimed, and freeze the split.

    Per deferral account d: t is the credit's tax on d; the part that still sits on
    the deferral account (min(t, R)) reverses it, and the rest reverses the release
    account (the share already claimed). Exact subtraction, no rounding. The credit
    may not take more than the invoice's net tax, and may not be taxed to a release
    account directly (that would lower the claim without a split).
    """
    tenant_id = credit.tenant_id
    policies = _deferral_policies(session, tenant_id)
    if not policies:
        return
    lines = _credit_lines(session, tenant_id, credit.id)
    release_accounts = {p.release_account_ref: p for p in policies.values()}
    on_deferral = any(line.tax_account_ref in policies for line in lines)
    on_release = any(line.tax_account_ref in release_accounts for line in lines)
    if not on_deferral and not on_release:
        return
    if credit.original_invoice_id is None:
        if on_deferral:
            raise _refuse(
                InvalidTransition,
                "a credit note with deferred tax requires its original invoice",
                "deferred_tax_credit_requires_invoice",
            )
        return
    # Lock order: credit (held by the caller), then the invoice.
    _invoice(session, tenant_id, credit.original_invoice_id, lock=True)
    positions = {
        position.deferral_account_ref: position
        for position in invoice_deferred_tax_position(
            session, tenant_id, credit.original_invoice_id
        )
    }
    for position in positions.values():
        _require_historical_deferral_proven(
            session,
            tenant_id,
            credit.original_invoice_id,
            position.deferral_account_ref,
        )
    for line in lines:
        policy = release_accounts.get(line.tax_account_ref or "")
        if (
            policy is not None
            and line.tax_account_ref not in policies
            and policy.deferral_account_ref in positions
        ):
            raise _refuse(
                InvalidTransition,
                "a credit line cannot be taxed to a release account while the "
                "invoice defers tax",
                "credit_tax_on_release_account",
            )
    for account, policy in sorted(policies.items()):
        account_lines = [line for line in lines if line.tax_account_ref == account]
        total = _money(sum((line.tax_amount for line in account_lines), Decimal("0")))
        if total <= 0:
            continue
        current = positions.get(account)
        if current is None or total > _money(
            current.deferred - current.credited - current.credit_released
        ):
            raise _refuse(
                InvalidAmount,
                "credit note tax exceeds the invoice tax not already credited",
                "credit_tax_exceeds_invoice_tax",
            )
        pool = min(total, current.remaining)
        for line in account_lines:
            if line.tax_amount <= 0:
                continue
            deferred = min(_money(line.tax_amount), pool)
            pool = _money(pool - deferred)
            session.add(
                CreditNoteTaxSplit(
                    tenant_id=tenant_id,
                    credit_note_id=credit.id,
                    line_number=line.line_number,
                    invoice_id=credit.original_invoice_id,
                    policy_id=policy.id,
                    deferral_account_ref=policy.deferral_account_ref,
                    release_account_ref=policy.release_account_ref,
                    deferred_amount=deferred,
                    released_amount=_money(line.tax_amount - deferred),
                    created_at=created_at,
                )
            )
    session.flush()


def approve_credit_note(
    db: Session,
    *,
    scope: TenantScope,
    credit_note_id: UUID,
    approval_reference: str,
    approved_by: str,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    approved_at: datetime,
) -> CreditNote:
    tenant_id = scope.tenant_id
    approval = _required(approval_reference, "approval reference", 255)
    actor = _required(approved_by, "approved by", 255)
    payload = {
        "credit_note_id": str(credit_note_id),
        "approval_reference": approval,
        "approved_by": actor,
        "approved_at": approved_at.isoformat(),
    }
    event_fingerprint = fingerprint_of(payload)

    def operation(session: Session) -> Mapping[str, object]:
        credit = _credit(session, tenant_id, credit_note_id, lock=True)
        if credit.status != CreditNoteStatus.SUBMITTED:
            raise InvalidTransition(
                "only a submitted supplier credit note can be approved"
            )
        _split_credit_deferred_tax(session, credit, created_at=approved_at)
        credit.status = CreditNoteStatus.APPROVED
        credit.available_amount = credit.total_amount
        credit.approval_reference = approval
        credit.approved_by = actor
        credit.approved_at = approved_at
        credit.updated_at = approved_at
        session.add(
            LiabilityEvent(
                tenant_id=tenant_id,
                event_kind="credit_recognized",
                document_kind="credit_note",
                document_id=credit.id,
                supplier_ref=credit.supplier_ref,
                currency_code=credit.currency_code,
                amount=-credit.total_amount,
                source_reference=approval,
                source_fingerprint=event_fingerprint,
                occurred_at=approved_at,
            )
        )
        session.flush()
        return {"credit_note_id": str(credit.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.approve_credit_note",
        key=idempotency_key,
        operation=operation,
        fingerprint=event_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _credit(db, tenant_id, UUID(str(outcome.result["credit_note_id"])))


def void_credit_note(
    db: Session,
    *,
    scope: TenantScope,
    credit_note_id: UUID,
    reason: str,
    voided_at: datetime,
) -> CreditNote:
    credit = _credit(db, scope.tenant_id, credit_note_id, lock=True)
    if credit.status not in {CreditNoteStatus.DRAFT, CreditNoteStatus.SUBMITTED}:
        raise InvalidTransition("an approved supplier credit note cannot be voided")
    credit.status = CreditNoteStatus.VOID
    credit.void_reason = _required(reason, "void reason", 2000)
    credit.updated_at = voided_at
    db.flush()
    return credit


def _apply_obligation_reduction(obligation: PaymentObligation, amount: Decimal) -> None:
    if amount <= 0:
        raise InvalidAmount("applied amount must be positive")
    if amount > obligation.outstanding_amount:
        raise InvalidAmount("amount exceeds the obligation outstanding balance")
    obligation.outstanding_amount = _money(obligation.outstanding_amount - amount)
    if obligation.outstanding_amount == 0:
        obligation.status = ObligationStatus.SETTLED
    else:
        obligation.status = ObligationStatus.PARTIALLY_SETTLED


def _credit_is_cash(db: Session, tenant_id: UUID, credit_note_id: UUID) -> bool:
    """CASH credit: a historical overpayment (cash already paid); else DOCUMENT."""
    return (
        _historical_credit_receipt_for_credit(db, tenant_id, credit_note_id) is not None
    )


def _credit_application_releases(
    session: Session, *, credit: CreditNote, invoice: SupplierInvoice, amount: Decimal
) -> list[tuple[str, TaxDeferralPolicy, Decimal]]:
    """Deferred tax an application releases, per deferral account (before it is made).

    Called under the credit, obligation and invoice locks, before the obligation is
    reduced, so O is the balance the share is measured against.
    - CASH credit (cash already paid): payment-like, the same proportional rule with
      the exact remainder when the application settles the invoice.
    - DOCUMENT credit applied to its own invoice: nothing, except the final
      settlement, which releases exactly R.
    - DOCUMENT credit applied elsewhere: refused while the invoice still defers tax.
    """
    tenant_id = credit.tenant_id
    policies = _deferral_policies(session, tenant_id)
    if not policies:
        return []
    positions = invoice_deferred_tax_position(session, tenant_id, invoice.id)
    if not positions:
        return []
    for position in positions:
        _require_historical_deferral_proven(
            session, tenant_id, invoice.id, position.deferral_account_ref
        )
    outstanding = _money(
        Decimal(
            session.scalar(
                select(func.sum(PaymentObligation.outstanding_amount)).where(
                    PaymentObligation.tenant_id == tenant_id,
                    PaymentObligation.invoice_id == invoice.id,
                )
            )
            or 0
        )
    )
    cash = _credit_is_cash(session, tenant_id, credit.id)
    own = (not cash) and credit.original_invoice_id == invoice.id
    if not cash and not own:
        if any(position.remaining > 0 for position in positions):
            raise _refuse(
                InvalidTransition,
                "a document credit cannot be set off against an invoice that "
                "still defers tax",
                "credit_setoff_on_deferred_invoice",
            )
        return []
    releases: list[tuple[str, TaxDeferralPolicy, Decimal]] = []
    for position in positions:
        policy = policies[position.deferral_account_ref]
        if cash:
            release = deferred_tax_release(
                position.remaining, amount, outstanding, policy.minor_units
            )
        else:
            release = position.remaining if amount == outstanding else Decimal("0")
        if release > 0:
            releases.append((position.deferral_account_ref, policy, release))
    return releases


def credit_application_tax_releases(
    db: Session, tenant_id: UUID, application_id: UUID
) -> tuple[CreditApplicationTaxRelease, ...]:
    """Deferred-tax release rows of one credit application (none: nothing to post)."""
    return tuple(
        db.scalars(
            select(CreditApplicationTaxRelease)
            .where(
                CreditApplicationTaxRelease.tenant_id == tenant_id,
                CreditApplicationTaxRelease.application_id == application_id,
            )
            .order_by(CreditApplicationTaxRelease.deferral_account_ref)
        )
    )


def apply_credit_note(
    db: Session,
    *,
    scope: TenantScope,
    command: ApplyCredit,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    applied_at: datetime,
) -> CreditApplication:
    tenant_id = scope.tenant_id
    amount = _money(command.amount)
    payload = {
        "credit_note_id": str(command.credit_note_id),
        "obligation_id": str(command.obligation_id),
        "amount": str(amount),
        "applied_by": _required(command.applied_by, "applied by", 255),
        "applied_at": applied_at.isoformat(),
    }

    def operation(session: Session) -> Mapping[str, object]:
        credit = _credit(session, tenant_id, command.credit_note_id, lock=True)
        obligation = _obligation(session, tenant_id, command.obligation_id, lock=True)
        # Lock order: credit, obligation, invoice (as payment record and reversal).
        invoice = _invoice(session, tenant_id, obligation.invoice_id, lock=True)
        if credit.status != CreditNoteStatus.APPROVED:
            raise InvalidTransition("only an approved supplier credit can be applied")
        if (
            credit.supplier_ref != invoice.supplier_ref
            or credit.currency_code != invoice.currency_code
        ):
            raise InvalidTransition(
                "credit and obligation supplier/currency must match"
            )
        if amount > credit.available_amount:
            raise InvalidAmount("amount exceeds available supplier credit")
        # Validate the amount before any release is measured against it.
        if amount <= 0:
            raise InvalidAmount("applied amount must be positive")
        if amount > obligation.outstanding_amount:
            raise InvalidAmount("amount exceeds the obligation outstanding balance")
        releases = _credit_application_releases(
            session, credit=credit, invoice=invoice, amount=amount
        )
        _apply_obligation_reduction(obligation, amount)
        credit.available_amount = _money(credit.available_amount - amount)
        credit.updated_at = applied_at
        obligation.updated_at = applied_at
        application = CreditApplication(
            tenant_id=tenant_id,
            credit_note_id=credit.id,
            obligation_id=obligation.id,
            amount=amount,
            applied_by=str(payload["applied_by"]),
            applied_at=applied_at,
        )
        session.add(application)
        session.flush()
        for deferral_account, policy, release in releases:
            session.add(
                CreditApplicationTaxRelease(
                    tenant_id=tenant_id,
                    application_id=application.id,
                    invoice_id=invoice.id,
                    policy_id=policy.id,
                    deferral_account_ref=deferral_account,
                    release_account_ref=policy.release_account_ref,
                    amount=_money(release),
                    created_at=applied_at,
                )
            )
        session.flush()
        return {"application_id": str(application.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.apply_credit_note",
        key=idempotency_key,
        operation=operation,
        fingerprint=fingerprint_of(payload),
        expires_at=idempotency_expires_at,
    )
    application = db.scalar(
        select(CreditApplication).where(
            CreditApplication.tenant_id == tenant_id,
            CreditApplication.id == UUID(str(outcome.result["application_id"])),
        )
    )
    if application is None:
        raise NotFound("credit application was not found")
    return application


def record_settlement(
    db: Session,
    *,
    scope: TenantScope,
    command: SettlementObservationInput,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    recorded_at: datetime,
) -> SettlementObservation:
    """Record an external settlement observation.

    The payment owner string is reserved for ``record_supplier_payment``, which
    calls the private path; an external caller cannot claim it to skip the
    deferred-tax guard.
    """
    if command.source_owner.strip() == PAYMENT_SETTLEMENT_OWNER:
        raise _refuse(
            InvalidTransition,
            "the supplier-payment settlement owner is reserved",
            "settlement_owner_reserved",
        )
    return _record_settlement(
        db,
        scope=scope,
        command=command,
        idempotency_key=idempotency_key,
        idempotency_expires_at=idempotency_expires_at,
        recorded_at=recorded_at,
        from_payment=False,
    )


def _record_settlement(
    db: Session,
    *,
    scope: TenantScope,
    command: SettlementObservationInput,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    recorded_at: datetime,
    from_payment: bool,
) -> SettlementObservation:
    tenant_id = scope.tenant_id
    payload = command.fingerprint_payload()
    request_fingerprint = fingerprint_of(payload)
    amount = _money(command.amount)

    def operation(session: Session) -> Mapping[str, object]:
        existing = session.scalar(
            select(SettlementObservation).where(
                SettlementObservation.tenant_id == tenant_id,
                SettlementObservation.source_owner == str(payload["source_owner"]),
                SettlementObservation.source_reference
                == str(payload["source_reference"]),
                SettlementObservation.source_version == str(payload["source_version"]),
            )
        )
        if existing is not None:
            if (
                existing.source_fingerprint != str(payload["source_fingerprint"])
                or existing.obligation_id != command.obligation_id
                or existing.amount != amount
            ):
                raise Conflict(
                    "settlement source identity was replayed with different facts"
                )
            return {"observation_id": str(existing.id)}
        obligation = _obligation(session, tenant_id, command.obligation_id, lock=True)
        if from_payment:
            # The payment record already holds the invoice lock and has released
            # the deferred tax for this settlement.
            invoice = _invoice(session, tenant_id, obligation.invoice_id)
        else:
            # An external observation lowers O but releases nothing, so it may not
            # touch an invoice that still defers tax. Lock order: obligation, invoice.
            invoice = _invoice(session, tenant_id, obligation.invoice_id, lock=True)
            if any(
                position.remaining > 0
                for position in invoice_deferred_tax_position(
                    session, tenant_id, invoice.id
                )
            ):
                raise _refuse(
                    InvalidTransition,
                    "an external settlement cannot be observed on an invoice that "
                    "still defers tax",
                    "settlement_observation_on_deferred_invoice",
                )
        if obligation.currency_code != str(payload["currency_code"]):
            raise InvalidAmount("settlement currency does not match the obligation")
        _apply_obligation_reduction(obligation, amount)
        obligation.updated_at = recorded_at
        observation = SettlementObservation(
            tenant_id=tenant_id,
            obligation_id=obligation.id,
            source_owner=str(payload["source_owner"]),
            source_reference=str(payload["source_reference"]),
            source_version=str(payload["source_version"]),
            source_fingerprint=str(payload["source_fingerprint"]),
            currency_code=str(payload["currency_code"]),
            amount=amount,
            occurred_at=command.occurred_at,
            recorded_at=recorded_at,
        )
        session.add(observation)
        session.flush()
        session.add(
            LiabilityEvent(
                tenant_id=tenant_id,
                obligation_id=obligation.id,
                event_kind="settlement_observed",
                document_kind="settlement_observation",
                document_id=observation.id,
                supplier_ref=invoice.supplier_ref,
                currency_code=invoice.currency_code,
                amount=-amount,
                source_reference=str(payload["source_reference"]),
                source_fingerprint=request_fingerprint,
                occurred_at=command.occurred_at,
            )
        )
        session.flush()
        return {"observation_id": str(observation.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.record_settlement",
        key=idempotency_key,
        operation=operation,
        fingerprint=request_fingerprint,
        expires_at=idempotency_expires_at,
    )
    observation = db.scalar(
        select(SettlementObservation).where(
            SettlementObservation.tenant_id == tenant_id,
            SettlementObservation.id == UUID(str(outcome.result["observation_id"])),
        )
    )
    if observation is None:
        raise NotFound("settlement observation was not found")
    return observation


def _consequence(
    *,
    source: ConsequenceSource,
    posting_date: date,
    description: str,
    currency_code: str,
    exchange_rate: Decimal,
    entries: tuple[AccountingEntry, ...],
) -> AccountingConsequence:
    payload = {
        "source": {
            "owner": source.owner,
            "document_kind": source.document_kind,
            "document_id": source.document_id,
            "version": source.version,
            "fingerprint": source.fingerprint,
        },
        "posting_date": posting_date.isoformat(),
        "description": description,
        "currency_code": currency_code,
        "exchange_rate": str(exchange_rate),
        "entries": [
            {
                "account_ref": entry.account_ref,
                "debit": str(entry.debit),
                "credit": str(entry.credit),
                "description": entry.description,
                "dimension_refs": list(entry.dimension_refs),
            }
            for entry in entries
        ],
    }
    return AccountingConsequence(
        source=source,
        posting_date=posting_date,
        description=description,
        currency_code=currency_code,
        exchange_rate=exchange_rate,
        entries=entries,
        fingerprint=fingerprint_of(payload),
    )


def build_invoice_accounting_consequence(
    db: Session, *, tenant_id: UUID, invoice_id: UUID
) -> AccountingConsequence:
    invoice = _invoice(db, tenant_id, invoice_id, lock=True)
    if _historical_receipt(db, tenant_id, invoice_id) is not None:
        raise InvalidTransition(
            "historical invoice already has imported accounting evidence"
        )
    if _historical_credit_receipt_for_invoice(db, tenant_id, invoice_id) is not None:
        raise _refuse(
            InvalidTransition,
            "invoice carries a historical supplier credit and has no consequence",
            "invoice_carries_historical_credit",
        )
    recognized = db.scalar(
        select(LiabilityEvent.id)
        .where(
            LiabilityEvent.tenant_id == tenant_id,
            LiabilityEvent.document_kind == "supplier_invoice",
            LiabilityEvent.document_id == invoice_id,
            LiabilityEvent.event_kind == "invoice_recognized",
        )
        .limit(1)
    )
    if recognized is None:
        raise InvalidTransition("invoice has no owner recognition event")
    if invoice.status != InvoiceStatus.APPROVED:
        raise InvalidTransition(
            "only an approved invoice has an accounting consequence"
        )
    entries: list[AccountingEntry] = []
    for line in _invoice_lines(db, tenant_id, invoice.id):
        dimensions = tuple(
            (str(code), str(value)) for code, value in line.dimension_refs
        )
        # A zero-value line is document evidence only: the ledger refuses a 0/0
        # entry. A tax-only line keeps its tax entry below.
        if line.line_amount != 0:
            entries.append(
                AccountingEntry(
                    account_ref=line.posting_account_ref,
                    debit=line.line_amount,
                    description=line.description,
                    dimension_refs=dimensions,
                )
            )
        if line.tax_amount > 0:
            if not line.tax_account_ref:
                raise InvalidAmount("persisted taxed line has no tax account reference")
            entries.append(
                AccountingEntry(
                    account_ref=line.tax_account_ref,
                    debit=line.tax_amount,
                    description=f"Tax: {line.description}",
                    dimension_refs=dimensions,
                )
            )
    entries.append(
        AccountingEntry(
            account_ref=invoice.liability_account_ref,
            credit=invoice.total_amount,
            description=f"Supplier liability {invoice.number}",
        )
    )
    _check_consequence_entries(entries)
    return _consequence(
        source=ConsequenceSource(
            owner="payables",
            document_kind="supplier_invoice",
            document_id=str(invoice.id),
            version="1",
            fingerprint=invoice.request_fingerprint,
        ),
        posting_date=invoice.invoice_date,
        description=f"Supplier invoice {invoice.number}",
        currency_code=invoice.currency_code,
        exchange_rate=invoice.exchange_rate,
        entries=tuple(entries),
    )


def build_credit_accounting_consequence(
    db: Session, *, tenant_id: UUID, credit_note_id: UUID
) -> AccountingConsequence:
    credit = _credit(db, tenant_id, credit_note_id)
    if _historical_credit_receipt_for_credit(db, tenant_id, credit.id) is not None:
        raise _refuse(
            InvalidTransition,
            "a historical supplier credit has no accounting consequence "
            "(the carried ledger already holds it)",
            "historical_credit_has_no_consequence",
        )
    if credit.status != CreditNoteStatus.APPROVED:
        raise InvalidTransition(
            "only an approved credit note has an accounting consequence"
        )
    entries: list[AccountingEntry] = [
        AccountingEntry(
            account_ref=credit.liability_account_ref,
            debit=credit.total_amount,
            description=f"Supplier credit {credit.number}",
        )
    ]
    splits = {
        row.line_number: row
        for row in db.scalars(
            select(CreditNoteTaxSplit).where(
                CreditNoteTaxSplit.tenant_id == tenant_id,
                CreditNoteTaxSplit.credit_note_id == credit.id,
            )
        )
    }
    for line in _credit_lines(db, tenant_id, credit.id):
        dimensions = tuple(
            (str(code), str(value)) for code, value in line.dimension_refs
        )
        if line.line_amount != 0:
            entries.append(
                AccountingEntry(
                    account_ref=line.posting_account_ref,
                    credit=line.line_amount,
                    description=line.description,
                    dimension_refs=dimensions,
                )
            )
        if line.tax_amount > 0:
            if not line.tax_account_ref:
                raise InvalidAmount("persisted taxed line has no tax account reference")
            split = splits.get(line.line_number)
            if split is None:
                entries.append(
                    AccountingEntry(
                        account_ref=line.tax_account_ref,
                        credit=line.tax_amount,
                        description=f"Tax reversal: {line.description}",
                        dimension_refs=dimensions,
                    )
                )
            else:
                # Frozen at approval: the part still deferred reverses the deferral
                # account (the a5 entry, unchanged); the part already claimed
                # reverses the release account.
                if split.deferred_amount > 0:
                    entries.append(
                        AccountingEntry(
                            account_ref=line.tax_account_ref,
                            credit=split.deferred_amount,
                            description=f"Tax reversal: {line.description}",
                            dimension_refs=dimensions,
                        )
                    )
                if split.released_amount > 0:
                    entries.append(
                        AccountingEntry(
                            account_ref=split.release_account_ref,
                            credit=split.released_amount,
                            description=f"Tax reversal: {line.description}",
                            dimension_refs=dimensions,
                        )
                    )
    _check_consequence_entries(entries)
    return _consequence(
        source=ConsequenceSource(
            owner="payables",
            document_kind="credit_note",
            document_id=str(credit.id),
            version="1",
            fingerprint=credit.request_fingerprint,
        ),
        posting_date=credit.credit_date,
        description=f"Supplier credit note {credit.number}",
        currency_code=credit.currency_code,
        exchange_rate=credit.exchange_rate,
        entries=tuple(entries),
    )


def record_accounting_receipt(
    db: Session,
    *,
    scope: TenantScope,
    command: AccountingReceiptInput,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    recorded_at: datetime,
) -> AccountingReceipt:
    tenant_id = scope.tenant_id
    kind = command.document_kind.strip()
    if kind == "supplier_invoice":
        consequence = build_invoice_accounting_consequence(
            db, tenant_id=tenant_id, invoice_id=command.document_id
        )
    elif kind == "credit_note":
        consequence = build_credit_accounting_consequence(
            db, tenant_id=tenant_id, credit_note_id=command.document_id
        )
    elif kind == "supplier_payment":
        consequence = build_payment_accounting_consequence(
            db, tenant_id=tenant_id, payment_id=command.document_id
        )
    elif kind == "supplier_payment_reversal":
        consequence = build_payment_reversal_accounting_consequence(
            db, tenant_id=tenant_id, payment_id=command.document_id
        )
    elif kind == "supplier_credit_application":
        consequence = build_credit_application_accounting_consequence(
            db, tenant_id=tenant_id, application_id=command.document_id
        )
    else:
        raise InvalidTransition("unknown accounting receipt document kind")
    claimed = _fingerprint(command.consequence_fingerprint, "consequence fingerprint")
    if claimed != consequence.fingerprint:
        raise Conflict("accounting receipt does not match the current consequence")
    reference = _required(command.accounting_reference, "accounting reference", 255)
    evidence = _fingerprint(
        command.accounting_evidence_fingerprint, "accounting evidence fingerprint"
    )
    payload = {
        "document_kind": kind,
        "document_id": str(command.document_id),
        "consequence_fingerprint": claimed,
        "accounting_reference": reference,
        "accounting_evidence_fingerprint": evidence,
    }

    def operation(session: Session) -> Mapping[str, object]:
        existing = session.scalar(
            select(AccountingReceipt).where(
                AccountingReceipt.tenant_id == tenant_id,
                AccountingReceipt.document_kind == kind,
                AccountingReceipt.document_id == command.document_id,
            )
        )
        if existing is not None:
            if (
                existing.consequence_fingerprint != claimed
                or existing.accounting_reference != reference
                or existing.accounting_evidence_fingerprint != evidence
            ):
                raise InvalidTransition(
                    "document already has different accounting evidence"
                )
            return {"receipt_id": str(existing.id)}
        receipt = AccountingReceipt(
            tenant_id=tenant_id,
            document_kind=kind,
            document_id=command.document_id,
            consequence_fingerprint=claimed,
            accounting_reference=reference,
            accounting_evidence_fingerprint=evidence,
            recorded_at=recorded_at,
        )
        session.add(receipt)
        session.flush()
        return {"receipt_id": str(receipt.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.record_accounting_receipt",
        key=idempotency_key,
        operation=operation,
        fingerprint=fingerprint_of(payload),
        expires_at=idempotency_expires_at,
    )
    receipt = db.scalar(
        select(AccountingReceipt).where(
            AccountingReceipt.tenant_id == tenant_id,
            AccountingReceipt.id == UUID(str(outcome.result["receipt_id"])),
        )
    )
    if receipt is None:
        raise NotFound("accounting receipt was not found")
    return receipt


def _historical_receipt(
    db: Session, tenant_id: UUID, invoice_id: UUID
) -> HistoricalInvoiceReceipt | None:
    return db.scalar(
        select(HistoricalInvoiceReceipt).where(
            HistoricalInvoiceReceipt.tenant_id == tenant_id,
            HistoricalInvoiceReceipt.invoice_id == invoice_id,
        )
    )


def _historical_credit_receipt_for_invoice(
    db: Session, tenant_id: UUID, invoice_id: UUID
) -> HistoricalCreditReceipt | None:
    return db.scalar(
        select(HistoricalCreditReceipt).where(
            HistoricalCreditReceipt.tenant_id == tenant_id,
            HistoricalCreditReceipt.invoice_id == invoice_id,
        )
    )


def _historical_credit_receipt_for_credit(
    db: Session, tenant_id: UUID, credit_note_id: UUID
) -> HistoricalCreditReceipt | None:
    return db.scalar(
        select(HistoricalCreditReceipt).where(
            HistoricalCreditReceipt.tenant_id == tenant_id,
            HistoricalCreditReceipt.credit_note_id == credit_note_id,
        )
    )


def _historical_money(value: Decimal) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value != value.quantize(Decimal("0.01"))
    ):
        raise InvalidAmount("historical money must be exact finite two-decimal money")


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        return canonical_instant(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal | UUID):
        return str(value)
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _json_value(getattr(value, f.name)) for f in fields(value)}
    return value


def _evidence_json(
    evidence: HistoricalUnpaidInvoiceEvidence
    | HistoricalSettledInvoiceEvidence
    | ApprovedInvoiceRecognitionEvidence
    | HistoricalTaxDeferralEvidence
    | HistoricalSupplierCreditEvidence,
) -> dict[str, object]:
    return {f.name: _json_value(getattr(evidence, f.name)) for f in fields(evidence)}


class _DiscardVerifierSavepoint(Exception):
    """Private sentinel: always leave the verifier savepoint by rolling it back."""


def _run_verifier(
    db: Session,
    *,
    scope: TenantScope,
    command: HistoricalUnpaidInvoiceCommand
    | HistoricalSettledInvoiceCommand
    | RecognizeApprovedInvoice
    | HistoricalTaxDeferralCommand
    | HistoricalSupplierCreditCommand,
    verifier: HistoricalUnpaidInvoiceVerifier
    | HistoricalSettledInvoiceVerifier
    | ApprovedInvoiceRecognitionVerifier
    | HistoricalTaxDeferralVerifier
    | HistoricalSupplierCreditVerifier,
) -> tuple[SupplierInvoice, object]:
    """Lock the invoice, then run the read-only verifier in a discarded savepoint."""
    # All invoice writers and accounting guards share this lock. Verify the
    # immutable destination facts under it, rather than admitting a stale proof.
    locked = _invoice(db, scope.tenant_id, command.invoice_id, lock=True)
    # The verifier only reads. It runs in a savepoint that is always discarded,
    # so flushed and Core writes are contained, and any ORM write is refused.
    wrote: list[bool] = []

    def _saw_flush(session: Session, _context: object) -> None:
        if session.new or session.dirty or session.deleted:
            wrote.append(True)

    def _refuse_commit(_session: Session) -> None:
        # Refuse before Session.commit() can also commit the caller's outer
        # transaction. Keep the violation even if the verifier catches it.
        wrote.append(True)
        raise InvalidTransition("historical verifier must not write")

    evidence = None
    try:
        with db.begin_nested() as savepoint:
            registered: list[tuple[str, Callable[..., object]]] = []
            try:
                event.listen(db, "after_flush", _saw_flush)
                registered.append(("after_flush", _saw_flush))
                event.listen(db, "before_commit", _refuse_commit)
                registered.append(("before_commit", _refuse_commit))
                with db.no_autoflush:
                    evidence = verifier.verify(
                        scope=scope,
                        command=command,  # type: ignore[arg-type]
                        destination=db,
                    )
                    if db.new or db.dirty or db.deleted:
                        wrote.append(True)
                    if not savepoint.is_active or (
                        db.get_nested_transaction() is not savepoint
                    ):
                        wrote.append(True)
            finally:
                for name, fn in reversed(registered):
                    event.remove(db, name, fn)
            raise _DiscardVerifierSavepoint
    except _DiscardVerifierSavepoint:
        pass
    if wrote:
        raise InvalidTransition("historical verifier must not write")
    return locked, evidence


def _check_historical_common(
    evidence: HistoricalUnpaidInvoiceEvidence
    | HistoricalSettledInvoiceEvidence
    | HistoricalTaxDeferralEvidence
    | HistoricalSupplierCreditEvidence,
    *,
    tenant_id: UUID,
    command: HistoricalUnpaidInvoiceCommand
    | HistoricalSettledInvoiceCommand
    | HistoricalTaxDeferralCommand
    | HistoricalSupplierCreditCommand,
) -> str:
    if (
        evidence.tenant_id,
        evidence.invoice_id,
        evidence.source_owner,
        evidence.source_invoice_id,
        evidence.source_snapshot_sha256,
    ) != (
        tenant_id,
        command.invoice_id,
        command.source_owner,
        command.source_invoice_id,
        command.source_snapshot_sha256,
    ):
        raise Conflict("historical evidence identity differs from command")
    fingerprint = evidence.fingerprint()
    if fingerprint != command.evidence_fingerprint:
        raise Conflict("historical evidence fingerprint differs from command")
    from dotmac_payables.contracts import _historical_digest

    _historical_digest(evidence.accounting_evidence_fingerprint)
    _historical_digest(evidence.facts_fingerprint)
    return fingerprint


def _check_unpaid_evidence(evidence: HistoricalUnpaidInvoiceEvidence) -> None:
    for value in (
        evidence.subtotal,
        evidence.tax_amount,
        evidence.total_amount,
        evidence.paid_amount,
        evidence.withholding_tax_amount,
        evidence.prepayment_amount,
    ):
        _historical_money(value)
    if evidence.source_status != "POSTED" or evidence.invoice_type != "STANDARD":
        raise InvalidTransition("historical admission requires POSTED STANDARD source")
    if (
        evidence.paid_amount != 0
        or type(evidence.allocation_count) is not int
        or evidence.allocation_count != 0
        or evidence.withholding_tax_amount != 0
        or evidence.prepayment_amount != 0
    ):
        raise InvalidTransition(
            "historical invoice must be wholly unpaid without allocations, "
            "WHT or prepayment"
        )


def _check_settled_evidence(
    evidence: HistoricalSettledInvoiceEvidence, *, admitted_at: datetime
) -> Decimal:
    """Prove the residual exactly from the settlements; return it."""
    for value in (
        evidence.subtotal,
        evidence.tax_amount,
        evidence.total_amount,
        evidence.settled_amount,
        evidence.residual_amount,
        evidence.withholding_tax_amount,
        evidence.prepayment_amount,
    ):
        _historical_money(value)
    if evidence.source_status != "POSTED" or evidence.invoice_type != "STANDARD":
        raise InvalidTransition("historical admission requires POSTED STANDARD source")
    if evidence.prepayment_amount != 0:
        raise InvalidTransition("historical settled invoice cannot carry a prepayment")
    settlements = evidence.settlements
    if not isinstance(settlements, tuple) or not settlements:
        raise InvalidTransition("settled historical admission requires settlements")
    seen: set[str] = set()
    applied = Decimal("0")
    withheld = Decimal("0")
    for item in settlements:
        item.validate()
        if item.source_settlement_id in seen:
            raise Conflict("historical settlement source ids must be unique")
        seen.add(item.source_settlement_id)
        if (
            item.currency_code.strip().upper() != evidence.currency_code
            or item.exchange_rate != evidence.exchange_rate
        ):
            raise InvalidAmount(
                "historical settlement currency or exchange rate differs from "
                "the invoice (fx_difference_unsupported)"
            )
        if item.settled_on < evidence.invoice_date:
            raise InvalidTransition("historical settlement precedes the invoice")
        if item.settled_on > admitted_at.date():
            raise InvalidTransition("historical settlement cannot follow admission")
        applied += item.applied_amount
        withheld += item.withholding_amount
    if (
        applied != evidence.settled_amount
        or withheld != evidence.withholding_tax_amount
    ):
        raise InvalidAmount("historical settlements do not sum to the declared totals")
    if applied > evidence.total_amount:
        raise InvalidAmount("historical settlements exceed the invoice total")
    residual = evidence.total_amount - applied
    if residual != evidence.residual_amount:
        raise InvalidAmount("historical residual is not proved by the settlements")
    if residual <= 0:
        raise InvalidTransition(
            "a fully settled historical invoice has no open obligation to admit"
        )
    return residual


def _admit_historical(
    db: Session,
    *,
    scope: TenantScope,
    command: HistoricalUnpaidInvoiceCommand | HistoricalSettledInvoiceCommand,
    verifier: HistoricalUnpaidInvoiceVerifier | HistoricalSettledInvoiceVerifier,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    admitted_at: datetime,
    settled: bool,
) -> HistoricalInvoiceReceipt:
    command.validate()
    if verifier is None or not callable(getattr(verifier, "verify", None)):
        raise InvalidTransition("historical admission requires a trusted verifier")
    canonical_instant(admitted_at)
    if not isinstance(db, Session) or isinstance(db, scoped_session):
        raise InvalidTransition("historical admission requires a plain Session")
    locked, evidence = _run_verifier(
        db, scope=scope, command=command, verifier=verifier
    )
    expected_type = (
        HistoricalSettledInvoiceEvidence if settled else HistoricalUnpaidInvoiceEvidence
    )
    if not isinstance(evidence, expected_type):
        raise InvalidTransition("historical verifier returned no typed evidence")
    fingerprint = _check_historical_common(
        evidence,
        tenant_id=scope.tenant_id,
        command=command,
    )
    residual: Decimal
    if isinstance(evidence, HistoricalUnpaidInvoiceEvidence):
        _check_unpaid_evidence(evidence)
    if (
        evidence.total_amount <= 0
        or evidence.subtotal < 0
        or evidence.tax_amount < 0
        or evidence.total_amount != evidence.subtotal + evidence.tax_amount
    ):
        raise InvalidAmount("historical invoice amounts do not reconcile")
    if not evidence.exchange_rate.is_finite() or evidence.exchange_rate <= 0:
        raise InvalidAmount("historical exchange rate must be positive and finite")
    if isinstance(evidence, HistoricalSettledInvoiceEvidence):
        residual = _check_settled_evidence(evidence, admitted_at=admitted_at)
        settlements = evidence.settlements
    else:
        residual = evidence.total_amount
        settlements = ()
    for journal_reference in (
        evidence.source_journal_ref,
        evidence.destination_journal_ref,
    ):
        _required(journal_reference, "historical journal reference", 255)
    canonical_instant(evidence.original_posted_at)
    if evidence.original_posted_at > admitted_at:
        raise InvalidTransition("historical posting cannot follow admission")

    def operation(session: Session) -> Mapping[str, object]:
        invoice = _invoice(session, scope.tenant_id, command.invoice_id, lock=True)
        existing = _historical_receipt(session, scope.tenant_id, invoice.id)
        if existing is not None:
            if existing.evidence_fingerprint != fingerprint:
                raise Conflict("invoice has different permanent historical evidence")
            return {"receipt_id": str(existing.id)}
        source_existing = session.scalar(
            select(HistoricalInvoiceReceipt).where(
                HistoricalInvoiceReceipt.tenant_id == scope.tenant_id,
                HistoricalInvoiceReceipt.source_owner == command.source_owner,
                HistoricalInvoiceReceipt.source_invoice_id == command.source_invoice_id,
            )
        )
        if source_existing is not None:
            raise Conflict("source invoice already admitted to another invoice")
        if invoice.status != InvoiceStatus.APPROVED:
            raise InvalidTransition("historical destination must already be APPROVED")
        for name in (
            "number",
            "supplier_ref",
            "supplier_name_snapshot",
            "supplier_document_number",
            "invoice_date",
            "received_date",
            "currency_code",
            "exchange_rate",
            "liability_account_ref",
            "subtotal",
            "tax_amount",
            "total_amount",
            "procurement_ref",
            "receipt_evidence_fingerprint",
            "submitted_by",
            "submitted_at",
            "approved_by",
            "approved_at",
            "approval_reference",
        ):
            actual, expected = getattr(invoice, name), getattr(evidence, name)
            if isinstance(actual, datetime) and isinstance(expected, datetime):
                equal = canonical_instant(actual) == canonical_instant(expected)
            else:
                equal = actual == expected
            if not equal:
                raise Conflict(f"historical destination differs on {name}")
        if len(invoice.payment_schedule) != 1:
            raise InvalidTransition(
                "historical invoice requires one full-face obligation"
            )
        schedule = invoice.payment_schedule[0]
        amount = Decimal(schedule["amount"])
        _historical_money(amount)
        if (
            amount != evidence.total_amount
            or date.fromisoformat(schedule["due_date"]) != evidence.due_date
        ):
            raise Conflict("historical payment schedule differs from evidence")
        for model, predicate in (
            (PaymentObligation, PaymentObligation.invoice_id == invoice.id),
            (
                LiabilityEvent,
                (LiabilityEvent.document_kind == "supplier_invoice")
                & (LiabilityEvent.document_id == invoice.id),
            ),
            (
                AccountingReceipt,
                (AccountingReceipt.document_kind == "supplier_invoice")
                & (AccountingReceipt.document_id == invoice.id),
            ),
            (InvoiceRecognition, InvoiceRecognition.invoice_id == invoice.id),
            (
                HistoricalCreditReceipt,
                HistoricalCreditReceipt.invoice_id == invoice.id,
            ),
        ):
            if (
                session.scalar(
                    select(model.id)
                    .where(model.tenant_id == scope.tenant_id, predicate)
                    .limit(1)
                )
                is not None
            ):
                raise Conflict(
                    "historical invoice already has owner recognition "
                    "or accounting evidence"
                )
        if settlements:
            _lock_settlement_ids(
                session,
                scope.tenant_id,
                [item.source_settlement_id for item in settlements],
            )
            taken = session.scalar(
                select(HistoricalSettlementRecord.id)
                .where(
                    HistoricalSettlementRecord.tenant_id == scope.tenant_id,
                    HistoricalSettlementRecord.source_settlement_id.in_(
                        [item.source_settlement_id for item in settlements]
                    ),
                )
                .limit(1)
            )
            taken_by_credit = session.scalar(
                select(HistoricalCreditSettlementRecord.id)
                .where(
                    HistoricalCreditSettlementRecord.tenant_id == scope.tenant_id,
                    HistoricalCreditSettlementRecord.source_settlement_id.in_(
                        [item.source_settlement_id for item in settlements]
                    ),
                )
                .limit(1)
            )
            if taken is not None or taken_by_credit is not None:
                raise Conflict(
                    "a legacy settlement was already admitted against an invoice"
                )
        obligation = PaymentObligation(
            tenant_id=scope.tenant_id,
            invoice_id=invoice.id,
            sequence=1,
            due_date=evidence.due_date,
            currency_code=evidence.currency_code,
            original_amount=evidence.total_amount,
            outstanding_amount=residual,
            status=(
                ObligationStatus.PARTIALLY_SETTLED
                if settlements
                else ObligationStatus.OPEN
            ),
            created_at=admitted_at,
            updated_at=admitted_at,
        )
        session.add(obligation)
        session.flush()
        event = LiabilityEvent(
            tenant_id=scope.tenant_id,
            obligation_id=obligation.id,
            event_kind="historical_invoice_recognized",
            document_kind="supplier_invoice",
            document_id=invoice.id,
            supplier_ref=evidence.supplier_ref,
            currency_code=evidence.currency_code,
            amount=evidence.total_amount,
            source_reference=command.source_invoice_id,
            source_fingerprint=fingerprint[4:],
            occurred_at=evidence.original_posted_at,
        )
        session.add(event)
        session.flush()
        receipt = HistoricalInvoiceReceipt(
            tenant_id=scope.tenant_id,
            invoice_id=invoice.id,
            obligation_id=obligation.id,
            event_id=event.id,
            source_owner=command.source_owner,
            source_invoice_id=command.source_invoice_id,
            source_snapshot_sha256=command.source_snapshot_sha256,
            evidence_fingerprint=fingerprint,
            facts_fingerprint=evidence.facts_fingerprint,
            accounting_evidence_fingerprint=evidence.accounting_evidence_fingerprint,
            source_journal_ref=evidence.source_journal_ref,
            destination_journal_ref=evidence.destination_journal_ref,
            evidence=_evidence_json(evidence),
            original_posted_at=evidence.original_posted_at,
            admitted_at=admitted_at,
        )
        session.add(receipt)
        session.flush()
        for number, item in enumerate(settlements, start=1):
            row = HistoricalSettlementRecord(
                tenant_id=scope.tenant_id,
                receipt_id=receipt.id,
                obligation_id=obligation.id,
                line_number=number,
                source_settlement_id=item.source_settlement_id,
                payment_ref=item.payment_ref,
                settled_on=item.settled_on,
                applied_amount=item.applied_amount,
                withholding_amount=item.withholding_amount,
                currency_code=item.currency_code.strip().upper(),
                exchange_rate=item.exchange_rate,
                source_row_fingerprint=item.source_row_fingerprint,
                source_journal_ref=item.source_journal_ref,
                destination_journal_ref=item.destination_journal_ref,
                recorded_at=admitted_at,
            )
            session.add(row)
            session.flush()
            session.add(
                LiabilityEvent(
                    tenant_id=scope.tenant_id,
                    obligation_id=obligation.id,
                    event_kind="historical_settlement_recorded",
                    document_kind="historical_settlement",
                    document_id=row.id,
                    supplier_ref=evidence.supplier_ref,
                    currency_code=evidence.currency_code,
                    amount=-item.applied_amount,
                    source_reference=item.source_settlement_id,
                    source_fingerprint=item.source_row_fingerprint[4:],
                    occurred_at=datetime.combine(
                        item.settled_on, datetime.min.time(), UTC
                    ),
                )
            )
        session.flush()
        return {"receipt_id": str(receipt.id)}

    # Check permanent evidence even when the short-lived kernel key still exists.
    existing = _historical_receipt(db, scope.tenant_id, locked.id)
    if existing is not None and existing.evidence_fingerprint != fingerprint:
        raise Conflict("invoice has different permanent historical evidence")
    try:
        outcome = execute_once(
            db,
            tenant_id=scope.tenant_id,
            scope=(
                "payables.admit_historical_settled_invoice"
                if settled
                else "payables.admit_historical_unpaid_invoice"
            ),
            key=idempotency_key,
            # The kernel ledger stores 64 hex characters; retain the complete
            # versioned identity as one fixed field in its established adapter.
            fingerprint=fingerprint_of(
                {"historical_evidence_fingerprint": fingerprint}
            ),
            operation=operation,
            expires_at=idempotency_expires_at,
        )
    except IntegrityError as exc:
        # execute_once has already restored its savepoint and tenant context.
        # A different invoice may race for this permanent source identity.
        raise Conflict("historical source identity or evidence already exists") from exc
    receipt = db.scalar(
        select(HistoricalInvoiceReceipt).where(
            HistoricalInvoiceReceipt.tenant_id == scope.tenant_id,
            HistoricalInvoiceReceipt.id == UUID(str(outcome.result["receipt_id"])),
        )
    )
    if receipt is None:
        raise NotFound("historical invoice receipt was not found")
    return receipt


def admit_historical_unpaid_invoice(
    db: Session,
    *,
    scope: TenantScope,
    command: HistoricalUnpaidInvoiceCommand,
    verifier: HistoricalUnpaidInvoiceVerifier,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    admitted_at: datetime,
) -> HistoricalInvoiceReceipt:
    """Recognize original liability once without approving or posting again.

    The verifier is a trusted assembly dependency. Its immutable source/header/GL
    checks precede replay; mutable owner eligibility is checked only on admission.
    """
    return _admit_historical(
        db,
        scope=scope,
        command=command,
        verifier=verifier,
        idempotency_key=idempotency_key,
        idempotency_expires_at=idempotency_expires_at,
        admitted_at=admitted_at,
        settled=False,
    )


def admit_historical_settled_invoice(
    db: Session,
    *,
    scope: TenantScope,
    command: HistoricalSettledInvoiceCommand,
    verifier: HistoricalSettledInvoiceVerifier,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    admitted_at: datetime,
) -> HistoricalInvoiceReceipt:
    """Admit a partially settled historical invoice with its legacy settlements.

    The obligation is created at full face; each verifier-proven legacy
    settlement becomes an immutable ``historical_settlements`` row (withholding
    counted as settled) and a negative liability event; the outstanding balance is
    the residual proved exactly from the settlements. No accounting consequence is
    produced: legacy AP control already holds the residual.
    """
    return _admit_historical(
        db,
        scope=scope,
        command=command,
        verifier=verifier,
        idempotency_key=idempotency_key,
        idempotency_expires_at=idempotency_expires_at,
        admitted_at=admitted_at,
        settled=True,
    )


# --- a6: cash-basis release of deferred input tax --------------------------------


def _deferral_policies(db: Session, tenant_id: UUID) -> dict[str, TaxDeferralPolicy]:
    return {
        row.deferral_account_ref: row
        for row in db.scalars(
            select(TaxDeferralPolicy)
            .where(TaxDeferralPolicy.tenant_id == tenant_id)
            .order_by(TaxDeferralPolicy.deferral_account_ref)
        )
    }


def declare_tax_deferral(
    db: Session,
    *,
    scope: TenantScope,
    command: DeclareTaxDeferral,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    declared_at: datetime,
) -> TaxDeferralPolicy:
    """Declare that tax booked to one account is deferred until payment.

    One immutable row per (tenant, deferral account). An identical replay returns
    the existing row; any other declaration for that account is a conflict.
    """
    command.validate()
    tenant_id = scope.tenant_id
    canonical_instant(declared_at)
    deferral = command.deferral_account_ref.strip()
    release = command.release_account_ref.strip()
    declared_by = command.declared_by.strip()
    evidence_reference = command.evidence_reference.strip()
    payload = {
        "deferral_account_ref": deferral,
        "release_account_ref": release,
        "minor_units": command.minor_units,
        "declared_by": declared_by,
        "evidence_reference": evidence_reference,
    }

    def operation(session: Session) -> Mapping[str, object]:
        policies = _deferral_policies(session, tenant_id)
        existing = policies.get(deferral)
        if existing is not None:
            if (
                existing.release_account_ref != release
                or existing.minor_units != command.minor_units
                or existing.declared_by != declared_by
                or existing.evidence_reference != evidence_reference
            ):
                raise _refuse(
                    Conflict,
                    "a different tax deferral is already declared for this account",
                    "tax_deferral_already_declared",
                )
            return {"policy_id": str(existing.id)}
        if any(row.release_account_ref == deferral for row in policies.values()) or any(
            row.deferral_account_ref == release for row in policies.values()
        ):
            raise _refuse(
                Conflict,
                "a deferral account cannot be another policy's release account",
                "tax_deferral_chain",
            )
        policy = TaxDeferralPolicy(
            tenant_id=tenant_id,
            deferral_account_ref=deferral,
            release_account_ref=release,
            minor_units=command.minor_units,
            declared_by=declared_by,
            evidence_reference=evidence_reference,
            declared_at=declared_at,
        )
        session.add(policy)
        session.flush()
        return {"policy_id": str(policy.id)}

    try:
        outcome = execute_once(
            db,
            tenant_id=tenant_id,
            scope="payables.declare_tax_deferral",
            key=idempotency_key,
            operation=operation,
            fingerprint=fingerprint_of(payload),
            expires_at=idempotency_expires_at,
        )
    except IntegrityError as exc:
        raise _refuse(
            Conflict,
            "a tax deferral is already declared for this account",
            "tax_deferral_already_declared",
        ) from exc
    policy = db.scalar(
        select(TaxDeferralPolicy).where(
            TaxDeferralPolicy.tenant_id == tenant_id,
            TaxDeferralPolicy.id == UUID(str(outcome.result["policy_id"])),
        )
    )
    if policy is None:
        raise NotFound("tax deferral policy was not found")
    return policy


def _sum_money(values: object) -> Decimal:
    return _money(sum((Decimal(v) for v in values), Decimal("0")))  # type: ignore[attr-defined]


def invoice_deferred_tax_position(
    db: Session, tenant_id: UUID, invoice_id: UUID
) -> tuple[DeferredTaxPosition, ...]:
    """The deferred-tax position of an invoice, per declared deferral account.

    All figures come from frozen rows. D is the tax on the invoice's lines booked to
    the deferral account; L the deferral released before admission; Cdef and Crel
    the deferral and release shares of APPROVED credit notes against the invoice
    (``credit_note_tax_splits``; an approved credit with no split rows counts its
    whole deferral-line tax in Cdef); Rel_pay the releases of RECORDED payments (a
    REVERSED payment's rows are excluded because its reversal mirrors them);
    Rel_app the releases of credit applications (permanent).

    R = D - L - Cdef - Rel_pay - Rel_app is what is still deferred, and
    Claimed = L + Rel_pay + Rel_app - Crel is the input VAT currently claimed, so
    D = R + Claimed + Ctax with Ctax = Cdef + Crel. The owner refuses to ever hold
    R < 0, Claimed < 0 or Ctax > D (a data fault, never repaired). Only accounts
    with D > 0 appear. Call under the invoice row lock to write.
    """
    policies = _deferral_policies(db, tenant_id)
    if not policies:
        return ()
    positions: list[DeferredTaxPosition] = []
    lines = _invoice_lines(db, tenant_id, invoice_id)
    credit_lines = list(
        db.execute(
            select(
                CreditNoteLine.credit_note_id,
                CreditNoteLine.line_number,
                CreditNoteLine.tax_amount,
                CreditNoteLine.tax_account_ref,
            )
            .join(
                CreditNote,
                (CreditNote.tenant_id == CreditNoteLine.tenant_id)
                & (CreditNote.id == CreditNoteLine.credit_note_id),
            )
            .where(
                CreditNote.tenant_id == tenant_id,
                CreditNote.original_invoice_id == invoice_id,
                CreditNote.status == CreditNoteStatus.APPROVED,
            )
        )
    )
    splits = {
        (row.credit_note_id, row.line_number): row
        for row in db.scalars(
            select(CreditNoteTaxSplit).where(
                CreditNoteTaxSplit.tenant_id == tenant_id,
                CreditNoteTaxSplit.invoice_id == invoice_id,
            )
        )
    }
    for account in sorted(policies):
        deferred = _sum_money(
            line.tax_amount for line in lines if line.tax_account_ref == account
        )
        if deferred <= 0:
            continue
        historical = db.scalar(
            select(HistoricalTaxDeferralRecord.released_before_admission).where(
                HistoricalTaxDeferralRecord.tenant_id == tenant_id,
                HistoricalTaxDeferralRecord.invoice_id == invoice_id,
                HistoricalTaxDeferralRecord.deferral_account_ref == account,
            )
        )
        credit_deferred = Decimal("0")
        credit_released = Decimal("0")
        for credit_id, line_number, tax, tax_account in credit_lines:
            if tax_account != account:
                continue
            split = splits.get((credit_id, line_number))
            if split is None:
                credit_deferred += Decimal(tax)
            else:
                credit_deferred += Decimal(split.deferred_amount)
                credit_released += Decimal(split.released_amount)
        by_payments = _sum_money(
            db.scalars(
                select(SupplierPaymentTaxRelease.amount)
                .join(
                    SupplierPayment,
                    (SupplierPayment.tenant_id == SupplierPaymentTaxRelease.tenant_id)
                    & (SupplierPayment.id == SupplierPaymentTaxRelease.payment_id),
                )
                .where(
                    SupplierPaymentTaxRelease.tenant_id == tenant_id,
                    SupplierPaymentTaxRelease.invoice_id == invoice_id,
                    SupplierPaymentTaxRelease.deferral_account_ref == account,
                    SupplierPayment.status == SupplierPaymentStatus.RECORDED,
                )
            )
        )
        by_applications = _sum_money(
            db.scalars(
                select(CreditApplicationTaxRelease.amount).where(
                    CreditApplicationTaxRelease.tenant_id == tenant_id,
                    CreditApplicationTaxRelease.invoice_id == invoice_id,
                    CreditApplicationTaxRelease.deferral_account_ref == account,
                )
            )
        )
        before = _money(Decimal(historical or 0))
        cdef = _money(credit_deferred)
        crel = _money(credit_released)
        remaining = _money(deferred - before - cdef - by_payments - by_applications)
        claimed = _money(before + by_payments + by_applications - crel)
        if remaining < 0 or claimed < 0 or cdef + crel > deferred:
            raise _refuse(
                InvalidAmount,
                "deferred tax position violates its invariants",
                "deferred_tax_position_negative",
            )
        positions.append(
            DeferredTaxPosition(
                deferral_account_ref=account,
                deferred=deferred,
                released_before_admission=before,
                credited=cdef,
                credit_released=crel,
                released_by_payments=by_payments,
                released_by_applications=by_applications,
                remaining=remaining,
                claimed=claimed,
            )
        )
    return tuple(positions)


def _require_historical_deferral_proven(
    db: Session, tenant_id: UUID, invoice_id: UUID, deferral_account_ref: str
) -> None:
    """A settled historical invoice needs its admitted deferral position."""
    receipt = _historical_receipt(db, tenant_id, invoice_id)
    if (
        receipt is not None
        and db.scalar(
            select(HistoricalSettlementRecord.id)
            .where(
                HistoricalSettlementRecord.tenant_id == tenant_id,
                HistoricalSettlementRecord.receipt_id == receipt.id,
            )
            .limit(1)
        )
        is not None
        and db.scalar(
            select(HistoricalTaxDeferralRecord.id).where(
                HistoricalTaxDeferralRecord.tenant_id == tenant_id,
                HistoricalTaxDeferralRecord.invoice_id == invoice_id,
                HistoricalTaxDeferralRecord.deferral_account_ref
                == deferral_account_ref,
            )
        )
        is None
    ):
        raise _refuse(
            InvalidTransition,
            "a settled historical invoice with deferred tax has no "
            "admitted deferral position",
            "historical_deferral_unproven",
        )


def _release_deferred_tax_for_payment(
    db: Session, payment: SupplierPayment, *, recorded_at: datetime
) -> None:
    """Freeze the deferred-tax release of a payment as rows, before settlement.

    Lock order: payment, obligations (already held by the recheck), then the
    invoices in invoice-id order. Nothing here reads policy that could change
    after the rows are written; the consequence is rebuilt from the rows.
    """
    tenant_id = payment.tenant_id
    policies = _deferral_policies(db, tenant_id)
    if not policies:
        return
    allocated: dict[UUID, Decimal] = {}
    for allocation in _payment_allocations(db, tenant_id, payment.id):
        obligation = _obligation(db, tenant_id, allocation.obligation_id)
        allocated[obligation.invoice_id] = _money(
            allocated.get(obligation.invoice_id, Decimal("0")) + allocation.amount
        )
    line_number = 0
    for invoice_id in sorted(allocated, key=str):
        _invoice(db, tenant_id, invoice_id, lock=True)
        outstanding = _money(
            Decimal(
                db.scalar(
                    select(func.sum(PaymentObligation.outstanding_amount)).where(
                        PaymentObligation.tenant_id == tenant_id,
                        PaymentObligation.invoice_id == invoice_id,
                    )
                )
                or 0
            )
        )
        for position in invoice_deferred_tax_position(db, tenant_id, invoice_id):
            _require_historical_deferral_proven(
                db, tenant_id, invoice_id, position.deferral_account_ref
            )
            policy = policies[position.deferral_account_ref]
            amount = deferred_tax_release(
                position.remaining,
                allocated[invoice_id],
                outstanding,
                policy.minor_units,
            )
            if amount <= 0:
                continue
            line_number += 1
            db.add(
                SupplierPaymentTaxRelease(
                    tenant_id=tenant_id,
                    payment_id=payment.id,
                    line_number=line_number,
                    invoice_id=invoice_id,
                    policy_id=policy.id,
                    deferral_account_ref=policy.deferral_account_ref,
                    release_account_ref=policy.release_account_ref,
                    amount=_money(amount),
                    created_at=recorded_at,
                )
            )
    db.flush()


def _payment_tax_releases(
    db: Session, tenant_id: UUID, payment_id: UUID
) -> list[SupplierPaymentTaxRelease]:
    return list(
        db.scalars(
            select(SupplierPaymentTaxRelease)
            .where(
                SupplierPaymentTaxRelease.tenant_id == tenant_id,
                SupplierPaymentTaxRelease.payment_id == payment_id,
            )
            .order_by(SupplierPaymentTaxRelease.line_number)
        )
    )


def _check_withholding_accounts(
    db: Session,
    *,
    tenant_id: UUID,
    cash_account_ref: str,
    withholding_refs: list[str],
    liability_refs: set[str],
) -> None:
    """A withholding account may not be cash, a liability or a tax-deferral account."""
    forbidden = {cash_account_ref, *liability_refs}
    for policy in _deferral_policies(db, tenant_id).values():
        forbidden.update((policy.deferral_account_ref, policy.release_account_ref))
    if any(ref in forbidden for ref in withholding_refs):
        raise _refuse(
            InvalidTransition,
            "a withholding account cannot be the cash account, an allocated "
            "invoice's liability account or a tax-deferral account",
            "withholding_account_conflict",
        )


PAYMENT_SETTLEMENT_OWNER = "payables.supplier_payment"
_ACTIVE_PAYMENT_STATES = (SupplierPaymentStatus.DRAFT, SupplierPaymentStatus.APPROVED)


def _same_actor(left: str | None, right: str | None) -> bool:
    return (
        left is not None
        and right is not None
        and left.strip().casefold() == right.strip().casefold()
    )


def _sod_enforced(payment: SupplierPayment, separation_of_duties: bool) -> bool:
    """The stricter of the row's stamped policy and the caller's value wins.

    Separation of duties is an optional per-tenant policy (D-9). ``prepare``
    stamps the policy in force on the row; a later step can tighten it (the
    caller asks for separation) but never loosen it.
    """
    return bool(payment.separation_enforced) or separation_of_duties


def _payment(
    db: Session, tenant_id: UUID, payment_id: UUID, *, lock: bool = False
) -> SupplierPayment:
    stmt = select(SupplierPayment).where(
        SupplierPayment.tenant_id == tenant_id, SupplierPayment.id == payment_id
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("supplier payment was not found")
    return row


def _payment_allocations(
    db: Session, tenant_id: UUID, payment_id: UUID
) -> list[SupplierPaymentAllocation]:
    return list(
        db.scalars(
            select(SupplierPaymentAllocation)
            .where(
                SupplierPaymentAllocation.tenant_id == tenant_id,
                SupplierPaymentAllocation.payment_id == payment_id,
            )
            .order_by(SupplierPaymentAllocation.line_number)
        )
    )


def _payment_withholdings(
    db: Session, tenant_id: UUID, payment_id: UUID
) -> list[SupplierPaymentWithholding]:
    return list(
        db.scalars(
            select(SupplierPaymentWithholding)
            .where(
                SupplierPaymentWithholding.tenant_id == tenant_id,
                SupplierPaymentWithholding.payment_id == payment_id,
            )
            .order_by(SupplierPaymentWithholding.line_number)
        )
    )


def _reserved_by_active_payments(
    db: Session, tenant_id: UUID, obligation_id: UUID, *, excluding: UUID | None
) -> Decimal:
    stmt = (
        select(func.sum(SupplierPaymentAllocation.amount))
        .join(
            SupplierPayment,
            (SupplierPayment.tenant_id == SupplierPaymentAllocation.tenant_id)
            & (SupplierPayment.id == SupplierPaymentAllocation.payment_id),
        )
        .where(
            SupplierPaymentAllocation.tenant_id == tenant_id,
            SupplierPaymentAllocation.obligation_id == obligation_id,
            SupplierPayment.status.in_(_ACTIVE_PAYMENT_STATES),
        )
    )
    if excluding is not None:
        stmt = stmt.where(SupplierPayment.id != excluding)
    return _money(Decimal(db.scalar(stmt) or 0))


def _liability_in_ledger(db: Session, tenant_id: UUID, invoice_id: UUID) -> bool:
    receipt = db.scalar(
        select(AccountingReceipt.id).where(
            AccountingReceipt.tenant_id == tenant_id,
            AccountingReceipt.document_kind == "supplier_invoice",
            AccountingReceipt.document_id == invoice_id,
        )
    )
    if receipt is not None:
        return True
    return _historical_receipt(db, tenant_id, invoice_id) is not None


def _check_allocation_payable(
    db: Session,
    *,
    tenant_id: UUID,
    obligation_id: UUID,
    amount: Decimal,
    supplier_ref: str,
    currency_code: str,
    exchange_rate: Decimal,
    payment_id: UUID | None,
) -> None:
    """Refuse an allocation that is not payable now. Locks the obligation row."""
    obligation = _obligation(db, tenant_id, obligation_id, lock=True)
    invoice = _invoice(db, tenant_id, obligation.invoice_id)
    if invoice.status != InvoiceStatus.APPROVED:
        raise InvalidTransition("only an approved supplier invoice can be paid")
    if invoice.supplier_ref != supplier_ref:
        raise InvalidTransition("obligation belongs to a different supplier")
    if obligation.currency_code != currency_code:
        raise InvalidAmount("payment currency does not match the obligation")
    if invoice.exchange_rate != exchange_rate:
        # fx_difference_unsupported: realised FX is not modelled, so a payment
        # at a rate other than the invoice's would drift the control account.
        raise InvalidAmount(
            "payment exchange rate differs from the invoice rate "
            "(fx_difference_unsupported)"
        )
    if not _liability_in_ledger(db, tenant_id, invoice.id):
        # liability_not_in_ledger: paying it would debit a control account that
        # was never credited.
        raise InvalidTransition(
            "supplier invoice has no accounting or historical receipt "
            "(liability_not_in_ledger)"
        )
    if obligation.status not in (
        ObligationStatus.OPEN,
        ObligationStatus.PARTIALLY_SETTLED,
    ):
        raise InvalidTransition("only an open payment obligation can be paid")
    reserved = _reserved_by_active_payments(
        db, tenant_id, obligation_id, excluding=payment_id
    )
    if reserved + amount > obligation.outstanding_amount:
        raise InvalidAmount(
            "allocation exceeds the obligation balance not already committed "
            "to other supplier payments"
        )


def create_supplier_payment(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateSupplierPayment,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    prepared_at: datetime,
    separation_of_duties: bool = False,
) -> SupplierPayment:
    """Prepare a DRAFT supplier payment against open obligations (no money moves).

    ``separation_of_duties`` is the tenant policy in force. It is stamped on the
    row (``separation_enforced``) and can never be changed afterwards; the later
    approve/record/reverse steps enforce it per row. The default is ``False``
    (separation OFF): two-person approval is never compulsory (Michael's
    ruling, D-9). A caller that wants two-person approval passes ``True``.
    """
    if not isinstance(separation_of_duties, bool):
        raise TypeError("separation_of_duties must be bool")
    tenant_id = scope.tenant_id
    payload = command.fingerprint_payload()
    request_fingerprint = fingerprint_of(payload)
    # The flag joins the fingerprint only when relaxed, so enforced
    # callers keep their a6 fingerprint bytes and replays.
    command_fingerprint = fingerprint_of(
        payload if separation_of_duties else {**payload, "separation_of_duties": False}
    )
    preparer = str(payload["prepared_by"])
    currency = str(payload["currency_code"])
    supplier_ref = str(payload["supplier_ref"])
    allocations = tuple(
        (item.obligation_id, _money(item.amount)) for item in command.allocations
    )
    withholdings = tuple(
        (
            str(item.payload()["account_ref"]),
            _money(item.amount),
            item.description.strip() if item.description else None,
            item.certificate_ref.strip() if item.certificate_ref else None,
        )
        for item in command.withholdings
    )
    gross = _money(sum((amount for _, amount in allocations), Decimal("0")))
    withheld = _money(sum((row[1] for row in withholdings), Decimal("0")))
    if any(amount <= 0 for _, amount in allocations) or any(
        row[1] <= 0 for row in withholdings
    ):
        raise InvalidAmount("rounded payment amounts must remain positive")
    if withheld >= gross:
        raise InvalidAmount("withholding must be less than the gross payment")

    def operation(session: Session) -> Mapping[str, object]:
        existing = session.scalar(
            select(SupplierPayment).where(
                SupplierPayment.tenant_id == tenant_id,
                SupplierPayment.number == str(payload["number"]),
            )
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise Conflict("supplier payment number names a different payment")
            if existing.separation_enforced != bool(separation_of_duties):
                raise Conflict(
                    "supplier payment number was prepared under a different "
                    "separation-of-duties policy"
                )
            return {"payment_id": str(existing.id)}
        for obligation_id, amount in sorted(allocations, key=lambda row: str(row[0])):
            _check_allocation_payable(
                session,
                tenant_id=tenant_id,
                obligation_id=obligation_id,
                amount=amount,
                supplier_ref=supplier_ref,
                currency_code=currency,
                exchange_rate=command.exchange_rate,
                payment_id=None,
            )
        if withholdings:
            _check_withholding_accounts(
                session,
                tenant_id=tenant_id,
                cash_account_ref=str(payload["cash_account_ref"]),
                withholding_refs=[row[0] for row in withholdings],
                liability_refs={
                    _invoice(
                        session,
                        tenant_id,
                        _obligation(session, tenant_id, obligation_id).invoice_id,
                    ).liability_account_ref
                    for obligation_id, _ in allocations
                },
            )
        payment = SupplierPayment(
            tenant_id=tenant_id,
            number=str(payload["number"]),
            supplier_ref=supplier_ref,
            supplier_name_snapshot=str(payload["supplier_name_snapshot"]),
            currency_code=currency,
            exchange_rate=command.exchange_rate,
            value_date=command.value_date,
            cash_account_ref=str(payload["cash_account_ref"]),
            payer_instrument_ref=str(payload["payer_instrument_ref"]),
            gross_amount=gross,
            wht_amount=withheld,
            net_amount=_money(gross - withheld),
            request_fingerprint=request_fingerprint,
            separation_enforced=bool(separation_of_duties),
            status=SupplierPaymentStatus.DRAFT,
            prepared_by=preparer,
            prepared_at=prepared_at,
            created_at=prepared_at,
            updated_at=prepared_at,
        )
        session.add(payment)
        session.flush()
        for number, (obligation_id, amount) in enumerate(allocations, start=1):
            session.add(
                SupplierPaymentAllocation(
                    tenant_id=tenant_id,
                    payment_id=payment.id,
                    line_number=number,
                    obligation_id=obligation_id,
                    amount=amount,
                    created_at=prepared_at,
                )
            )
        for number, (account_ref, amount, description, certificate) in enumerate(
            withholdings, start=1
        ):
            session.add(
                SupplierPaymentWithholding(
                    tenant_id=tenant_id,
                    payment_id=payment.id,
                    line_number=number,
                    account_ref=account_ref,
                    amount=amount,
                    description=description,
                    certificate_ref=certificate,
                    created_at=prepared_at,
                )
            )
        session.flush()
        return {"payment_id": str(payment.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.create_supplier_payment",
        key=idempotency_key,
        operation=operation,
        fingerprint=command_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _payment(db, tenant_id, UUID(str(outcome.result["payment_id"])))


def _recheck_allocations(db: Session, payment: SupplierPayment) -> None:
    for allocation in sorted(
        _payment_allocations(db, payment.tenant_id, payment.id),
        key=lambda row: str(row.obligation_id),
    ):
        _check_allocation_payable(
            db,
            tenant_id=payment.tenant_id,
            obligation_id=allocation.obligation_id,
            amount=allocation.amount,
            supplier_ref=payment.supplier_ref,
            currency_code=payment.currency_code,
            exchange_rate=payment.exchange_rate,
            payment_id=payment.id,
        )
    withholding_refs = [
        row.account_ref
        for row in _payment_withholdings(db, payment.tenant_id, payment.id)
    ]
    if withholding_refs:
        _check_withholding_accounts(
            db,
            tenant_id=payment.tenant_id,
            cash_account_ref=payment.cash_account_ref,
            withholding_refs=withholding_refs,
            liability_refs={
                _invoice(
                    db,
                    payment.tenant_id,
                    _obligation(
                        db, payment.tenant_id, allocation.obligation_id
                    ).invoice_id,
                ).liability_account_ref
                for allocation in _payment_allocations(
                    db, payment.tenant_id, payment.id
                )
            },
        )


def approve_supplier_payment(
    db: Session,
    *,
    scope: TenantScope,
    payment_id: UUID,
    approval_reference: str,
    approved_by: str,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    approved_at: datetime,
    separation_of_duties: bool = False,
) -> SupplierPayment:
    """DRAFT -> APPROVED. The approver must differ from the preparer.

    The separation applies when the row was stamped ``separation_enforced`` at
    preparation or ``separation_of_duties`` is true (the stricter value wins).
    The default is ``False`` (D-9: never compulsory); pass ``True`` to require it.
    """
    if not isinstance(separation_of_duties, bool):
        raise TypeError("separation_of_duties must be bool")
    tenant_id = scope.tenant_id
    approval = _required(approval_reference, "approval reference", 255)
    actor = _required(approved_by, "approved by", 255)
    payload: dict[str, object] = {
        "payment_id": str(payment_id),
        "approval_reference": approval,
        "approved_by": actor,
        "approved_at": approved_at.isoformat(),
    }
    if not separation_of_duties:
        payload["separation_of_duties"] = False
    event_fingerprint = fingerprint_of(payload)

    def operation(session: Session) -> Mapping[str, object]:
        payment = _payment(session, tenant_id, payment_id, lock=True)
        if payment.status != SupplierPaymentStatus.DRAFT:
            raise InvalidTransition("only a draft supplier payment can be approved")
        if _sod_enforced(payment, separation_of_duties) and _same_actor(
            actor, payment.prepared_by
        ):
            raise InvalidTransition(
                "a supplier payment cannot be approved by its preparer"
            )
        _recheck_allocations(session, payment)
        payment.status = SupplierPaymentStatus.APPROVED
        payment.approval_reference = approval
        payment.approved_by = actor
        payment.approved_at = approved_at
        payment.updated_at = approved_at
        session.flush()
        return {"payment_id": str(payment.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.approve_supplier_payment",
        key=idempotency_key,
        operation=operation,
        fingerprint=event_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _payment(db, tenant_id, UUID(str(outcome.result["payment_id"])))


def record_supplier_payment(
    db: Session,
    *,
    scope: TenantScope,
    payment_id: UUID,
    bank_reference: str,
    recorded_by: str,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    recorded_at: datetime,
    separation_of_duties: bool = False,
) -> SupplierPayment:
    """APPROVED -> RECORDED, settling every allocation in the caller's transaction.

    Recording is an assertion by a human, backed by ``bank_reference``, that the
    money left through an external channel. The recorder must differ from both the
    preparer and the approver when separation applies (stamped on the row at
    preparation or ``separation_of_duties`` true; the stricter value wins).
    The default is ``False`` (D-9: never compulsory); pass ``True`` to require it.
    Nothing here calls a bank or provider.
    """
    if not isinstance(separation_of_duties, bool):
        raise TypeError("separation_of_duties must be bool")
    tenant_id = scope.tenant_id
    reference = _required(bank_reference, "bank reference", 255)
    actor = _required(recorded_by, "recorded by", 255)
    payload: dict[str, object] = {
        "payment_id": str(payment_id),
        "bank_reference": reference,
        "recorded_by": actor,
        "recorded_at": recorded_at.isoformat(),
    }
    if not separation_of_duties:
        payload["separation_of_duties"] = False
    event_fingerprint = fingerprint_of(payload)

    def operation(session: Session) -> Mapping[str, object]:
        payment = _payment(session, tenant_id, payment_id, lock=True)
        if payment.status != SupplierPaymentStatus.APPROVED:
            raise InvalidTransition("only an approved supplier payment can be recorded")
        if _sod_enforced(payment, separation_of_duties) and (
            _same_actor(actor, payment.prepared_by)
            or _same_actor(actor, payment.approved_by)
        ):
            raise InvalidTransition(
                "a supplier payment must be recorded by someone other than "
                "its preparer and approver"
            )
        duplicate = session.scalar(
            select(SupplierPayment.id).where(
                SupplierPayment.tenant_id == tenant_id,
                SupplierPayment.payer_instrument_ref == payment.payer_instrument_ref,
                SupplierPayment.bank_reference == reference,
            )
        )
        if duplicate is not None:
            raise Conflict("bank reference was already recorded against a payment")
        _recheck_allocations(session, payment)
        _release_deferred_tax_for_payment(session, payment, recorded_at=recorded_at)
        for allocation in sorted(
            _payment_allocations(session, tenant_id, payment.id),
            key=lambda row: str(row.obligation_id),
        ):
            fingerprint = fingerprint_of(
                {
                    "payment_id": str(payment.id),
                    "line_number": allocation.line_number,
                    "obligation_id": str(allocation.obligation_id),
                    "amount": str(allocation.amount),
                    "bank_reference": reference,
                }
            )
            _record_settlement(
                session,
                scope=scope,
                command=SettlementObservationInput(
                    obligation_id=allocation.obligation_id,
                    source_owner=PAYMENT_SETTLEMENT_OWNER,
                    source_reference=f"{payment.id}:{allocation.line_number}",
                    source_version="1",
                    source_fingerprint=fingerprint,
                    amount=allocation.amount,
                    occurred_at=datetime.combine(
                        payment.value_date, datetime.min.time(), UTC
                    ),
                    currency_code=payment.currency_code,
                ),
                idempotency_key=f"supplier_payment:{payment.id}:settle:{allocation.line_number}",
                idempotency_expires_at=None,
                recorded_at=recorded_at,
                from_payment=True,
            )
        payment.status = SupplierPaymentStatus.RECORDED
        payment.bank_reference = reference
        payment.recorded_by = actor
        payment.recorded_at = recorded_at
        payment.updated_at = recorded_at
        session.flush()
        return {"payment_id": str(payment.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.record_supplier_payment",
        key=idempotency_key,
        operation=operation,
        fingerprint=event_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _payment(db, tenant_id, UUID(str(outcome.result["payment_id"])))


def cancel_supplier_payment(
    db: Session,
    *,
    scope: TenantScope,
    payment_id: UUID,
    reason: str,
    cancelled_by: str,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    cancelled_at: datetime,
) -> SupplierPayment:
    """DRAFT or APPROVED -> CANCELLED, releasing its obligation reservation.

    A RECORDED payment is never cancelled; correcting one is a forward reversal
    owned by a later release.
    """
    tenant_id = scope.tenant_id
    why = _required(reason, "cancel reason", 2000)
    actor = _required(cancelled_by, "cancelled by", 255)
    payload = {
        "payment_id": str(payment_id),
        "reason": why,
        "cancelled_by": actor,
        "cancelled_at": cancelled_at.isoformat(),
    }
    event_fingerprint = fingerprint_of(payload)

    def operation(session: Session) -> Mapping[str, object]:
        payment = _payment(session, tenant_id, payment_id, lock=True)
        if payment.status not in _ACTIVE_PAYMENT_STATES:
            raise InvalidTransition(
                "only a draft or approved supplier payment can be cancelled"
            )
        payment.status = SupplierPaymentStatus.CANCELLED
        payment.cancelled_by = actor
        payment.cancelled_at = cancelled_at
        payment.cancel_reason = why
        payment.updated_at = cancelled_at
        session.flush()
        return {"payment_id": str(payment.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.cancel_supplier_payment",
        key=idempotency_key,
        operation=operation,
        fingerprint=event_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _payment(db, tenant_id, UUID(str(outcome.result["payment_id"])))


def _check_payment_balance(
    entries: list[AccountingEntry],
    by_account: dict[str, Decimal],
    release_entries: list[AccountingEntry],
    rows: list[SupplierPaymentTaxRelease],
    payment: SupplierPayment,
    *,
    reversal: bool = False,
) -> None:
    """Debits equal credits, liability lines equal gross, release lines equal rows."""
    debits = sum((entry.debit for entry in entries), Decimal("0"))
    credits = sum((entry.credit for entry in entries), Decimal("0"))
    rows_total = _money(sum((row.amount for row in rows), Decimal("0")))
    if (
        debits != credits
        or _money(sum(by_account.values(), Decimal("0"))) != payment.gross_amount
        or sum((e.debit for e in release_entries), Decimal("0")) != rows_total
        or sum((e.credit for e in release_entries), Decimal("0")) != rows_total
    ):
        raise InvalidAmount(
            "persisted supplier payment reversal does not balance"
            if reversal
            else "persisted supplier payment does not balance"
        )


def build_payment_accounting_consequence(
    db: Session, *, tenant_id: UUID, payment_id: UUID
) -> AccountingConsequence:
    """Provider-neutral GL consequence of a RECORDED payment, for the ERP adapter.

    Dr supplier liability (gross, per invoice liability account), Cr cash account
    (net), Cr each withholding-tax payable account. Payables posts nothing itself.
    """
    payment = _payment(db, tenant_id, payment_id)
    if payment.status not in (
        SupplierPaymentStatus.RECORDED,
        SupplierPaymentStatus.REVERSED,
    ):
        raise InvalidTransition(
            "only a recorded supplier payment has an accounting consequence"
        )
    by_account: dict[str, Decimal] = {}
    for allocation in _payment_allocations(db, tenant_id, payment.id):
        obligation = _obligation(db, tenant_id, allocation.obligation_id)
        invoice = _invoice(db, tenant_id, obligation.invoice_id)
        account = invoice.liability_account_ref
        by_account[account] = _money(
            by_account.get(account, Decimal("0")) + allocation.amount
        )
    entries: list[AccountingEntry] = [
        AccountingEntry(
            account_ref=account,
            debit=amount,
            description=f"Supplier payment {payment.number}",
        )
        for account, amount in by_account.items()
    ]
    entries.append(
        AccountingEntry(
            account_ref=payment.cash_account_ref,
            credit=payment.net_amount,
            description=(
                f"Supplier payment {payment.number} ref {payment.bank_reference}"
            ),
        )
    )
    for withholding in _payment_withholdings(db, tenant_id, payment.id):
        entries.append(
            AccountingEntry(
                account_ref=withholding.account_ref,
                credit=withholding.amount,
                description=withholding.description
                or f"Withholding tax: payment {payment.number}",
            )
        )
    releases = _payment_tax_releases(db, tenant_id, payment.id)
    release_entries: list[AccountingEntry] = []
    for release in releases:
        description = f"Deferred input tax released: payment {payment.number}"
        release_entries.append(
            AccountingEntry(
                account_ref=release.release_account_ref,
                debit=release.amount,
                description=description,
            )
        )
        release_entries.append(
            AccountingEntry(
                account_ref=release.deferral_account_ref,
                credit=release.amount,
                description=description,
            )
        )
    entries.extend(release_entries)
    _check_payment_balance(entries, by_account, release_entries, releases, payment)
    return _consequence(
        source=ConsequenceSource(
            owner="payables",
            document_kind="supplier_payment",
            document_id=str(payment.id),
            version="1",
            fingerprint=payment.request_fingerprint,
        ),
        posting_date=payment.value_date,
        description=f"Supplier payment {payment.number}",
        currency_code=payment.currency_code,
        exchange_rate=payment.exchange_rate,
        entries=tuple(entries),
    )


def _payment_has_ledger_receipt(db: Session, tenant_id: UUID, payment_id: UUID) -> bool:
    return (
        db.scalar(
            select(AccountingReceipt.id).where(
                AccountingReceipt.tenant_id == tenant_id,
                AccountingReceipt.document_kind == "supplier_payment",
                AccountingReceipt.document_id == payment_id,
            )
        )
        is not None
    )


def _invoice_has_credit_vat_decision(
    db: Session, tenant_id: UUID, invoice_id: UUID
) -> bool:
    """A credit split put VAT on the release account, or a DOCUMENT credit
    application released the remainder: both assumed the payment's cash stays paid."""
    if (
        db.scalar(
            select(CreditNoteTaxSplit.id)
            .where(
                CreditNoteTaxSplit.tenant_id == tenant_id,
                CreditNoteTaxSplit.invoice_id == invoice_id,
                CreditNoteTaxSplit.released_amount > 0,
            )
            .limit(1)
        )
        is not None
    ):
        return True
    return (
        db.scalar(
            select(CreditApplicationTaxRelease.id)
            .join(
                CreditApplication,
                (CreditApplication.tenant_id == CreditApplicationTaxRelease.tenant_id)
                & (CreditApplication.id == CreditApplicationTaxRelease.application_id),
            )
            .where(
                CreditApplicationTaxRelease.tenant_id == tenant_id,
                CreditApplicationTaxRelease.invoice_id == invoice_id,
                CreditApplication.credit_note_id.not_in(
                    select(HistoricalCreditReceipt.credit_note_id).where(
                        HistoricalCreditReceipt.tenant_id == tenant_id
                    )
                ),
            )
            .limit(1)
        )
        is not None
    )


def reverse_supplier_payment(
    db: Session,
    *,
    scope: TenantScope,
    payment_id: UUID,
    reason: str,
    reversal_date: date,
    reversed_by: str,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    reversed_at: datetime,
    separation_of_duties: bool = False,
) -> SupplierPayment:
    """RECORDED -> REVERSED (terminal): reopen what the payment settled.

    For a payment that did not clear (a bounced transfer, a wrong supplier). The
    reverser must differ from the recorder and the approver when separation
    applies (stamped on the row or ``separation_of_duties`` true; the default
    is ``False``, D-9: never compulsory). Every allocation's
    amount is returned to its obligation's outstanding balance and a positive
    ``settlement_reversed`` liability event is appended; settlement evidence is
    never edited. The GL reversal is exposed by
    ``build_payment_reversal_accounting_consequence`` for the assembly to post.
    Only a payment whose original consequence was acknowledged by the ledger
    (an accounting receipt) can be reversed.
    """
    if not isinstance(separation_of_duties, bool):
        raise TypeError("separation_of_duties must be bool")
    tenant_id = scope.tenant_id
    why = _required(reason, "reversal reason", 2000)
    actor = _required(reversed_by, "reversed by", 255)
    payload: dict[str, object] = {
        "payment_id": str(payment_id),
        "reason": why,
        "reversal_date": reversal_date.isoformat(),
        "reversed_by": actor,
        "reversed_at": reversed_at.isoformat(),
    }
    if not separation_of_duties:
        payload["separation_of_duties"] = False
    event_fingerprint = fingerprint_of(payload)

    def operation(session: Session) -> Mapping[str, object]:
        payment = _payment(session, tenant_id, payment_id, lock=True)
        if payment.status != SupplierPaymentStatus.RECORDED:
            raise InvalidTransition("only a recorded supplier payment can be reversed")
        if _sod_enforced(payment, separation_of_duties) and (
            _same_actor(actor, payment.recorded_by)
            or _same_actor(actor, payment.approved_by)
        ):
            raise InvalidTransition(
                "a supplier payment must be reversed by someone other than "
                "its recorder and approver"
            )
        if reversal_date < payment.value_date:
            raise InvalidTransition(
                "a payment cannot be reversed before its value date"
            )
        if not _payment_has_ledger_receipt(session, tenant_id, payment.id):
            raise InvalidTransition(
                "supplier payment has no accounting receipt (payment_not_in_ledger)"
            )
        ordered = sorted(
            _payment_allocations(session, tenant_id, payment.id),
            key=lambda row: str(row.obligation_id),
        )
        # Lock order: payment, obligations, then invoices in invoice-id order.
        locked_obligations = {
            allocation.obligation_id: _obligation(
                session, tenant_id, allocation.obligation_id, lock=True
            )
            for allocation in ordered
        }
        locked_invoices = {
            invoice_id: _invoice(session, tenant_id, invoice_id, lock=True)
            for invoice_id in sorted(
                {row.invoice_id for row in locked_obligations.values()}, key=str
            )
        }
        for invoice_id in locked_invoices:
            if _invoice_has_credit_vat_decision(session, tenant_id, invoice_id):
                raise _refuse(
                    InvalidTransition,
                    "a credit has already settled this invoice's VAT; this payment "
                    "reversal goes to Finance exceptions",
                    "payment_reversal_blocked_by_credit_vat",
                )
        for allocation in ordered:
            obligation = locked_obligations[allocation.obligation_id]
            invoice = locked_invoices[obligation.invoice_id]
            reopened = _money(obligation.outstanding_amount + allocation.amount)
            historical = session.scalar(
                select(func.sum(HistoricalSettlementRecord.applied_amount)).where(
                    HistoricalSettlementRecord.tenant_id == tenant_id,
                    HistoricalSettlementRecord.obligation_id == obligation.id,
                )
            )
            credited = session.scalar(
                select(func.sum(CreditApplication.amount)).where(
                    CreditApplication.tenant_id == tenant_id,
                    CreditApplication.obligation_id == obligation.id,
                )
            )
            ceiling = _money(
                obligation.original_amount
                - Decimal(historical or 0)
                - Decimal(credited or 0)
            )
            if reopened > ceiling:
                raise InvalidAmount(
                    "reversal would exceed the obligation face less historical "
                    "settlements and credit applications"
                )
            obligation.outstanding_amount = reopened
            obligation.status = (
                ObligationStatus.OPEN
                if reopened == obligation.original_amount
                else ObligationStatus.PARTIALLY_SETTLED
            )
            obligation.updated_at = reversed_at
            session.add(
                LiabilityEvent(
                    tenant_id=tenant_id,
                    obligation_id=obligation.id,
                    event_kind="settlement_reversed",
                    document_kind="supplier_payment_reversal",
                    document_id=payment.id,
                    supplier_ref=invoice.supplier_ref,
                    currency_code=invoice.currency_code,
                    amount=allocation.amount,
                    source_reference=f"{payment.id}:{allocation.line_number}",
                    source_fingerprint=event_fingerprint,
                    occurred_at=reversed_at,
                )
            )
        payment.status = SupplierPaymentStatus.REVERSED
        payment.reversed_by = actor
        payment.reversed_at = reversed_at
        payment.reversal_reason = why
        payment.reversal_date = reversal_date
        payment.updated_at = reversed_at
        session.flush()
        return {"payment_id": str(payment.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="payables.reverse_supplier_payment",
        key=idempotency_key,
        operation=operation,
        fingerprint=event_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _payment(db, tenant_id, UUID(str(outcome.result["payment_id"])))


def build_credit_application_accounting_consequence(
    db: Session, *, tenant_id: UUID, application_id: UUID
) -> AccountingConsequence:
    """GL consequence of a credit application: the deferred tax it released.

    Dr release account / Cr deferral account per row. There is no 2000 line: the
    application nets inside the supplier control account, as in a5. An application
    that released nothing has no consequence.
    """
    application = db.scalar(
        select(CreditApplication).where(
            CreditApplication.tenant_id == tenant_id,
            CreditApplication.id == application_id,
        )
    )
    if application is None:
        raise NotFound("credit application was not found")
    rows = credit_application_tax_releases(db, tenant_id, application_id)
    if not rows:
        raise _refuse(
            InvalidTransition,
            "the credit application released no deferred tax",
            "credit_application_has_no_consequence",
        )
    obligation = _obligation(db, tenant_id, application.obligation_id)
    invoice = _invoice(db, tenant_id, obligation.invoice_id)
    applied_at = application.applied_at
    if applied_at.tzinfo is None:
        applied_at = applied_at.replace(tzinfo=UTC)
    entries: list[AccountingEntry] = []
    for row in rows:
        description = (
            f"Deferred input tax released: credit application {application.id}"
        )
        entries.append(
            AccountingEntry(
                account_ref=row.release_account_ref,
                debit=row.amount,
                description=description,
            )
        )
        entries.append(
            AccountingEntry(
                account_ref=row.deferral_account_ref,
                credit=row.amount,
                description=description,
            )
        )
    total = _money(sum((row.amount for row in rows), Decimal("0")))
    if (
        sum((e.debit for e in entries), Decimal("0")) != total
        or sum((e.credit for e in entries), Decimal("0")) != total
    ):
        raise InvalidAmount("persisted credit application does not balance")
    return _consequence(
        source=ConsequenceSource(
            owner="payables",
            document_kind="supplier_credit_application",
            document_id=str(application.id),
            version="1",
            fingerprint=fingerprint_of(
                {
                    "credit_note_id": str(application.credit_note_id),
                    "obligation_id": str(application.obligation_id),
                    "amount": str(application.amount),
                    "applied_at": canonical_instant(applied_at),
                }
            ),
        ),
        posting_date=applied_at.astimezone(UTC).date(),
        description=f"Credit application {application.id}",
        currency_code=invoice.currency_code,
        exchange_rate=invoice.exchange_rate,
        entries=tuple(entries),
    )


def build_payment_reversal_accounting_consequence(
    db: Session, *, tenant_id: UUID, payment_id: UUID
) -> AccountingConsequence:
    """GL reversal of a REVERSED payment: Dr cash (net) and WHT, Cr liability."""
    payment = _payment(db, tenant_id, payment_id)
    if (
        payment.status != SupplierPaymentStatus.REVERSED
        or payment.reversal_date is None
    ):
        raise InvalidTransition(
            "only a reversed supplier payment has a reversal consequence"
        )
    by_account: dict[str, Decimal] = {}
    for allocation in _payment_allocations(db, tenant_id, payment.id):
        obligation = _obligation(db, tenant_id, allocation.obligation_id)
        invoice = _invoice(db, tenant_id, obligation.invoice_id)
        account = invoice.liability_account_ref
        by_account[account] = _money(
            by_account.get(account, Decimal("0")) + allocation.amount
        )
    entries: list[AccountingEntry] = [
        AccountingEntry(
            account_ref=payment.cash_account_ref,
            debit=payment.net_amount,
            description=f"Reversal of supplier payment {payment.number}",
        )
    ]
    for withholding in _payment_withholdings(db, tenant_id, payment.id):
        entries.append(
            AccountingEntry(
                account_ref=withholding.account_ref,
                debit=withholding.amount,
                description=f"Reversal of withholding tax: payment {payment.number}",
            )
        )
    for account, amount in by_account.items():
        entries.append(
            AccountingEntry(
                account_ref=account,
                credit=amount,
                description=f"Reversal of supplier payment {payment.number}",
            )
        )
    releases = _payment_tax_releases(db, tenant_id, payment.id)
    release_entries: list[AccountingEntry] = []
    for release in releases:
        description = (
            f"Reversal of deferred input tax release: payment {payment.number}"
        )
        release_entries.append(
            AccountingEntry(
                account_ref=release.deferral_account_ref,
                debit=release.amount,
                description=description,
            )
        )
        release_entries.append(
            AccountingEntry(
                account_ref=release.release_account_ref,
                credit=release.amount,
                description=description,
            )
        )
    entries.extend(release_entries)
    _check_payment_balance(
        entries, by_account, release_entries, releases, payment, reversal=True
    )
    return _consequence(
        source=ConsequenceSource(
            owner="payables",
            document_kind="supplier_payment_reversal",
            document_id=str(payment.id),
            version="1",
            fingerprint=fingerprint_of(
                {
                    "request": payment.request_fingerprint,
                    "reversal_date": payment.reversal_date.isoformat(),
                    "reversal_reason": payment.reversal_reason,
                }
            ),
        ),
        posting_date=payment.reversal_date,
        description=f"Reversal of supplier payment {payment.number}",
        currency_code=payment.currency_code,
        exchange_rate=payment.exchange_rate,
        entries=tuple(entries),
    )


def recognize_approved_invoice(
    db: Session,
    *,
    scope: TenantScope,
    command: RecognizeApprovedInvoice,
    verifier: ApprovedInvoiceRecognitionVerifier,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    recognized_at: datetime,
) -> SupplierInvoice:
    """Write the missing owner recognition of an imported APPROVED invoice.

    Creates the ``invoice_recognized`` event and the obligations from the
    invoice's own schedule exactly as ``approve_invoice`` would, without any
    status regression or edit to the imported invoice. A trusted verifier must
    prove from snapshot evidence that the legacy invoice was APPROVED and never
    POSTED (no journal entry, posting date or GL lines), otherwise recognizing it
    and then posting would double count expense, VAT and the control account.
    Refuses unless the invoice is APPROVED with no recognition event, obligation,
    accounting receipt, historical receipt or earlier recognition. The permanent
    ``invoice_recognitions`` row keeps the evidence reference, actor, approval
    reference and the verifier's evidence; the assembly binds the reference to its
    reviewed list and enforces who may act.
    """
    command.validate()
    if verifier is None or not callable(getattr(verifier, "verify", None)):
        raise InvalidTransition("recognition requires a trusted verifier")
    canonical_instant(recognized_at)
    if not isinstance(db, Session) or isinstance(db, scoped_session):
        raise InvalidTransition("recognition requires a plain Session")
    tenant_id = scope.tenant_id
    _, evidence = _run_verifier(db, scope=scope, command=command, verifier=verifier)
    if not isinstance(evidence, ApprovedInvoiceRecognitionEvidence):
        raise InvalidTransition("recognition verifier returned no typed evidence")
    if (
        evidence.tenant_id,
        evidence.invoice_id,
        evidence.source_owner,
        evidence.source_invoice_id,
        evidence.source_snapshot_sha256,
    ) != (
        tenant_id,
        command.invoice_id,
        command.source_owner,
        command.source_invoice_id,
        command.source_snapshot_sha256,
    ):
        raise Conflict("recognition evidence identity differs from command")
    evidence_fingerprint = evidence.fingerprint()
    if evidence_fingerprint != command.evidence_fingerprint:
        raise Conflict("recognition evidence fingerprint differs from command")
    from dotmac_payables.contracts import _historical_digest

    _historical_digest(evidence.facts_fingerprint)
    if (
        evidence.source_status != "APPROVED"
        or evidence.source_journal_entry_id is not None
        or evidence.source_posted_at is not None
        or type(evidence.source_gl_line_count) is not int
        or evidence.source_gl_line_count != 0
    ):
        raise InvalidTransition(
            "legacy invoice is not provably APPROVED and never posted "
            "(status, journal entry, posting date and GL lines must all be absent)"
        )
    evidence_ref = command.evidence_reference.strip()
    actor = command.recognized_by.strip()
    approval = command.approval_reference.strip()
    payload = {
        "invoice_id": str(command.invoice_id),
        "evidence_reference": evidence_ref,
        "approval_reference": approval,
        "approved_by": actor,
        "approved_at": recognized_at.isoformat(),
        "evidence_fingerprint": evidence_fingerprint,
    }
    request_fingerprint = fingerprint_of(payload)
    # Same payload shape as approve_invoice for the liability event fingerprint.
    event_fingerprint = fingerprint_of(
        {
            "invoice_id": str(command.invoice_id),
            "approval_reference": approval,
            "approved_by": actor,
            "approved_at": recognized_at.isoformat(),
        }
    )

    def operation(session: Session) -> Mapping[str, object]:
        invoice = _invoice(session, tenant_id, command.invoice_id, lock=True)
        prior = session.scalar(
            select(InvoiceRecognition).where(
                InvoiceRecognition.tenant_id == tenant_id,
                InvoiceRecognition.invoice_id == invoice.id,
            )
        )
        if prior is not None:
            if (
                prior.evidence_reference != evidence_ref
                or prior.recognized_by != actor
                or prior.approval_reference != approval
                or prior.evidence_fingerprint != evidence_fingerprint
            ):
                raise Conflict("invoice was already recognized with other evidence")
            return {"invoice_id": str(invoice.id)}
        for name, expected in (
            ("number", evidence.number),
            ("supplier_ref", evidence.supplier_ref),
            ("currency_code", evidence.currency_code),
            ("total_amount", evidence.total_amount),
        ):
            if getattr(invoice, name) != expected:
                raise Conflict(f"recognition destination differs on {name}")
        if invoice.status != InvoiceStatus.APPROVED:
            raise InvalidTransition("only an approved invoice can be recognized")
        for model, predicate in (
            (
                LiabilityEvent,
                (LiabilityEvent.document_kind == "supplier_invoice")
                & (LiabilityEvent.document_id == invoice.id),
            ),
            (PaymentObligation, PaymentObligation.invoice_id == invoice.id),
            (
                AccountingReceipt,
                (AccountingReceipt.document_kind == "supplier_invoice")
                & (AccountingReceipt.document_id == invoice.id),
            ),
            (
                HistoricalInvoiceReceipt,
                HistoricalInvoiceReceipt.invoice_id == invoice.id,
            ),
            (
                HistoricalCreditReceipt,
                HistoricalCreditReceipt.invoice_id == invoice.id,
            ),
        ):
            if (
                session.scalar(
                    select(model.id)
                    .where(model.tenant_id == tenant_id, predicate)
                    .limit(1)
                )
                is not None
            ):
                raise InvalidTransition(
                    "invoice already has recognition, obligation or "
                    "accounting evidence"
                )
        schedule = [
            (
                date.fromisoformat(str(item["due_date"])),
                _money(Decimal(str(item["amount"]))),
            )
            for item in invoice.payment_schedule
        ]
        if not schedule or sum((a for _, a in schedule), Decimal("0")) != _money(
            invoice.total_amount
        ):
            raise InvalidAmount("invoice schedule does not equal the invoice total")
        session.add(
            InvoiceRecognition(
                tenant_id=tenant_id,
                invoice_id=invoice.id,
                evidence_reference=evidence_ref,
                recognized_by=actor,
                approval_reference=approval,
                source_owner=command.source_owner,
                source_invoice_id=command.source_invoice_id,
                source_snapshot_sha256=command.source_snapshot_sha256,
                evidence_fingerprint=evidence_fingerprint,
                facts_fingerprint=evidence.facts_fingerprint,
                evidence=_evidence_json(evidence),
                recognized_at=recognized_at,
            )
        )
        session.flush()
        for sequence, (due, amount) in enumerate(schedule, start=1):
            session.add(
                PaymentObligation(
                    tenant_id=tenant_id,
                    invoice_id=invoice.id,
                    sequence=sequence,
                    due_date=due,
                    currency_code=invoice.currency_code,
                    original_amount=amount,
                    outstanding_amount=amount,
                    status=ObligationStatus.OPEN,
                    created_at=recognized_at,
                    updated_at=recognized_at,
                )
            )
        session.add(
            LiabilityEvent(
                tenant_id=tenant_id,
                event_kind="invoice_recognized",
                document_kind="supplier_invoice",
                document_id=invoice.id,
                supplier_ref=invoice.supplier_ref,
                currency_code=invoice.currency_code,
                amount=invoice.total_amount,
                source_reference=approval,
                source_fingerprint=event_fingerprint,
                occurred_at=recognized_at,
            )
        )
        session.flush()
        return {"invoice_id": str(invoice.id)}

    try:
        execute_once(
            db,
            tenant_id=tenant_id,
            scope="payables.recognize_approved_invoice",
            key=idempotency_key,
            operation=operation,
            fingerprint=request_fingerprint,
            expires_at=idempotency_expires_at,
        )
    except IntegrityError as exc:
        raise Conflict("recognition source identity already exists") from exc
    return _invoice(db, tenant_id, command.invoice_id)


def admit_historical_tax_deferral(
    db: Session,
    *,
    scope: TenantScope,
    command: HistoricalTaxDeferralCommand,
    verifier: HistoricalTaxDeferralVerifier,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    admitted_at: datetime,
) -> HistoricalTaxDeferralRecord:
    """Record, with no GL, the deferral already released on a settled history invoice.

    Without it, the first v2 payment of a settled historical invoice would release
    deferred tax that legacy had already released. The verifier proves the amount
    released before admission; the owner proves it adds up against the invoice's
    own lines. One permanent row per (invoice, deferral account).
    """
    command.validate()
    if verifier is None or not callable(getattr(verifier, "verify", None)):
        raise InvalidTransition("historical deferral admission requires a verifier")
    canonical_instant(admitted_at)
    if not isinstance(db, Session) or isinstance(db, scoped_session):
        raise InvalidTransition(
            "historical deferral admission requires a plain Session"
        )
    locked, evidence = _run_verifier(
        db, scope=scope, command=command, verifier=verifier
    )
    if not isinstance(evidence, HistoricalTaxDeferralEvidence):
        raise InvalidTransition("historical verifier returned no typed evidence")
    fingerprint = _check_historical_common(
        evidence, tenant_id=scope.tenant_id, command=command
    )
    if evidence.deferral_account_ref != command.deferral_account_ref:
        raise Conflict("historical evidence deferral account differs from command")
    for value in (
        evidence.deferred_tax_total,
        evidence.released_before_admission,
        evidence.remaining,
    ):
        _historical_money(value)
    if not isinstance(evidence.releases, tuple):
        raise InvalidTransition("historical deferral releases must be a tuple")
    released = Decimal("0")
    for item in evidence.releases:
        item.validate()
        released += item.released_amount
    if evidence.deferred_tax_total <= 0:
        raise InvalidAmount("historical deferred tax total must be positive")
    if (
        released != evidence.released_before_admission
        or evidence.remaining
        != evidence.deferred_tax_total - evidence.released_before_admission
        or evidence.remaining < 0
    ):
        raise InvalidAmount("historical deferral releases do not reconcile")

    def operation(session: Session) -> Mapping[str, object]:
        invoice = _invoice(session, scope.tenant_id, command.invoice_id, lock=True)
        existing = session.scalar(
            select(HistoricalTaxDeferralRecord).where(
                HistoricalTaxDeferralRecord.tenant_id == scope.tenant_id,
                HistoricalTaxDeferralRecord.invoice_id == invoice.id,
                HistoricalTaxDeferralRecord.deferral_account_ref
                == command.deferral_account_ref,
            )
        )
        if existing is not None:
            if existing.evidence_fingerprint != fingerprint:
                raise Conflict("invoice has different permanent deferral evidence")
            return {"record_id": str(existing.id)}
        receipt = _historical_receipt(session, scope.tenant_id, invoice.id)
        settled = (
            receipt is not None
            and session.scalar(
                select(HistoricalSettlementRecord.id)
                .where(
                    HistoricalSettlementRecord.tenant_id == scope.tenant_id,
                    HistoricalSettlementRecord.receipt_id == receipt.id,
                )
                .limit(1)
            )
            is not None
        )
        if receipt is None or not settled:
            raise _refuse(
                InvalidTransition,
                "a historical deferral requires a settled historical invoice",
                "historical_deferral_invoice_not_settled",
            )
        if (receipt.source_owner, receipt.source_invoice_id) != (
            command.source_owner,
            command.source_invoice_id,
        ):
            raise Conflict("historical deferral source differs from the receipt")
        deferred = _sum_money(
            line.tax_amount
            for line in _invoice_lines(session, scope.tenant_id, invoice.id)
            if line.tax_account_ref == command.deferral_account_ref
        )
        if deferred != evidence.deferred_tax_total:
            raise Conflict("historical deferred tax differs from the invoice lines")
        row = HistoricalTaxDeferralRecord(
            tenant_id=scope.tenant_id,
            invoice_id=invoice.id,
            deferral_account_ref=command.deferral_account_ref,
            source_owner=command.source_owner,
            source_invoice_id=command.source_invoice_id,
            source_snapshot_sha256=command.source_snapshot_sha256,
            evidence_fingerprint=fingerprint,
            facts_fingerprint=evidence.facts_fingerprint,
            accounting_evidence_fingerprint=evidence.accounting_evidence_fingerprint,
            deferred_tax_total=evidence.deferred_tax_total,
            released_before_admission=evidence.released_before_admission,
            remaining=evidence.remaining,
            evidence=_evidence_json(evidence),
            admitted_at=admitted_at,
        )
        session.add(row)
        session.flush()
        return {"record_id": str(row.id)}

    existing_row = db.scalar(
        select(HistoricalTaxDeferralRecord).where(
            HistoricalTaxDeferralRecord.tenant_id == scope.tenant_id,
            HistoricalTaxDeferralRecord.invoice_id == locked.id,
            HistoricalTaxDeferralRecord.deferral_account_ref
            == command.deferral_account_ref,
        )
    )
    if existing_row is not None and existing_row.evidence_fingerprint != fingerprint:
        raise Conflict("invoice has different permanent deferral evidence")
    try:
        outcome = execute_once(
            db,
            tenant_id=scope.tenant_id,
            scope="payables.admit_historical_tax_deferral",
            key=idempotency_key,
            fingerprint=fingerprint_of(
                {"historical_evidence_fingerprint": fingerprint}
            ),
            operation=operation,
            expires_at=idempotency_expires_at,
        )
    except IntegrityError as exc:
        raise Conflict("historical deferral identity already exists") from exc
    row = db.scalar(
        select(HistoricalTaxDeferralRecord).where(
            HistoricalTaxDeferralRecord.tenant_id == scope.tenant_id,
            HistoricalTaxDeferralRecord.id == UUID(str(outcome.result["record_id"])),
        )
    )
    if row is None:
        raise NotFound("historical tax deferral was not found")
    return row


def _check_credit_evidence(
    evidence: HistoricalSupplierCreditEvidence, *, admitted_at: datetime
) -> None:
    """Owner proof that the settlements overpaid the invoice, exactly."""
    for value in (
        evidence.total_amount,
        evidence.settled_amount,
        evidence.withholding_tax_amount,
        evidence.credit_amount,
        evidence.carried_control_amount,
    ):
        _historical_money(value)
    if evidence.source_status != "POSTED" or evidence.invoice_type != "STANDARD":
        raise InvalidTransition("historical admission requires POSTED STANDARD source")
    if not evidence.exchange_rate.is_finite() or evidence.exchange_rate <= 0:
        raise InvalidAmount("historical exchange rate must be positive and finite")
    if evidence.total_amount <= 0:
        raise InvalidAmount("historical invoice total must be positive")
    settlements = evidence.settlements
    if not isinstance(settlements, tuple) or not settlements:
        raise InvalidTransition("historical credit admission requires settlements")
    seen: set[str] = set()
    applied = Decimal("0")
    withheld = Decimal("0")
    for item in settlements:
        item.validate()
        if item.source_settlement_id in seen:
            raise Conflict("historical settlement source ids must be unique")
        seen.add(item.source_settlement_id)
        if (
            item.currency_code.strip().upper() != evidence.currency_code
            or item.exchange_rate != evidence.exchange_rate
        ):
            raise InvalidAmount(
                "historical settlement currency or exchange rate differs from "
                "the invoice (fx_difference_unsupported)"
            )
        if item.settled_on > admitted_at.date():
            raise InvalidTransition("historical settlement cannot follow admission")
        applied += item.applied_amount
        withheld += item.withholding_amount
    if (
        applied != evidence.settled_amount
        or withheld != evidence.withholding_tax_amount
    ):
        raise InvalidAmount("historical settlements do not sum to the declared totals")
    if (
        evidence.settled_amount <= evidence.total_amount
        or evidence.credit_amount != evidence.settled_amount - evidence.total_amount
    ):
        raise _refuse(
            InvalidAmount,
            "the settlements do not overpay the invoice",
            "historical_credit_not_overpaid",
        )
    if evidence.carried_control_amount != -evidence.credit_amount:
        raise _refuse(
            InvalidAmount,
            "the carried supplier control net does not hold the credit",
            "legacy_gl_does_not_hold_credit",
        )
    for reference in (
        evidence.carried_evidence_ref,
        evidence.source_journal_ref,
        evidence.destination_journal_ref,
    ):
        _required(reference, "historical evidence reference", 255)
    canonical_instant(evidence.original_posted_at)
    if evidence.original_posted_at > admitted_at:
        raise InvalidTransition("historical posting cannot follow admission")


def admit_historical_supplier_credit(
    db: Session,
    *,
    scope: TenantScope,
    command: HistoricalSupplierCreditCommand,
    verifier: HistoricalSupplierCreditVerifier,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    admitted_at: datetime,
) -> CreditNote:
    """Admit a historical overpayment as an APPROVED supplier credit (no GL).

    The carried ledger already holds the debit balance and the verifier proves it
    exactly, so the credit has no accounting consequence. It lowers the supplier
    liability balance by the credit amount and can be applied with the existing
    ``apply_credit_note``. The destination invoice stays a shell: it has no
    obligation, liability event, receipt or recognition, and can never be paid.
    """
    command.validate()
    if verifier is None or not callable(getattr(verifier, "verify", None)):
        raise InvalidTransition("historical credit admission requires a verifier")
    canonical_instant(admitted_at)
    if not isinstance(db, Session) or isinstance(db, scoped_session):
        raise InvalidTransition("historical credit admission requires a plain Session")
    locked, evidence = _run_verifier(
        db, scope=scope, command=command, verifier=verifier
    )
    if not isinstance(evidence, HistoricalSupplierCreditEvidence):
        raise InvalidTransition("historical verifier returned no typed evidence")
    fingerprint = _check_historical_common(
        evidence, tenant_id=scope.tenant_id, command=command
    )
    _check_credit_evidence(evidence, admitted_at=admitted_at)
    credit_number = _required(command.credit_number, "credit number", 50).upper()
    evidence_reference = _required(
        command.evidence_reference, "evidence reference", 255
    )
    admitted_by = _required(command.admitted_by, "admitted by", 255)
    credit_amount = evidence.credit_amount
    last_settled = max(item.settled_on for item in evidence.settlements)
    # The credit's number, Finance's evidence reference and the admitting actor are
    # part of the permanent identity: a replay that differs is a conflict, never a
    # silent replay of the first admission.
    request_identity = fingerprint_of(
        {
            "historical_evidence_fingerprint": fingerprint,
            "credit_number": credit_number,
            "evidence_reference": evidence_reference,
            "admitted_by": admitted_by,
        }
    )

    def replay_matches(receipt: HistoricalCreditReceipt) -> bool:
        credit_row = _credit(db, scope.tenant_id, receipt.credit_note_id)
        return (
            receipt.evidence_fingerprint == fingerprint
            and receipt.evidence_reference == evidence_reference
            and receipt.admitted_by == admitted_by
            and credit_row.number == credit_number
        )

    def operation(session: Session) -> Mapping[str, object]:
        invoice = _invoice(session, scope.tenant_id, command.invoice_id, lock=True)
        existing = _historical_credit_receipt_for_invoice(
            session, scope.tenant_id, invoice.id
        )
        if existing is not None:
            if not replay_matches(existing):
                raise Conflict("invoice has different permanent historical evidence")
            return {"credit_note_id": str(existing.credit_note_id)}
        source_existing = session.scalar(
            select(HistoricalCreditReceipt.id).where(
                HistoricalCreditReceipt.tenant_id == scope.tenant_id,
                HistoricalCreditReceipt.source_owner == command.source_owner,
                HistoricalCreditReceipt.source_invoice_id == command.source_invoice_id,
            )
        )
        if source_existing is not None:
            raise Conflict("source invoice already admitted to another invoice")
        if invoice.status != InvoiceStatus.APPROVED:
            raise InvalidTransition("historical destination must already be APPROVED")
        for name, expected in (
            ("number", evidence.number),
            ("supplier_ref", evidence.supplier_ref),
            ("currency_code", evidence.currency_code),
            ("exchange_rate", evidence.exchange_rate),
            ("total_amount", evidence.total_amount),
            ("liability_account_ref", evidence.liability_account_ref),
        ):
            if getattr(invoice, name) != expected:
                raise Conflict(f"historical destination differs on {name}")
        if any(item.settled_on < invoice.invoice_date for item in evidence.settlements):
            raise InvalidTransition("historical settlement precedes the invoice")
        for model, predicate in (
            (PaymentObligation, PaymentObligation.invoice_id == invoice.id),
            (
                LiabilityEvent,
                (LiabilityEvent.document_kind == "supplier_invoice")
                & (LiabilityEvent.document_id == invoice.id),
            ),
            (
                AccountingReceipt,
                (AccountingReceipt.document_kind == "supplier_invoice")
                & (AccountingReceipt.document_id == invoice.id),
            ),
            (
                HistoricalInvoiceReceipt,
                HistoricalInvoiceReceipt.invoice_id == invoice.id,
            ),
            (InvoiceRecognition, InvoiceRecognition.invoice_id == invoice.id),
        ):
            if (
                session.scalar(
                    select(model.id)
                    .where(model.tenant_id == scope.tenant_id, predicate)
                    .limit(1)
                )
                is not None
            ):
                raise Conflict(
                    "historical invoice already has owner recognition "
                    "or accounting evidence"
                )
        settlement_ids = [item.source_settlement_id for item in evidence.settlements]
        _lock_settlement_ids(session, scope.tenant_id, settlement_ids)
        for settlement_model in (
            HistoricalSettlementRecord,
            HistoricalCreditSettlementRecord,
        ):
            if (
                session.scalar(
                    select(settlement_model.id)
                    .where(
                        settlement_model.tenant_id == scope.tenant_id,
                        settlement_model.source_settlement_id.in_(settlement_ids),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise Conflict(
                    "a legacy settlement was already admitted against an invoice"
                )
        if (
            session.scalar(
                select(CreditNote.id).where(
                    CreditNote.tenant_id == scope.tenant_id,
                    CreditNote.number == credit_number,
                )
            )
            is not None
        ):
            raise Conflict("credit number already exists")
        request_fingerprint = request_identity
        # Walk the document's own transitions (DRAFT -> SUBMITTED -> APPROVED) so
        # the database's document guard sees only legal, field-limited updates.
        credit = CreditNote(
            tenant_id=scope.tenant_id,
            original_invoice_id=invoice.id,
            number=credit_number,
            supplier_ref=evidence.supplier_ref,
            supplier_name_snapshot=evidence.supplier_name_snapshot,
            supplier_document_number=None,
            credit_date=last_settled,
            currency_code=evidence.currency_code,
            exchange_rate=evidence.exchange_rate,
            liability_account_ref=evidence.liability_account_ref,
            subtotal=credit_amount,
            tax_amount=Decimal("0"),
            total_amount=credit_amount,
            available_amount=Decimal("0"),
            request_fingerprint=request_fingerprint,
            status=CreditNoteStatus.DRAFT,
            created_at=admitted_at,
            updated_at=admitted_at,
        )
        session.add(credit)
        session.flush()
        credit.status = CreditNoteStatus.SUBMITTED
        credit.submitted_by = admitted_by
        credit.submitted_at = admitted_at
        credit.updated_at = admitted_at
        session.flush()
        credit.status = CreditNoteStatus.APPROVED
        credit.available_amount = credit_amount
        credit.approval_reference = evidence_reference
        credit.approved_by = admitted_by
        credit.approved_at = admitted_at
        credit.updated_at = admitted_at
        session.flush()
        event = LiabilityEvent(
            tenant_id=scope.tenant_id,
            event_kind="historical_credit_recognized",
            document_kind="credit_note",
            document_id=credit.id,
            supplier_ref=evidence.supplier_ref,
            currency_code=evidence.currency_code,
            amount=-credit_amount,
            source_reference=command.source_invoice_id,
            source_fingerprint=fingerprint[4:],
            occurred_at=datetime.combine(last_settled, datetime.min.time(), UTC),
        )
        session.add(event)
        session.flush()
        receipt = HistoricalCreditReceipt(
            tenant_id=scope.tenant_id,
            credit_note_id=credit.id,
            invoice_id=invoice.id,
            event_id=event.id,
            source_owner=command.source_owner,
            source_invoice_id=command.source_invoice_id,
            source_snapshot_sha256=command.source_snapshot_sha256,
            evidence_fingerprint=fingerprint,
            facts_fingerprint=evidence.facts_fingerprint,
            accounting_evidence_fingerprint=evidence.accounting_evidence_fingerprint,
            source_journal_ref=evidence.source_journal_ref,
            destination_journal_ref=evidence.destination_journal_ref,
            carried_evidence_ref=evidence.carried_evidence_ref,
            evidence_reference=evidence_reference,
            admitted_by=admitted_by,
            credit_amount=credit_amount,
            carried_control_amount=evidence.carried_control_amount,
            evidence=_evidence_json(evidence),
            original_posted_at=evidence.original_posted_at,
            admitted_at=admitted_at,
        )
        session.add(receipt)
        session.flush()
        for number, item in enumerate(evidence.settlements, start=1):
            session.add(
                HistoricalCreditSettlementRecord(
                    tenant_id=scope.tenant_id,
                    receipt_id=receipt.id,
                    line_number=number,
                    source_settlement_id=item.source_settlement_id,
                    payment_ref=item.payment_ref,
                    settled_on=item.settled_on,
                    applied_amount=item.applied_amount,
                    withholding_amount=item.withholding_amount,
                    currency_code=item.currency_code.strip().upper(),
                    exchange_rate=item.exchange_rate,
                    source_row_fingerprint=item.source_row_fingerprint,
                    source_journal_ref=item.source_journal_ref,
                    destination_journal_ref=item.destination_journal_ref,
                    recorded_at=admitted_at,
                )
            )
        session.flush()
        return {"credit_note_id": str(credit.id)}

    existing_receipt = _historical_credit_receipt_for_invoice(
        db, scope.tenant_id, locked.id
    )
    if existing_receipt is not None and not replay_matches(existing_receipt):
        raise Conflict("invoice has different permanent historical evidence")
    try:
        outcome = execute_once(
            db,
            tenant_id=scope.tenant_id,
            scope="payables.admit_historical_supplier_credit",
            key=idempotency_key,
            fingerprint=request_identity,
            operation=operation,
            expires_at=idempotency_expires_at,
        )
    except IntegrityError as exc:
        raise Conflict("historical source identity or evidence already exists") from exc
    return _credit(db, scope.tenant_id, UUID(str(outcome.result["credit_note_id"])))


def supplier_liability_balance(
    db: Session, *, tenant_id: UUID, supplier_ref: str, currency_code: str
) -> Decimal:
    amount = db.scalar(
        select(func.sum(LiabilityEvent.amount)).where(
            LiabilityEvent.tenant_id == tenant_id,
            LiabilityEvent.supplier_ref == supplier_ref,
            LiabilityEvent.currency_code == currency_code.upper(),
        )
    )
    return _money(Decimal(amount or 0))


__all__ = [
    "build_credit_application_accounting_consequence",
    "credit_application_tax_releases",
    "admit_historical_supplier_credit",
    "admit_historical_tax_deferral",
    "declare_tax_deferral",
    "invoice_deferred_tax_position",
    "admit_historical_settled_invoice",
    "admit_historical_unpaid_invoice",
    "build_payment_reversal_accounting_consequence",
    "recognize_approved_invoice",
    "reverse_supplier_payment",
    "apply_credit_note",
    "approve_credit_note",
    "approve_invoice",
    "approve_supplier_payment",
    "build_credit_accounting_consequence",
    "build_invoice_accounting_consequence",
    "build_payment_accounting_consequence",
    "cancel_supplier_payment",
    "create_credit_note",
    "create_invoice",
    "create_supplier_payment",
    "record_accounting_receipt",
    "record_settlement",
    "record_supplier_payment",
    "submit_credit_note",
    "submit_invoice",
    "supplier_liability_balance",
    "void_credit_note",
    "void_invoice",
]
