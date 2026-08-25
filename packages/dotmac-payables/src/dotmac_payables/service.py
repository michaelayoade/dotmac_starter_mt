"""Supplier-liability lifecycle in the caller's transaction.

Payables owns documents, liabilities and obligations. It produces a typed
accounting consequence but never imports or writes Accounting, and it accepts
settlement only as an immutable observation from the payment owner.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import execute_once, fingerprint_of
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_payables.contracts import (
    AccountingConsequence,
    AccountingEntry,
    AccountingReceiptInput,
    ApplyCredit,
    Conflict,
    ConsequenceSource,
    CreateCreditNote,
    CreateSupplierInvoice,
    CreditNoteStatus,
    InvalidAmount,
    InvalidTransition,
    InvoiceStatus,
    NotFound,
    ObligationStatus,
    SettlementObservationInput,
)
from dotmac_payables.models import (
    AccountingReceipt,
    CreditApplication,
    CreditNote,
    CreditNoteLine,
    LiabilityEvent,
    PaymentObligation,
    SettlementObservation,
    SupplierInvoice,
    SupplierInvoiceLine,
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
        stmt = stmt.with_for_update()
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
        invoice = _invoice(session, tenant_id, obligation.invoice_id)
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
        invoice = _invoice(session, tenant_id, obligation.invoice_id)
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
    invoice = _invoice(db, tenant_id, invoice_id)
    if invoice.status != InvoiceStatus.APPROVED:
        raise InvalidTransition(
            "only an approved invoice has an accounting consequence"
        )
    entries: list[AccountingEntry] = []
    for line in _invoice_lines(db, tenant_id, invoice.id):
        dimensions = tuple(
            (str(code), str(value)) for code, value in line.dimension_refs
        )
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
    for line in _credit_lines(db, tenant_id, credit.id):
        dimensions = tuple(
            (str(code), str(value)) for code, value in line.dimension_refs
        )
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
            entries.append(
                AccountingEntry(
                    account_ref=line.tax_account_ref,
                    credit=line.tax_amount,
                    description=f"Tax reversal: {line.description}",
                    dimension_refs=dimensions,
                )
            )
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
    "apply_credit_note",
    "approve_credit_note",
    "approve_invoice",
    "build_credit_accounting_consequence",
    "build_invoice_accounting_consequence",
    "create_credit_note",
    "create_invoice",
    "record_accounting_receipt",
    "record_settlement",
    "submit_credit_note",
    "submit_invoice",
    "supplier_liability_balance",
    "void_credit_note",
    "void_invoice",
]
