"""0.1.0a5: governed payment reversal, settled historical admission, recognition."""

# ruff: noqa: F811

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict, fingerprint_of
from dotmac_payables import models
from dotmac_payables.contracts import (
    AccountingReceiptInput,
    ApprovedInvoiceRecognitionEvidence,
    Conflict,
    CreateSupplierInvoice,
    HistoricalSettledInvoiceCommand,
    HistoricalSettledInvoiceEvidence,
    HistoricalSettlement,
    InvalidAmount,
    InvalidTransition,
    InvoiceStatus,
    ObligationSchedule,
    ObligationStatus,
    PayableLineInput,
    PayablesError,
    RecognizeApprovedInvoice,
    SupplierPaymentStatus,
    SupplierPaymentWithholdingInput,
)
from dotmac_payables.service import (
    admit_historical_settled_invoice,
    approve_invoice,
    build_invoice_accounting_consequence,
    build_payment_accounting_consequence,
    build_payment_reversal_accounting_consequence,
    create_invoice,
    recognize_approved_invoice,
    record_accounting_receipt,
    reverse_supplier_payment,
    submit_invoice,
    supplier_liability_balance,
)
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.unit.test_payables_supplier_payments import (  # noqa: F401
    CASH,
    LIABILITY,
    NOW,
    SUPPLIER,
    TENANT,
    WHT,
    _approve,
    _command,
    _count,
    _create,
    _obligation,
    _record,
    db,
)

LATER = NOW + timedelta(days=3)


# --- S1-7 governed reversal ---------------------------------------------------


def _receipted_payment(db: Session, *, wht: bool = False, amount: str = "1000"):
    ob = _obligation(db, amount=amount)
    withholdings = (
        (SupplierPaymentWithholdingInput(WHT, Decimal("50"), "WHT 5%"),) if wht else ()
    )
    payment = _create(db, _command([(ob, amount)], wht=withholdings))
    _approve(db, payment)
    _record(db, payment)
    consequence = build_payment_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    )
    record_accounting_receipt(
        db,
        scope=TenantScope(TENANT),
        command=AccountingReceiptInput(
            "supplier_payment",
            payment.id,
            consequence.fingerprint,
            "journal:p",
            "a" * 64,
        ),
        idempotency_key="receipt:payment",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    return payment, ob


def _reverse(
    db,
    payment,
    *,
    by="user:reverser",
    key="rev:1",
    date_=date(2026, 9, 5),
    why="bounced",
):
    return reverse_supplier_payment(
        db,
        scope=TenantScope(TENANT),
        payment_id=payment.id,
        reason=why,
        reversal_date=date_,
        reversed_by=by,
        idempotency_key=key,
        idempotency_expires_at=None,
        reversed_at=LATER,
        separation_of_duties=True,
    )


def test_reversal_reopens_the_obligation_and_emits_the_gl_reversal(db: Session) -> None:
    payment, ob = _receipted_payment(db, wht=True)
    assert ob.status == ObligationStatus.SETTLED
    reversed_ = _reverse(db, payment)
    db.refresh(ob)
    assert reversed_.status == SupplierPaymentStatus.REVERSED
    assert reversed_.reversal_reason == "bounced"
    assert ob.outstanding_amount == Decimal("1000")
    assert ob.status == ObligationStatus.OPEN
    assert supplier_liability_balance(
        db, tenant_id=TENANT, supplier_ref=SUPPLIER, currency_code="NGN"
    ) == Decimal("1000")
    # Settlement evidence is never edited; the reversal is a new positive event.
    assert _count(db, models.SettlementObservation) == 1
    event = db.scalars(
        select(models.LiabilityEvent).where(
            models.LiabilityEvent.event_kind == "settlement_reversed"
        )
    ).one()
    assert event.amount == Decimal("1000")

    consequence = build_payment_reversal_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    )
    assert consequence.source.document_kind == "supplier_payment_reversal"
    assert consequence.posting_date == date(2026, 9, 5)
    assert {(e.account_ref, e.debit, e.credit) for e in consequence.entries} == {
        (CASH, Decimal("950"), Decimal("0")),
        (WHT, Decimal("50"), Decimal("0")),
        (LIABILITY, Decimal("0"), Decimal("1000")),
    }
    record_accounting_receipt(
        db,
        scope=TenantScope(TENANT),
        command=AccountingReceiptInput(
            "supplier_payment_reversal",
            payment.id,
            consequence.fingerprint,
            "journal:r",
            "b" * 64,
        ),
        idempotency_key="receipt:reversal",
        idempotency_expires_at=None,
        recorded_at=LATER,
    )
    # The original consequence stays rebuildable (receipt replay) after reversal.
    assert build_payment_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    )
    # The reopened obligation can be paid again.
    again = _create(db, _command([(ob, "1000")], number="SPAY-0002"), key="pay:c:2")
    assert again.status == SupplierPaymentStatus.DRAFT


def test_partial_reversal_leaves_the_other_settlement_in_place(db: Session) -> None:
    ob = _obligation(db, amount="1000")
    first = _create(db, _command([(ob, "600")]))
    _approve(db, first)
    _record(db, first)
    second = _create(db, _command([(ob, "400")], number="SPAY-0002"), key="pay:c:2")
    _approve(db, second, key="pay:a:2")
    _record(db, second, key="pay:r:2", ref="BANK-REF-2")
    for payment, key in ((first, "r1"), (second, "r2")):
        consequence = build_payment_accounting_consequence(
            db, tenant_id=TENANT, payment_id=payment.id
        )
        record_accounting_receipt(
            db,
            scope=TenantScope(TENANT),
            command=AccountingReceiptInput(
                "supplier_payment", payment.id, consequence.fingerprint, key, "a" * 64
            ),
            idempotency_key=key,
            idempotency_expires_at=None,
            recorded_at=NOW,
        )
    _reverse(db, first)
    db.refresh(ob)
    assert ob.outstanding_amount == Decimal("600")
    assert ob.status == ObligationStatus.PARTIALLY_SETTLED


@pytest.mark.parametrize("actor", ["user:recorder", "USER:APPROVER"])
def test_reverser_must_differ_from_recorder_and_approver(
    db: Session, actor: str
) -> None:
    payment, ob = _receipted_payment(db)
    with pytest.raises(InvalidTransition, match="recorder and approver"):
        _reverse(db, payment, by=actor)
    assert payment.status == SupplierPaymentStatus.RECORDED
    assert ob.outstanding_amount == Decimal("0")


def test_reversal_refusals(db: Session) -> None:
    ob = _obligation(db)
    payment = _create(db, _command([(ob, "1000")]))
    with pytest.raises(InvalidTransition, match="recorded supplier payment"):
        _reverse(db, payment)  # DRAFT
    _approve(db, payment)
    with pytest.raises(InvalidTransition, match="recorded supplier payment"):
        _reverse(db, payment, key="rev:2")  # APPROVED
    _record(db, payment)
    with pytest.raises(InvalidTransition, match="payment_not_in_ledger"):
        _reverse(db, payment, key="rev:3")  # no accounting receipt yet
    with pytest.raises(PayablesError, match="reversal reason is required"):
        _reverse(db, payment, key="rev:4", why=" ")


def test_reversal_date_and_double_reversal_are_refused(db: Session) -> None:
    payment, _ = _receipted_payment(db)
    with pytest.raises(InvalidTransition, match="before its value date"):
        _reverse(db, payment, date_=date(2026, 9, 1))
    _reverse(db, payment)
    with pytest.raises(InvalidTransition, match="recorded supplier payment"):
        _reverse(db, payment, key="rev:again")
    with pytest.raises(InvalidTransition):
        _approve(db, payment, key="x")
    with pytest.raises(PayablesError, match="supplier payment was not found"):
        build_payment_reversal_accounting_consequence(
            db, tenant_id=uuid.uuid4(), payment_id=payment.id
        )


def test_no_reversal_consequence_before_reversal(db: Session) -> None:
    payment, _ = _receipted_payment(db)
    with pytest.raises(InvalidTransition, match="reversal consequence"):
        build_payment_reversal_accounting_consequence(
            db, tenant_id=TENANT, payment_id=payment.id
        )


def test_reversal_is_idempotent(db: Session) -> None:
    payment, ob = _receipted_payment(db)
    _reverse(db, payment)
    _reverse(db, payment)  # replay: same key, same facts
    with pytest.raises(IdempotencyConflict):
        _reverse(db, payment, why="different reason")
    assert _count(db, models.LiabilityEvent) == 3  # recognised, settled, reversed
    db.refresh(ob)
    assert ob.outstanding_amount == Decimal("1000")


def test_database_checks_refuse_a_forged_reversal(db: Session) -> None:
    payment, _ = _receipted_payment(db)
    for values in (
        {  # reversed by the recorder
            "status": SupplierPaymentStatus.REVERSED,
            "reversed_by": "USER:RECORDER",
            "reversed_at": NOW,
            "reversal_reason": "x",
            "reversal_date": date(2026, 9, 5),
        },
        {"status": SupplierPaymentStatus.REVERSED, "reversed_by": "user:r"},
        {"reversed_by": "user:r"},  # reversal facts on a RECORDED payment
    ):
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(
                    update(models.SupplierPayment)
                    .where(models.SupplierPayment.id == payment.id)
                    .values(**values)
                )


# --- settled historical admission ---------------------------------------------


def _settled_case(db: Session, *, schedule_amount: str = "1075", n: int = 1):
    invoice = create_invoice(
        db,
        scope=TenantScope(TENANT),
        command=CreateSupplierInvoice(
            number=f"PINV-H-{n}",
            supplier_ref=SUPPLIER,
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number=f"H-{n}",
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref=LIABILITY,
            lines=(
                PayableLineInput(
                    description="Fibre",
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                    posting_account_ref="account:5100",
                    tax_amount=Decimal("75"),
                    tax_account_ref="account:2200",
                ),
            ),
            schedule=(ObligationSchedule(date(2026, 8, 31), Decimal(schedule_amount)),),
        ),
        idempotency_key=f"h:create:{n}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    invoice.status = InvoiceStatus.APPROVED
    db.flush()
    names = (
        "number supplier_ref supplier_name_snapshot supplier_document_number "
        "invoice_date received_date currency_code exchange_rate subtotal tax_amount "
        "total_amount liability_account_ref procurement_ref "
        "receipt_evidence_fingerprint "
        "submitted_by submitted_at approved_by approved_at approval_reference"
    ).split()
    settlements = (
        HistoricalSettlement(
            "alloc:1",
            "PAY-100",
            date(2026, 8, 20),
            Decimal("300.00"),
            Decimal("0.00"),
            "NGN",
            Decimal("1"),
            "cv1:" + "1" * 64,
            "legacy:gl.journal_entry:p100",
            "v2:journal:p100",
        ),
        HistoricalSettlement(
            "alloc:2",
            "PAY-101",
            date(2026, 8, 25),
            Decimal("200.00"),
            Decimal("10.00"),
            "NGN",
            Decimal("1"),
            "cv1:" + "2" * 64,
            "legacy:gl.journal_entry:p101",
            "v2:journal:p101",
        ),
    )
    evidence = HistoricalSettledInvoiceEvidence(
        tenant_id=TENANT,
        source_owner="legacy",
        source_invoice_id=f"source:h{n}",
        source_snapshot_sha256="a" * 64,
        invoice_id=invoice.id,
        source_status="POSTED",
        invoice_type="STANDARD",
        settlements=settlements,
        settled_amount=Decimal("500.00"),
        residual_amount=Decimal("575.00"),
        withholding_tax_amount=Decimal("10.00"),
        prepayment_amount=Decimal(0),
        due_date=date(2026, 8, 31),
        original_posted_at=NOW,
        source_journal_ref="source:journal",
        destination_journal_ref="destination:journal",
        accounting_evidence_fingerprint="cv1:" + "b" * 64,
        facts_fingerprint="cv1:" + "c" * 64,
        **{name: getattr(invoice, name) for name in names},
    )
    return invoice, evidence


class _Verifier:
    def __init__(self, evidence):
        self.evidence = evidence

    def verify(self, *, scope, command, destination):
        return self.evidence


def _command_for(evidence) -> HistoricalSettledInvoiceCommand:
    return HistoricalSettledInvoiceCommand(
        evidence.source_owner,
        evidence.source_invoice_id,
        evidence.invoice_id,
        evidence.source_snapshot_sha256,
        evidence.fingerprint(),
    )


def _admit(db, evidence, key="h:admit", *, command=None):
    return admit_historical_settled_invoice(
        db,
        scope=TenantScope(TENANT),
        command=command or _command_for(evidence),
        verifier=_Verifier(evidence),
        idempotency_key=key,
        idempotency_expires_at=None,
        admitted_at=NOW + timedelta(days=30),
    )


def test_settled_admission_records_history_and_the_proved_residual(db: Session) -> None:
    invoice, evidence = _settled_case(db)
    receipt = _admit(db, evidence)
    obligation = db.scalars(select(models.PaymentObligation)).one()
    assert obligation.original_amount == Decimal("1075")
    assert obligation.outstanding_amount == Decimal("575")
    assert obligation.status == ObligationStatus.PARTIALLY_SETTLED
    rows = db.scalars(
        select(models.HistoricalSettlementRecord).order_by(
            models.HistoricalSettlementRecord.line_number
        )
    ).all()
    assert [(r.payment_ref, r.applied_amount, r.withholding_amount) for r in rows] == [
        ("PAY-100", Decimal("300"), Decimal("0")),
        ("PAY-101", Decimal("200"), Decimal("10")),
    ]
    assert supplier_liability_balance(
        db, tenant_id=TENANT, supplier_ref=SUPPLIER, currency_code="NGN"
    ) == Decimal("575")
    kinds = sorted(
        e.event_kind for e in db.scalars(select(models.LiabilityEvent)).all()
    )
    assert kinds == [
        "historical_invoice_recognized",
        "historical_settlement_recorded",
        "historical_settlement_recorded",
    ]
    assert receipt.evidence["settlements"][1]["payment_ref"] == "PAY-101"
    # No GL consequence: legacy AP control already holds the residual.
    with pytest.raises(InvalidTransition, match="imported accounting evidence"):
        build_invoice_accounting_consequence(
            db, tenant_id=TENANT, invoice_id=invoice.id
        )
    assert _count(db, models.AccountingReceipt) == 0


def test_settled_admission_is_idempotent_and_conflicts_on_changed_evidence(
    db: Session,
) -> None:
    invoice, evidence = _settled_case(db)
    first = _admit(db, evidence)
    again = _admit(db, evidence, key="h:admit:2")
    assert again.id == first.id
    assert _count(db, models.HistoricalSettlementRecord) == 2
    changed = replace(evidence, facts_fingerprint="cv1:" + "d" * 64)
    with pytest.raises(Conflict, match="different permanent historical evidence"):
        _admit(db, changed, key="h:admit:3")


def test_residual_is_payable_through_the_supplier_payment_lifecycle(
    db: Session,
) -> None:
    _, evidence = _settled_case(db)
    _admit(db, evidence)
    obligation = db.scalars(select(models.PaymentObligation)).one()
    with pytest.raises(InvalidAmount):
        _create(db, _command([(obligation, "575.01")]))
    payment = _create(db, _command([(obligation, "575")]))
    _approve(db, payment)
    _record(db, payment)
    db.refresh(obligation)
    assert obligation.status == ObligationStatus.SETTLED
    entries = build_payment_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    ).entries
    assert entries[0].account_ref == LIABILITY and entries[0].debit == Decimal("575")


def _refused(db, evidence, exc, match):
    with pytest.raises(exc, match=match):
        _admit(db, evidence)
    assert _count(db, models.HistoricalInvoiceReceipt) == 0
    assert _count(db, models.HistoricalSettlementRecord) == 0
    assert _count(db, models.PaymentObligation) == 0


def test_settled_admission_refuses_unprovable_or_inconsistent_evidence(
    db: Session,
) -> None:
    _, good = _settled_case(db)
    first, second = good.settlements
    # FX mismatch on a settlement.
    _refused(
        db,
        replace(good, settlements=(replace(first, exchange_rate=Decimal("2")), second)),
        InvalidAmount,
        "fx_difference_unsupported",
    )
    _refused(
        db,
        replace(good, settlements=(replace(first, currency_code="USD"), second)),
        InvalidAmount,
        "fx_difference_unsupported",
    )
    # Declared totals do not match the settlements.
    _refused(
        db, replace(good, settled_amount=Decimal("499.00")), InvalidAmount, "do not sum"
    )
    _refused(
        db,
        replace(good, withholding_tax_amount=Decimal("0.00")),
        InvalidAmount,
        "do not sum",
    )
    # Residual not proved by the settlements.
    _refused(
        db,
        replace(good, residual_amount=Decimal("574.00")),
        InvalidAmount,
        "not proved",
    )
    # Over-settlement.
    big = replace(first, applied_amount=Decimal("1200.00"))
    _refused(
        db,
        replace(
            good,
            settlements=(big, second),
            settled_amount=Decimal("1400.00"),
            residual_amount=Decimal("-325.00"),
        ),
        InvalidAmount,
        "exceed the invoice total",
    )
    # Fully settled: nothing open to admit.
    full = replace(first, applied_amount=Decimal("875.00"))
    _refused(
        db,
        replace(
            good,
            settlements=(full, second),
            settled_amount=Decimal("1075.00"),
            residual_amount=Decimal("0.00"),
        ),
        InvalidTransition,
        "fully settled",
    )
    # Duplicate legacy settlement id, bad dates, no settlements, prepayment.
    _refused(
        db,
        replace(
            good, settlements=(first, replace(second, source_settlement_id="alloc:1"))
        ),
        Conflict,
        "unique",
    )
    _refused(
        db,
        replace(
            good, settlements=(replace(first, settled_on=date(2026, 7, 1)), second)
        ),
        InvalidTransition,
        "precedes the invoice",
    )
    _refused(
        db,
        replace(
            good, settlements=(replace(first, settled_on=date(2027, 6, 1)), second)
        ),
        InvalidTransition,
        "follow admission",
    )
    _refused(
        db,
        replace(
            good,
            settlements=(),
            settled_amount=Decimal(0),
            residual_amount=Decimal("1075.00"),
            withholding_tax_amount=Decimal(0),
        ),
        InvalidTransition,
        "requires settlements",
    )
    _refused(
        db,
        replace(good, prepayment_amount=Decimal("1.00")),
        InvalidTransition,
        "prepayment",
    )
    # Withholding above the settled amount, malformed row fingerprint.
    _refused(
        db,
        replace(
            good,
            settlements=(replace(first, withholding_amount=Decimal("301.00")), second),
        ),
        InvalidAmount,
        "within the amount",
    )
    _refused(
        db,
        replace(
            good, settlements=(replace(first, source_row_fingerprint="nope"), second)
        ),
        PayablesError,
        "cv1",
    )
    # A schedule that is not one full-face line cannot be admitted.
    # Wrong evidence type (unpaid-shaped) is not accepted by the settled command.
    with pytest.raises(InvalidTransition, match="no typed evidence"):
        admit_historical_settled_invoice(
            db,
            scope=TenantScope(TENANT),
            command=_command_for(good),
            verifier=_Verifier(object()),
            idempotency_key="h:wrong",
            idempotency_expires_at=None,
            admitted_at=NOW + timedelta(days=30),
        )


def test_settled_admission_refuses_changed_destination_or_existing_recognition(
    db: Session,
) -> None:
    invoice, evidence = _settled_case(db)
    with pytest.raises(Conflict, match="differs on subtotal"):
        _admit(
            db,
            replace(
                evidence,
                total_amount=Decimal("1074.00"),
                residual_amount=Decimal("574.00"),
                subtotal=Decimal("999.00"),
            ),
        )
    db.add(
        models.AccountingReceipt(
            tenant_id=TENANT,
            document_kind="supplier_invoice",
            document_id=invoice.id,
            consequence_fingerprint="e" * 64,
            accounting_reference="j",
            accounting_evidence_fingerprint="f" * 64,
            recorded_at=NOW,
        )
    )
    db.flush()
    with pytest.raises(Conflict, match="already has owner recognition"):
        _admit(db, evidence, key="h:admit:2")


def test_historical_settlement_rows_are_rolled_back_with_a_refused_admission(
    db: Session,
) -> None:
    invoice, evidence = _settled_case(db)
    tampered = replace(evidence, supplier_ref="party:someone-else")
    with pytest.raises(Conflict, match="differs on supplier_ref"):
        _admit(db, tampered)
    assert _count(db, models.HistoricalSettlementRecord) == 0
    assert _count(db, models.LiabilityEvent) == 0


# --- recognition of an imported APPROVED invoice ------------------------------


def _imported_approved(db: Session, number: str = "PINV-R-1", doc: str = "R-1"):
    invoice = create_invoice(
        db,
        scope=TenantScope(TENANT),
        command=CreateSupplierInvoice(
            number=number,
            supplier_ref=SUPPLIER,
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number=doc,
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref=LIABILITY,
            lines=(
                PayableLineInput(
                    description="Fibre",
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                    posting_account_ref="account:5100",
                    tax_amount=Decimal("75"),
                    tax_account_ref="account:2200",
                ),
            ),
            schedule=(
                ObligationSchedule(date(2026, 8, 31), Decimal("600")),
                ObligationSchedule(date(2026, 9, 30), Decimal("475")),
            ),
        ),
        idempotency_key=f"r:create:{number}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    # The importer moves invoices to APPROVED by direct UPDATE.
    invoice.status = InvoiceStatus.APPROVED
    invoice.approved_by = "legacy:user"
    invoice.approved_at = NOW
    invoice.approval_reference = "legacy-approval"
    db.flush()
    return invoice


def _rec_evidence(invoice, **over):
    values = {
        "tenant_id": TENANT,
        "source_owner": "legacy",
        "source_invoice_id": f"src:{invoice.id}",
        "source_snapshot_sha256": "a" * 64,
        "invoice_id": invoice.id,
        "source_status": "APPROVED",
        "source_journal_entry_id": None,
        "source_posted_at": None,
        "source_gl_line_count": 0,
        "number": invoice.number,
        "supplier_ref": invoice.supplier_ref,
        "currency_code": invoice.currency_code,
        "total_amount": invoice.total_amount,
        "facts_fingerprint": "cv1:" + "9" * 64,
    }
    values.update(over)
    return ApprovedInvoiceRecognitionEvidence(**values)


class _RecVerifier:
    def __init__(self, evidence):
        self.evidence = evidence

    def verify(self, *, scope, command, destination):
        return self.evidence


def _rec_command(
    evidence,
    *,
    evidence_ref="census:abc#12",
    actor="user:finance",
    approval="appr:1",
    **over,
):
    values = {
        "invoice_id": evidence.invoice_id,
        "evidence_reference": evidence_ref,
        "recognized_by": actor,
        "approval_reference": approval,
        "source_owner": evidence.source_owner,
        "source_invoice_id": evidence.source_invoice_id,
        "source_snapshot_sha256": evidence.source_snapshot_sha256,
        "evidence_fingerprint": evidence.fingerprint(),
    }
    values.update(over)
    return RecognizeApprovedInvoice(**values)


def _recognize(
    db,
    invoice,
    *,
    key="r:1",
    evidence="census:abc#12",
    actor="user:finance",
    approval="appr:1",
    proof=None,
    command=None,
):
    proof = proof or _rec_evidence(invoice)
    return recognize_approved_invoice(
        db,
        scope=TenantScope(TENANT),
        command=command
        or _rec_command(proof, evidence_ref=evidence, actor=actor, approval=approval),
        verifier=_RecVerifier(proof),
        idempotency_key=key,
        idempotency_expires_at=None,
        recognized_at=LATER,
    )


def test_recognition_writes_what_approve_invoice_would_without_touching_the_invoice(
    db: Session,
) -> None:
    imported = _imported_approved(db)

    def frozen(invoice):
        # SQLite drops tzinfo on reload; compare the instant, not the tag.
        return (
            invoice.status,
            invoice.approved_by,
            invoice.approved_at.replace(tzinfo=None),
            invoice.approval_reference,
            invoice.submitted_by,
            invoice.request_fingerprint,
            invoice.total_amount,
        )

    before = frozen(imported)
    _recognize(db, imported)
    assert frozen(imported) == before

    # Reference: the same invoice approved through the normal path.
    normal = create_invoice(
        db,
        scope=TenantScope(TENANT),
        command=CreateSupplierInvoice(
            number="PINV-R-2",
            supplier_ref=SUPPLIER,
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number="R-2",
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref=LIABILITY,
            lines=(
                PayableLineInput(
                    description="Fibre",
                    quantity=Decimal("1"),
                    unit_price=Decimal("1000"),
                    posting_account_ref="account:5100",
                    tax_amount=Decimal("75"),
                    tax_account_ref="account:2200",
                ),
            ),
            schedule=(
                ObligationSchedule(date(2026, 8, 31), Decimal("600")),
                ObligationSchedule(date(2026, 9, 30), Decimal("475")),
            ),
        ),
        idempotency_key="r:create:normal",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    submit_invoice(
        db,
        scope=TenantScope(TENANT),
        invoice_id=normal.id,
        submitted_by="user:ap",
        submitted_at=NOW,
    )
    approve_invoice(
        db,
        scope=TenantScope(TENANT),
        invoice_id=normal.id,
        approval_reference="appr:1",
        approved_by="user:finance",
        idempotency_key="r:approve:normal",
        idempotency_expires_at=None,
        approved_at=LATER,
    )

    def shape(invoice_id):
        obligations = db.scalars(
            select(models.PaymentObligation)
            .where(models.PaymentObligation.invoice_id == invoice_id)
            .order_by(models.PaymentObligation.sequence)
        ).all()
        event = db.scalars(
            select(models.LiabilityEvent).where(
                models.LiabilityEvent.document_id == invoice_id
            )
        ).one()
        return (
            [
                (
                    o.sequence,
                    o.due_date,
                    o.original_amount,
                    o.outstanding_amount,
                    o.status,
                )
                for o in obligations
            ],
            (
                event.event_kind,
                event.amount,
                event.source_reference,
                event.occurred_at,
                event.supplier_ref,
                event.currency_code,
            ),
        )

    assert shape(imported.id) == shape(normal.id)
    for invoice, actor in ((imported, "user:finance"), (normal, "user:finance")):
        event = db.scalars(
            select(models.LiabilityEvent).where(
                models.LiabilityEvent.document_id == invoice.id
            )
        ).one()
        assert event.source_fingerprint == fingerprint_of(
            {
                "invoice_id": str(invoice.id),
                "approval_reference": "appr:1",
                "approved_by": actor,
                "approved_at": LATER.isoformat(),
            }
        )
    # The recognition record keeps the evidence reference, actor and approval.
    row = db.scalars(select(models.InvoiceRecognition)).one()
    assert (row.evidence_reference, row.recognized_by, row.approval_reference) == (
        "census:abc#12",
        "user:finance",
        "appr:1",
    )


def test_recognized_invoice_has_a_consequence_and_accepts_a_receipt(
    db: Session,
) -> None:
    imported = _imported_approved(db)
    with pytest.raises(InvalidTransition, match="no owner recognition"):
        build_invoice_accounting_consequence(
            db, tenant_id=TENANT, invoice_id=imported.id
        )
    _recognize(db, imported)
    consequence = build_invoice_accounting_consequence(
        db, tenant_id=TENANT, invoice_id=imported.id
    )
    assert (
        sum(e.debit for e in consequence.entries)
        == sum(e.credit for e in consequence.entries)
        == Decimal("1075")
    )
    receipt = record_accounting_receipt(
        db,
        scope=TenantScope(TENANT),
        command=AccountingReceiptInput(
            "supplier_invoice",
            imported.id,
            consequence.fingerprint,
            "journal:i",
            "a" * 64,
        ),
        idempotency_key="r:receipt",
        idempotency_expires_at=None,
        recorded_at=LATER,
    )
    assert receipt.document_id == imported.id


def test_recognition_is_idempotent_and_conflicts_on_other_evidence(db: Session) -> None:
    imported = _imported_approved(db)
    _recognize(db, imported)
    _recognize(db, imported)  # replay
    _recognize(db, imported, key="r:2")  # same facts, new key
    assert _count(db, models.PaymentObligation) == 2
    assert _count(db, models.LiabilityEvent) == 1
    with pytest.raises(Conflict, match="other evidence"):
        _recognize(db, imported, key="r:3", evidence="census:zzz#1")
    with pytest.raises(IdempotencyConflict):
        _recognize(db, imported, evidence="census:zzz#1")


def test_recognition_refuses_everything_but_an_unrecognized_approved_invoice(
    db: Session,
) -> None:
    draft = create_invoice(
        db,
        scope=TenantScope(TENANT),
        command=CreateSupplierInvoice(
            number="PINV-R-3",
            supplier_ref=SUPPLIER,
            supplier_name_snapshot="F",
            supplier_document_number="R-3",
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref=LIABILITY,
            lines=(PayableLineInput("x", Decimal("1"), Decimal("10"), "account:5100"),),
            schedule=(ObligationSchedule(date(2026, 8, 31), Decimal("10")),),
        ),
        idempotency_key="r:draft",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    with pytest.raises(InvalidTransition, match="only an approved invoice"):
        _recognize(db, draft)
    draft.status = InvoiceStatus.SUBMITTED
    db.flush()
    with pytest.raises(InvalidTransition, match="only an approved invoice"):
        _recognize(db, draft, key="r:s")

    # Already approved through the owner path: has recognition and obligations.
    ob = _obligation(db)
    with pytest.raises(InvalidTransition, match="already has recognition"):
        _recognize(db, db.get(models.SupplierInvoice, ob.invoice_id), key="r:n")

    # Historical admission evidence blocks it as well.
    imported = _imported_approved(db, "PINV-R-4", "R-4")
    db.add(
        models.AccountingReceipt(
            tenant_id=TENANT,
            document_kind="supplier_invoice",
            document_id=imported.id,
            consequence_fingerprint="e" * 64,
            accounting_reference="j",
            accounting_evidence_fingerprint="f" * 64,
            recorded_at=NOW,
        )
    )
    db.flush()
    with pytest.raises(InvalidTransition, match="already has recognition"):
        _recognize(db, imported, key="r:a")

    proof = _rec_evidence(imported)
    for bad in (
        _rec_command(proof, evidence_ref=" "),
        _rec_command(proof, actor=" "),
        _rec_command(proof, approval=" "),
    ):
        with pytest.raises(PayablesError, match="is required"):
            recognize_approved_invoice(
                db,
                scope=TenantScope(TENANT),
                command=bad,
                verifier=_RecVerifier(proof),
                idempotency_key="r:bad",
                idempotency_expires_at=None,
                recognized_at=LATER,
            )
    assert _count(db, models.InvoiceRecognition) == 0


def test_recognition_is_tenant_scoped(db: Session) -> None:
    imported = _imported_approved(db)
    other = uuid.uuid4()
    proof = _rec_evidence(imported, tenant_id=other)
    with pytest.raises(PayablesError, match="not found"):
        recognize_approved_invoice(
            db,
            scope=TenantScope(other),
            command=_rec_command(proof),
            verifier=_RecVerifier(proof),
            idempotency_key="r:x",
            idempotency_expires_at=None,
            recognized_at=LATER,
        )
    assert _count(db, models.PaymentObligation) == 0


def test_recognition_requires_proof_the_legacy_invoice_was_never_posted(
    db: Session,
) -> None:
    imported = _imported_approved(db)
    posted_at = LATER
    for override, match in (
        ({"source_status": "POSTED"}, "never posted"),
        ({"source_journal_entry_id": "je-1"}, "never posted"),
        ({"source_posted_at": posted_at}, "never posted"),
        ({"source_gl_line_count": 2}, "never posted"),
    ):
        proof = _rec_evidence(imported, **override)
        with pytest.raises(InvalidTransition, match=match):
            _recognize(db, imported, proof=proof)
    # Evidence must be typed, bound to the command and to this invoice.
    good = _rec_evidence(imported)
    with pytest.raises(InvalidTransition, match="no typed evidence"):
        recognize_approved_invoice(
            db,
            scope=TenantScope(TENANT),
            command=_rec_command(good),
            verifier=_RecVerifier(object()),
            idempotency_key="r:t",
            idempotency_expires_at=None,
            recognized_at=LATER,
        )
    with pytest.raises(Conflict, match="fingerprint differs"):
        _recognize(
            db,
            imported,
            proof=good,
            command=_rec_command(good, evidence_fingerprint="cv1:" + "0" * 64),
        )
    with pytest.raises(Conflict, match="identity differs"):
        _recognize(
            db,
            imported,
            proof=_rec_evidence(imported, source_invoice_id="other"),
            command=_rec_command(good),
        )
    with pytest.raises(Conflict, match="differs on number"):
        _recognize(db, imported, proof=_rec_evidence(imported, number="OTHER"))
    with pytest.raises(InvalidTransition, match="trusted verifier"):
        recognize_approved_invoice(
            db,
            scope=TenantScope(TENANT),
            command=_rec_command(good),
            verifier=None,
            idempotency_key="r:n",
            idempotency_expires_at=None,
            recognized_at=LATER,
        )
    assert _count(db, models.InvoiceRecognition) == 0
    assert _count(db, models.PaymentObligation) == 0
    # The verifier result is recorded on the permanent row.
    _recognize(db, imported)
    row = db.scalars(select(models.InvoiceRecognition)).one()
    assert row.evidence_fingerprint == good.fingerprint()
    assert row.evidence["source_status"] == "APPROVED"
    assert row.source_snapshot_sha256 == "a" * 64
    with pytest.raises(Conflict, match="other evidence"):
        _recognize(
            db,
            imported,
            key="r:other",
            proof=_rec_evidence(imported, facts_fingerprint="cv1:" + "8" * 64),
        )


# --- review hardening: A5-1, A5-2, A5-4 ---------------------------------------


def test_settlement_requires_both_journal_references(db: Session) -> None:
    _, good = _settled_case(db)
    first, second = good.settlements
    for field in ("source_journal_ref", "destination_journal_ref"):
        _refused(
            db,
            replace(good, settlements=(replace(first, **{field: " "}), second)),
            PayablesError,
            "journal reference is required",
        )
    admitted = _admit(db, good)
    rows = db.scalars(select(models.HistoricalSettlementRecord)).all()
    assert {(r.source_journal_ref, r.destination_journal_ref) for r in rows} == {
        ("legacy:gl.journal_entry:p100", "v2:journal:p100"),
        ("legacy:gl.journal_entry:p101", "v2:journal:p101"),
    }
    assert admitted.evidence["settlements"][0]["destination_journal_ref"]


def test_one_legacy_settlement_cannot_settle_two_invoices(db: Session) -> None:
    _, first_evidence = _settled_case(db, n=1)
    _admit(db, first_evidence)
    _, second_evidence = _settled_case(db, n=2)
    # Same legacy allocation ids presented against another invoice.
    with pytest.raises(Conflict, match="already admitted against an invoice"):
        _admit(db, second_evidence, key="h:admit:2")
    assert _count(db, models.HistoricalSettlementRecord) == 2


def test_reversal_cannot_reopen_beyond_face_less_history_and_credits(
    db: Session,
) -> None:
    payment, ob = _receipted_payment(db)
    db.add(
        models.CreditApplication(
            tenant_id=TENANT,
            credit_note_id=uuid.uuid4(),
            obligation_id=ob.id,
            amount=Decimal("100"),
            applied_by="u",
            applied_at=NOW,
        )
    )
    db.flush()
    with pytest.raises(InvalidAmount, match="less historical settlements and credit"):
        _reverse(db, payment)
    assert payment.status == SupplierPaymentStatus.RECORDED


def test_historical_settlement_money_is_two_decimal_only(db: Session) -> None:
    _, good = _settled_case(db)
    first, second = good.settlements
    _refused(
        db,
        replace(
            good,
            settlements=(replace(first, applied_amount=Decimal("300.001")), second),
        ),
        InvalidAmount,
        "two-decimal",
    )
