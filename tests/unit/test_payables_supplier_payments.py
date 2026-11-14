"""Supplier-payment lifecycle, settlement and GL consequence canaries (a4)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.models import Tenant
from dotmac_payables import models
from dotmac_payables.contracts import (
    AccountingReceiptInput,
    Conflict,
    CreateSupplierInvoice,
    CreateSupplierPayment,
    InvalidAmount,
    InvalidTransition,
    NotFound,
    ObligationSchedule,
    ObligationStatus,
    PayableLineInput,
    PayablesError,
    SupplierPaymentAllocationInput,
    SupplierPaymentStatus,
    SupplierPaymentWithholdingInput,
)
from dotmac_payables.service import (
    approve_invoice,
    approve_supplier_payment,
    build_payment_accounting_consequence,
    cancel_supplier_payment,
    create_invoice,
    create_supplier_payment,
    record_accounting_receipt,
    record_supplier_payment,
    submit_invoice,
    supplier_liability_balance,
)
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

TENANT = uuid.uuid4()
OTHER = uuid.uuid4()
NOW = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)
SUPPLIER = "party:supplier-1"
LIABILITY = "account:2000"
CASH = "account:1010"
WHT = "account:2300"


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_payables": None}},
    )
    Tenant.__table__.create(engine)
    IdempotencyRecord.__table__.create(engine)
    for table_name in models.TABLES:
        models.metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT, slug="alpha", name="Alpha"))
        session.add(Tenant(id=OTHER, slug="bravo", name="Bravo"))
        session.flush()
        yield session
    engine.dispose()


_counter = {"n": 0}


def _obligation(
    db: Session,
    *,
    amount: str = "1000",
    supplier: str = SUPPLIER,
    currency: str = "NGN",
    tenant: uuid.UUID = TENANT,
    liability: str = LIABILITY,
) -> models.PaymentObligation:
    """An approved invoice with one open obligation of ``amount`` (tax-free)."""
    _counter["n"] += 1
    n = _counter["n"]
    scope = TenantScope(tenant)
    invoice = create_invoice(
        db,
        scope=scope,
        command=CreateSupplierInvoice(
            number=f"PINV-{n:04d}",
            supplier_ref=supplier,
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number=f"FV-{n}",
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code=currency,
            exchange_rate=Decimal("1"),
            liability_account_ref=liability,
            lines=(
                PayableLineInput(
                    description="Fibre cable",
                    quantity=Decimal("1"),
                    unit_price=Decimal(amount),
                    posting_account_ref="account:5100",
                ),
            ),
            schedule=(ObligationSchedule(date(2026, 8, 31), Decimal(amount)),),
        ),
        idempotency_key=f"inv:create:{n}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    submit_invoice(
        db, scope=scope, invoice_id=invoice.id, submitted_by="user:ap", submitted_at=NOW
    )
    approve_invoice(
        db,
        scope=scope,
        invoice_id=invoice.id,
        approval_reference=f"approval:{n}",
        approved_by="user:controller",
        idempotency_key=f"inv:approve:{n}",
        idempotency_expires_at=None,
        approved_at=NOW,
    )
    return db.scalars(
        select(models.PaymentObligation).where(
            models.PaymentObligation.tenant_id == tenant,
            models.PaymentObligation.invoice_id == invoice.id,
        )
    ).one()


def _command(
    obligations: list[tuple[models.PaymentObligation, str]],
    *,
    number: str = "SPAY-0001",
    supplier: str = SUPPLIER,
    currency: str = "NGN",
    wht: tuple[SupplierPaymentWithholdingInput, ...] = (),
    prepared_by: str = "user:preparer",
) -> CreateSupplierPayment:
    return CreateSupplierPayment(
        number=number,
        supplier_ref=supplier,
        supplier_name_snapshot="Fibre Vendor Ltd",
        currency_code=currency,
        exchange_rate=Decimal("1"),
        value_date=date(2026, 9, 2),
        cash_account_ref=CASH,
        payer_instrument_ref="instrument:bank-main",
        allocations=tuple(
            SupplierPaymentAllocationInput(ob.id, Decimal(amount))
            for ob, amount in obligations
        ),
        prepared_by=prepared_by,
        withholdings=wht,
    )


def _create(db: Session, command: CreateSupplierPayment, key: str = "pay:create:1"):
    return create_supplier_payment(
        db,
        scope=TenantScope(TENANT),
        command=command,
        idempotency_key=key,
        idempotency_expires_at=None,
        prepared_at=NOW,
    )


def _approve(db, payment, *, by="user:approver", key="pay:approve:1", tenant=TENANT):
    return approve_supplier_payment(
        db,
        scope=TenantScope(tenant),
        payment_id=payment.id,
        approval_reference="approval:pay:1",
        approved_by=by,
        idempotency_key=key,
        idempotency_expires_at=None,
        approved_at=NOW + timedelta(hours=1),
    )


def _record(db, payment, *, by="user:recorder", ref="BANK-REF-1", key="pay:record:1"):
    return record_supplier_payment(
        db,
        scope=TenantScope(TENANT),
        payment_id=payment.id,
        bank_reference=ref,
        recorded_by=by,
        idempotency_key=key,
        idempotency_expires_at=None,
        recorded_at=NOW + timedelta(hours=2),
    )


def _recorded(db: Session, *, wht: bool = False):
    ob = _obligation(db, amount="1000")
    withholdings = (
        (SupplierPaymentWithholdingInput(WHT, Decimal("50"), "WHT 5%", "CERT-1"),)
        if wht
        else ()
    )
    payment = _create(db, _command([(ob, "1000")], wht=withholdings))
    _approve(db, payment)
    _record(db, payment)
    return payment, ob


def _count(db: Session, model: type) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


# --- happy path ---------------------------------------------------------------


def test_lifecycle_settles_obligations_and_emits_balanced_consequence(
    db: Session,
) -> None:
    first = _obligation(db, amount="1000")
    second = _obligation(db, amount="400")
    payment = _create(db, _command([(first, "1000"), (second, "150")]))
    assert payment.status == SupplierPaymentStatus.DRAFT
    assert payment.gross_amount == Decimal("1150") == payment.net_amount
    # A draft moves nothing.
    assert first.outstanding_amount == Decimal("1000")
    assert _count(db, models.SettlementObservation) == 0

    _approve(db, payment)
    assert payment.status == SupplierPaymentStatus.APPROVED
    assert payment.approved_by == "user:approver"
    assert first.outstanding_amount == Decimal("1000")

    _record(db, payment)
    db.refresh(first)
    db.refresh(second)
    assert payment.status == SupplierPaymentStatus.RECORDED
    assert payment.bank_reference == "BANK-REF-1"
    assert first.status == ObligationStatus.SETTLED
    assert second.status == ObligationStatus.PARTIALLY_SETTLED
    assert second.outstanding_amount == Decimal("250")
    assert _count(db, models.SettlementObservation) == 2
    # Liability is reduced by exactly the gross paid (invoices 1400 recognised).
    assert supplier_liability_balance(
        db, tenant_id=TENANT, supplier_ref=SUPPLIER, currency_code="NGN"
    ) == Decimal("250")


def test_consequence_dr_ap_cr_cash_and_optional_wht(db: Session) -> None:
    payment, _ = _recorded(db, wht=True)
    consequence = build_payment_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    )
    assert consequence.source.owner == "payables"
    assert consequence.source.document_kind == "supplier_payment"
    assert consequence.source.document_id == str(payment.id)
    assert consequence.posting_date == date(2026, 9, 2)
    entries = {(e.account_ref, e.debit, e.credit) for e in consequence.entries}
    assert entries == {
        (LIABILITY, Decimal("1000"), Decimal("0")),
        (CASH, Decimal("0"), Decimal("950")),
        (WHT, Decimal("0"), Decimal("50")),
    }
    assert sum(e.debit for e in consequence.entries) == sum(
        e.credit for e in consequence.entries
    )
    # Deterministic: rebuilding yields the same fingerprint.
    again = build_payment_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    )
    assert again.fingerprint == consequence.fingerprint


def test_consequence_without_wht_has_two_legs_and_groups_liability_accounts(
    db: Session,
) -> None:
    a = _obligation(db, amount="300", liability="account:2000")
    b = _obligation(db, amount="200", liability="account:2001")
    c = _obligation(db, amount="100", liability="account:2000")
    payment = _create(db, _command([(a, "300"), (b, "200"), (c, "100")]))
    _approve(db, payment)
    _record(db, payment)
    consequence = build_payment_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    )
    assert [(e.account_ref, e.debit, e.credit) for e in consequence.entries] == [
        ("account:2000", Decimal("400"), Decimal("0")),
        ("account:2001", Decimal("200"), Decimal("0")),
        (CASH, Decimal("0"), Decimal("600")),
    ]


def test_accounting_receipt_round_trip_for_a_payment(db: Session) -> None:
    payment, _ = _recorded(db)
    consequence = build_payment_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    )
    command = AccountingReceiptInput(
        document_kind="supplier_payment",
        document_id=payment.id,
        consequence_fingerprint=consequence.fingerprint,
        accounting_reference="journal:J-1",
        accounting_evidence_fingerprint="c" * 64,
    )
    receipt = record_accounting_receipt(
        db,
        scope=TenantScope(TENANT),
        command=command,
        idempotency_key="receipt:1",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    replay = record_accounting_receipt(
        db,
        scope=TenantScope(TENANT),
        command=command,
        idempotency_key="receipt:1b",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    assert replay.id == receipt.id
    with pytest.raises(Conflict, match="does not match"):
        record_accounting_receipt(
            db,
            scope=TenantScope(TENANT),
            command=replace(command, consequence_fingerprint="d" * 64),
            idempotency_key="receipt:2",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )


def test_no_consequence_before_recording(db: Session) -> None:
    ob = _obligation(db)
    payment = _create(db, _command([(ob, "1000")]))
    for stage in ("draft", "approved"):
        with pytest.raises(InvalidTransition, match="recorded supplier payment"):
            build_payment_accounting_consequence(
                db, tenant_id=TENANT, payment_id=payment.id
            )
        if stage == "draft":
            _approve(db, payment)


# --- separation of duties -----------------------------------------------------


def test_approver_must_differ_from_preparer_case_insensitively(db: Session) -> None:
    ob = _obligation(db)
    payment = _create(db, _command([(ob, "1000")], prepared_by="User:Preparer"))
    with pytest.raises(InvalidTransition, match="preparer"):
        _approve(db, payment, by="  user:preparer ")
    assert payment.status == SupplierPaymentStatus.DRAFT


@pytest.mark.parametrize("actor", ["user:preparer", "user:approver"])
def test_recorder_must_differ_from_preparer_and_approver(
    db: Session, actor: str
) -> None:
    ob = _obligation(db)
    payment = _create(db, _command([(ob, "1000")]))
    _approve(db, payment)
    with pytest.raises(InvalidTransition, match="preparer and approver"):
        _record(db, payment, by=actor)
    assert payment.status == SupplierPaymentStatus.APPROVED
    assert ob.outstanding_amount == Decimal("1000")
    assert _count(db, models.SettlementObservation) == 0


def test_database_checks_refuse_a_forged_sod_or_evidence_row(db: Session) -> None:
    ob = _obligation(db)
    payment = _create(db, _command([(ob, "1000")]))
    forged = [
        {  # approved by the preparer
            "status": SupplierPaymentStatus.APPROVED,
            "approved_by": payment.prepared_by,
            "approved_at": NOW,
            "approval_reference": "x",
        },
        {  # approved without evidence
            "status": SupplierPaymentStatus.APPROVED,
            "approved_by": "user:approver",
        },
        {  # recorded without any approval
            "status": SupplierPaymentStatus.RECORDED,
            "recorded_by": "user:recorder",
            "recorded_at": NOW,
            "bank_reference": "R",
        },
    ]
    for values in forged:
        with pytest.raises(IntegrityError):
            with db.begin_nested():
                db.execute(
                    update(models.SupplierPayment)
                    .where(models.SupplierPayment.id == payment.id)
                    .values(**values)
                )
    _approve(db, payment)
    with pytest.raises(IntegrityError):
        with db.begin_nested():
            db.execute(
                update(models.SupplierPayment)
                .where(models.SupplierPayment.id == payment.id)
                .values(
                    status=SupplierPaymentStatus.RECORDED,
                    recorded_by="USER:APPROVER",
                    recorded_at=NOW,
                    bank_reference="R",
                )
            )


# --- state machine refusals ---------------------------------------------------


def test_invalid_transitions_are_refused(db: Session) -> None:
    ob = _obligation(db)
    payment = _create(db, _command([(ob, "1000")]))
    with pytest.raises(InvalidTransition, match="approved supplier payment"):
        _record(db, payment)  # DRAFT -> RECORDED
    _approve(db, payment)
    with pytest.raises(InvalidTransition, match="draft supplier payment"):
        _approve(db, payment, key="pay:approve:again")
    _record(db, payment)
    with pytest.raises(InvalidTransition, match="approved supplier payment"):
        _record(db, payment, key="pay:record:again", ref="BANK-REF-2")
    with pytest.raises(InvalidTransition, match="draft or approved"):
        cancel_supplier_payment(
            db,
            scope=TenantScope(TENANT),
            payment_id=payment.id,
            reason="oops",
            cancelled_by="user:x",
            idempotency_key="pay:cancel:1",
            idempotency_expires_at=None,
            cancelled_at=NOW,
        )
    assert payment.status == SupplierPaymentStatus.RECORDED


@pytest.mark.parametrize("approve_first", [False, True])
def test_cancel_from_draft_or_approved_releases_the_reservation(
    db: Session, approve_first: bool
) -> None:
    ob = _obligation(db, amount="1000")
    payment = _create(db, _command([(ob, "1000")]))
    if approve_first:
        _approve(db, payment)
    cancelled = cancel_supplier_payment(
        db,
        scope=TenantScope(TENANT),
        payment_id=payment.id,
        reason="wrong supplier account",
        cancelled_by="user:anyone",
        idempotency_key="pay:cancel:1",
        idempotency_expires_at=None,
        cancelled_at=NOW,
    )
    assert cancelled.status == SupplierPaymentStatus.CANCELLED
    assert cancelled.cancel_reason == "wrong supplier account"
    with pytest.raises(InvalidTransition):
        _approve(db, payment, key="pay:approve:late")
    with pytest.raises(InvalidTransition):
        _record(db, payment)
    # The obligation can be proposed again after cancellation.
    again = _create(db, _command([(ob, "1000")], number="SPAY-0002"), key="pay:c:2")
    assert again.status == SupplierPaymentStatus.DRAFT
    assert ob.outstanding_amount == Decimal("1000")


def test_cancel_requires_a_reason(db: Session) -> None:
    ob = _obligation(db)
    payment = _create(db, _command([(ob, "1000")]))
    with pytest.raises(PayablesError, match="cancel reason is required"):
        cancel_supplier_payment(
            db,
            scope=TenantScope(TENANT),
            payment_id=payment.id,
            reason="  ",
            cancelled_by="user:x",
            idempotency_key="pay:cancel:blank",
            idempotency_expires_at=None,
            cancelled_at=NOW,
        )


# --- settlement against obligations -------------------------------------------


def test_allocation_cannot_exceed_outstanding_or_other_commitments(
    db: Session,
) -> None:
    ob = _obligation(db, amount="1000")
    with pytest.raises(InvalidAmount, match="exceeds the obligation balance"):
        _create(db, _command([(ob, "1000.01")]))
    _create(db, _command([(ob, "700")]), key="pay:c:1")
    with pytest.raises(InvalidAmount, match="already committed"):
        _create(db, _command([(ob, "400")], number="SPAY-0002"), key="pay:c:2")
    ok = _create(db, _command([(ob, "300")], number="SPAY-0003"), key="pay:c:3")
    assert ok.status == SupplierPaymentStatus.DRAFT


def test_record_refuses_if_obligation_was_settled_elsewhere_and_rolls_back(
    db: Session,
) -> None:
    first = _obligation(db, amount="500")
    second = _obligation(db, amount="500")
    payment = _create(db, _command([(first, "500"), (second, "500")]))
    _approve(db, payment)
    # Money applied by another route after approval (e.g. a credit application).
    second.outstanding_amount = Decimal("100")
    second.status = ObligationStatus.PARTIALLY_SETTLED
    db.flush()
    with pytest.raises(InvalidAmount, match="exceeds the obligation outstanding"):
        _record(db, payment)
    db.refresh(payment)
    db.refresh(first)
    assert payment.status == SupplierPaymentStatus.APPROVED
    assert payment.bank_reference is None
    assert first.outstanding_amount == Decimal("500")
    assert _count(db, models.SettlementObservation) == 0


def test_settled_obligation_cannot_be_proposed(db: Session) -> None:
    payment, ob = _recorded(db)
    with pytest.raises(InvalidTransition, match="open payment obligation"):
        _create(db, _command([(ob, "1")], number="SPAY-0002"), key="pay:c:2")


def test_wrong_supplier_and_wrong_currency_are_refused(db: Session) -> None:
    ob = _obligation(db)
    with pytest.raises(InvalidTransition, match="different supplier"):
        _create(db, _command([(ob, "10")], supplier="party:other"))
    with pytest.raises(InvalidAmount, match="currency"):
        _create(db, _command([(ob, "10")], currency="USD"), key="pay:c:2")


def test_unknown_obligation_is_refused(db: Session) -> None:
    ghost = models.PaymentObligation(id=uuid.uuid4())
    with pytest.raises(NotFound, match="payment obligation"):
        _create(db, _command([(ghost, "10")]))


# --- WHT ----------------------------------------------------------------------


def test_withholding_cannot_reach_or_exceed_the_gross(db: Session) -> None:
    ob = _obligation(db)
    for amount in ("1000", "1500"):
        with pytest.raises(InvalidAmount, match="less than the gross"):
            _create(
                db,
                _command(
                    [(ob, "1000")],
                    wht=(SupplierPaymentWithholdingInput(WHT, Decimal(amount)),),
                ),
                key=f"pay:c:{amount}",
            )
    with pytest.raises(InvalidAmount, match="positive"):
        SupplierPaymentWithholdingInput(WHT, Decimal("0")).payload()


def test_withholding_is_optional_and_may_have_several_lines(db: Session) -> None:
    ob = _obligation(db)
    payment = _create(
        db,
        _command(
            [(ob, "1000")],
            wht=(
                SupplierPaymentWithholdingInput("account:2300", Decimal("30")),
                SupplierPaymentWithholdingInput("account:2301", Decimal("20")),
            ),
        ),
    )
    assert payment.wht_amount == Decimal("50")
    assert payment.net_amount == Decimal("950")
    _approve(db, payment)
    _record(db, payment)
    credits = {
        e.account_ref: e.credit
        for e in build_payment_accounting_consequence(
            db, tenant_id=TENANT, payment_id=payment.id
        ).entries
        if e.credit
    }
    assert credits == {
        CASH: Decimal("950"),
        "account:2300": Decimal("30"),
        "account:2301": Decimal("20"),
    }


# --- input validation ---------------------------------------------------------


def test_payment_requires_allocations_unique_obligations_and_positive_amounts(
    db: Session,
) -> None:
    ob = _obligation(db)
    with pytest.raises(InvalidAmount, match="at least one allocation"):
        _create(db, _command([]))
    with pytest.raises(InvalidAmount, match="once per payment"):
        _create(db, _command([(ob, "10"), (ob, "20")]), key="pay:c:2")
    with pytest.raises(InvalidAmount, match="positive"):
        _create(db, _command([(ob, "0")]), key="pay:c:3")
    with pytest.raises(PayablesError, match="prepared by is required"):
        _create(db, _command([(ob, "10")], prepared_by=" "), key="pay:c:4")
    assert _count(db, models.SupplierPayment) == 0


# --- idempotency --------------------------------------------------------------


def test_create_is_idempotent_by_key_and_by_number(db: Session) -> None:
    ob = _obligation(db)
    command = _command([(ob, "1000")])
    first = _create(db, command)
    assert _create(db, command).id == first.id  # same key replays
    assert _create(db, command, key="pay:create:other").id == first.id  # same number
    assert _count(db, models.SupplierPayment) == 1
    with pytest.raises(IdempotencyConflict):
        _create(db, _command([(ob, "900")]))  # same key, different request
    with pytest.raises(Conflict, match="different payment"):
        _create(db, _command([(ob, "900")]), key="pay:create:third")


def test_approve_and_record_replay_do_not_double_apply(db: Session) -> None:
    ob = _obligation(db)
    payment = _create(db, _command([(ob, "1000")]))
    _approve(db, payment)
    _approve(db, payment)  # same key + same facts: replay
    with pytest.raises(IdempotencyConflict):
        _approve(db, payment, by="user:someone-else")  # same key, new facts
    _record(db, payment)
    _record(db, payment)  # replay
    assert _count(db, models.SettlementObservation) == 1
    assert _count(db, models.LiabilityEvent) == 2  # recognised + one settlement
    assert supplier_liability_balance(
        db, tenant_id=TENANT, supplier_ref=SUPPLIER, currency_code="NGN"
    ) == Decimal("0")


def test_one_bank_reference_records_one_payment(db: Session) -> None:
    first, _ = _recorded(db)
    ob = _obligation(db, amount="200")
    second = _create(db, _command([(ob, "200")], number="SPAY-0002"), key="pay:c:2")
    _approve(db, second, key="pay:a:2")
    with pytest.raises(Conflict, match="bank reference was already recorded"):
        _record(db, second, key="pay:r:2", ref="BANK-REF-1")
    assert second.status == SupplierPaymentStatus.APPROVED
    assert ob.outstanding_amount == Decimal("200")
    _record(db, second, key="pay:r:3", ref="BANK-REF-2")
    assert second.status == SupplierPaymentStatus.RECORDED
    assert first.status == SupplierPaymentStatus.RECORDED


# --- tenant scoping -----------------------------------------------------------


def test_other_tenant_cannot_see_or_drive_a_payment(db: Session) -> None:
    payment, ob = _recorded(db)
    other_scope = TenantScope(OTHER)
    with pytest.raises(NotFound, match="supplier payment"):
        build_payment_accounting_consequence(db, tenant_id=OTHER, payment_id=payment.id)
    ob2 = _obligation(db, tenant=OTHER, amount="100")
    draft = create_supplier_payment(
        db,
        scope=other_scope,
        command=_command([(ob2, "100")]),
        idempotency_key="pay:create:1",  # same key and number as TENANT's: no clash
        idempotency_expires_at=None,
        prepared_at=NOW,
    )
    assert draft.tenant_id == OTHER and draft.id != payment.id
    with pytest.raises(NotFound, match="supplier payment"):
        _approve(db, payment, tenant=OTHER, key="x")
    with pytest.raises(NotFound, match="payment obligation"):
        create_supplier_payment(
            db,
            scope=other_scope,
            command=_command([(ob, "10")], number="SPAY-0009"),
            idempotency_key="pay:create:9",
            idempotency_expires_at=None,
            prepared_at=NOW,
        )
