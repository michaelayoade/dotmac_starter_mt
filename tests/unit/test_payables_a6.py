"""0.1.0a6: deferred-VAT release, WHT guards, historical credit, zero-line entries."""

# ruff: noqa: F811

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_payables import models
from dotmac_payables.contracts import (
    AccountingEntry,
    AccountingReceiptInput,
    ApplyCredit,
    Conflict,
    CreateCreditNote,
    CreateSupplierInvoice,
    CreditNoteStatus,
    DeclareTaxDeferral,
    HistoricalSettledInvoiceCommand,
    HistoricalSettlement,
    HistoricalSupplierCreditCommand,
    HistoricalSupplierCreditEvidence,
    HistoricalTaxDeferralCommand,
    HistoricalTaxDeferralEvidence,
    HistoricalTaxDeferralRelease,
    HistoricalUnpaidInvoiceCommand,
    HistoricalUnpaidInvoiceEvidence,
    InvalidAmount,
    InvalidTransition,
    InvoiceStatus,
    ObligationSchedule,
    ObligationStatus,
    PayableLineInput,
    PayablesError,
    RecognizeApprovedInvoice,
    SettlementObservationInput,
    SupplierPaymentStatus,
    SupplierPaymentWithholdingInput,
    deferred_tax_release,
)
from dotmac_payables.service import (
    _check_consequence_entries,
    admit_historical_settled_invoice,
    admit_historical_supplier_credit,
    admit_historical_tax_deferral,
    admit_historical_unpaid_invoice,
    apply_credit_note,
    approve_credit_note,
    approve_invoice,
    build_credit_accounting_consequence,
    build_credit_application_accounting_consequence,
    build_invoice_accounting_consequence,
    build_payment_accounting_consequence,
    build_payment_reversal_accounting_consequence,
    create_credit_note,
    create_invoice,
    credit_application_tax_releases,
    declare_tax_deferral,
    invoice_deferred_tax_position,
    recognize_approved_invoice,
    record_accounting_receipt,
    record_settlement,
    reverse_supplier_payment,
    submit_credit_note,
    submit_invoice,
    supplier_liability_balance,
)
from sqlalchemy import select
from sqlalchemy.orm import Session, scoped_session

from tests.unit.test_payables_a5 import (
    _rec_evidence,
    _RecVerifier,
    _settled_case,
)
from tests.unit.test_payables_supplier_payments import (  # noqa: F401
    CASH,
    LIABILITY,
    NOW,
    OTHER,
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

DEFERRAL = "account:1455"
RELEASE = "account:1440"
EXPENSE = "account:5100"
LATER = NOW + timedelta(days=3)
SCOPE = TenantScope(TENANT)
_seq = {"n": 500}


def _n() -> int:
    _seq["n"] += 1
    return _seq["n"]


# --- helpers ------------------------------------------------------------------


def _declare(
    db: Session,
    *,
    deferral: str = DEFERRAL,
    release: str = RELEASE,
    minor: int = 2,
    key: str | None = None,
    by: str = "user:finance",
    ref: str = "decision:d-9",
):
    return declare_tax_deferral(
        db,
        scope=SCOPE,
        command=DeclareTaxDeferral(deferral, release, minor, by, ref),
        idempotency_key=key or f"decl:{_n()}",
        idempotency_expires_at=None,
        declared_at=NOW,
    )


def _invoice(
    db: Session,
    *,
    lines: list[tuple[str, str, str | None]] | None = None,
    schedule: list[str] | None = None,
    receipt: bool = True,
    supplier: str = SUPPLIER,
):
    """An approved invoice. ``lines`` are (net, tax, tax_account); default 1000+75."""
    n = _n()
    lines = lines or [("1000", "75", DEFERRAL)]
    total = sum((Decimal(a) + Decimal(b) for a, b, _ in lines), Decimal("0"))
    schedule = schedule or [str(total)]
    invoice = create_invoice(
        db,
        scope=SCOPE,
        command=CreateSupplierInvoice(
            number=f"PINV-A6-{n}",
            supplier_ref=supplier,
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number=f"A6-{n}",
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref=LIABILITY,
            lines=tuple(
                PayableLineInput(
                    description=f"Line {i}",
                    quantity=Decimal("1"),
                    unit_price=Decimal(net),
                    posting_account_ref=EXPENSE,
                    tax_amount=Decimal(tax),
                    tax_account_ref=tax_account,
                )
                for i, (net, tax, tax_account) in enumerate(lines, start=1)
            ),
            schedule=tuple(
                ObligationSchedule(
                    date(2026, 8, 31) + timedelta(days=30 * i), Decimal(a)
                )
                for i, a in enumerate(schedule)
            ),
        ),
        idempotency_key=f"a6:inv:create:{n}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    submit_invoice(
        db, scope=SCOPE, invoice_id=invoice.id, submitted_by="user:ap", submitted_at=NOW
    )
    approve_invoice(
        db,
        scope=SCOPE,
        invoice_id=invoice.id,
        approval_reference=f"approval:a6:{n}",
        approved_by="user:controller",
        idempotency_key=f"a6:inv:approve:{n}",
        idempotency_expires_at=None,
        approved_at=NOW,
    )
    if receipt:
        db.add(
            models.AccountingReceipt(
                tenant_id=TENANT,
                document_kind="supplier_invoice",
                document_id=invoice.id,
                consequence_fingerprint="e" * 64,
                accounting_reference=f"journal:a6:{n}",
                accounting_evidence_fingerprint="f" * 64,
                recorded_at=NOW,
            )
        )
        db.flush()
    obligations = list(
        db.scalars(
            select(models.PaymentObligation)
            .where(models.PaymentObligation.invoice_id == invoice.id)
            .order_by(models.PaymentObligation.sequence)
        )
    )
    return invoice, obligations


def _pay(db: Session, pairs, *, wht=(), cash: str = CASH):
    n = _n()
    command = replace(
        _command(pairs, number=f"SPAY-A6-{n}", wht=wht), cash_account_ref=cash
    )
    payment = _create(db, command, key=f"a6:pc:{n}")
    _approve(db, payment, key=f"a6:pa:{n}")
    _record(db, payment, ref=f"BANK-A6-{n}", key=f"a6:pr:{n}")
    return payment


def _releases(db: Session, payment=None) -> list[models.SupplierPaymentTaxRelease]:
    stmt = select(models.SupplierPaymentTaxRelease).order_by(
        models.SupplierPaymentTaxRelease.created_at,
        models.SupplierPaymentTaxRelease.line_number,
    )
    if payment is not None:
        stmt = stmt.where(models.SupplierPaymentTaxRelease.payment_id == payment.id)
    return list(db.scalars(stmt))


def _consequence(db: Session, payment):
    return build_payment_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    )


def _shape(consequence) -> list[tuple[str, Decimal, Decimal]]:
    return [(e.account_ref, e.debit, e.credit) for e in consequence.entries]


def _receipt(db: Session, payment, kind="supplier_payment", build=None):
    build = build or build_payment_accounting_consequence
    consequence = build(db, tenant_id=TENANT, payment_id=payment.id)
    record_accounting_receipt(
        db,
        scope=SCOPE,
        command=AccountingReceiptInput(
            kind, payment.id, consequence.fingerprint, f"journal:{kind}", "a" * 64
        ),
        idempotency_key=f"a6:receipt:{kind}:{payment.id}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )


def _position(db: Session, invoice):
    (position,) = invoice_deferred_tax_position(db, TENANT, invoice.id)
    return position


# --- pure formula and the refusal code convention -----------------------------


def test_payables_error_code_is_optional_and_existing_raises_are_unchanged() -> None:
    assert PayablesError("plain").code is None
    assert InvalidAmount("amount", code="x").code == "x"
    with pytest.raises(PayablesError) as caught:
        DeclareTaxDeferral("a", "a", 2, "u", "e").validate()
    assert caught.value.code == "tax_deferral_same_account"
    assert "(tax_deferral_same_account)" in str(caught.value)


@pytest.mark.parametrize(
    ("remaining", "allocated", "outstanding", "units", "expected"),
    [
        ("75", "1075", "1075", 2, "75"),  # final payment: the exact remainder
        ("75", "333.33", "1075", 2, "23.26"),  # 23.2558... rounds half up
        ("75.123456", "50", "100", 2, "37.56"),  # 37.561728 -> 37.56
        ("75", "0.01", "1075", 2, "0.00"),  # a tiny share rounds to zero
        ("0.05", "50", "100", 2, "0.03"),  # 0.025 half up
        ("10", "1", "3", 0, "3"),
        ("10", "3", "3", 0, "10"),
    ],
)
def test_deferred_tax_release_formula(
    remaining: str, allocated: str, outstanding: str, units: int, expected: str
) -> None:
    got = deferred_tax_release(
        Decimal(remaining), Decimal(allocated), Decimal(outstanding), units
    )
    assert got == Decimal(expected)


def test_deferred_tax_release_refusals() -> None:
    with pytest.raises(InvalidAmount) as caught:
        deferred_tax_release(Decimal("-1"), Decimal("1"), Decimal("2"), 2)
    assert caught.value.code == "deferred_tax_position_negative"
    for allocated, outstanding in (("0", "5"), ("6", "5"), ("1", "0")):
        with pytest.raises(InvalidAmount):
            deferred_tax_release(
                Decimal("1"), Decimal(allocated), Decimal(outstanding), 2
            )


# --- the policy ---------------------------------------------------------------


def test_policy_declaration_replays_and_is_immutable(db: Session) -> None:
    first = _declare(db, key="decl:1")
    again = _declare(db, key="decl:2")  # identical facts, another key
    assert again.id == first.id
    assert _count(db, models.TaxDeferralPolicy) == 1
    for changed in (
        {"release": "account:1441"},
        {"minor": 3},
        {"by": "user:other"},
        {"ref": "decision:other"},
    ):
        with pytest.raises(Conflict) as caught:
            _declare(db, **changed)
        assert caught.value.code == "tax_deferral_already_declared"
    with pytest.raises(IdempotencyConflict):
        _declare(db, key="decl:1", minor=3)
    assert _count(db, models.TaxDeferralPolicy) == 1


def test_policy_refuses_same_account_and_chains(db: Session) -> None:
    with pytest.raises(Conflict) as same:
        _declare(db, deferral=DEFERRAL, release=DEFERRAL)
    assert same.value.code == "tax_deferral_same_account"
    _declare(db)
    with pytest.raises(Conflict) as chain:
        # the release account of one policy cannot itself be deferred
        _declare(db, deferral=RELEASE, release="account:1500")
    assert chain.value.code == "tax_deferral_chain"
    with pytest.raises(Conflict) as reverse_chain:
        # nor can a new release account be another policy's deferral account
        _declare(db, deferral="account:1456", release=DEFERRAL)
    assert reverse_chain.value.code == "tax_deferral_chain"
    assert _count(db, models.TaxDeferralPolicy) == 1


@pytest.mark.parametrize("units", [-1, 7, 2.0])
def test_policy_minor_units_are_zero_to_six(db: Session, units) -> None:
    with pytest.raises(InvalidAmount):
        _declare(db, minor=units)


def test_policy_is_tenant_scoped(db: Session) -> None:
    _declare(db)
    declare_tax_deferral(
        db,
        scope=TenantScope(OTHER),
        command=DeclareTaxDeferral(DEFERRAL, RELEASE, 2, "user:f", "ref"),
        idempotency_key="decl:other",
        idempotency_expires_at=None,
        declared_at=NOW,
    )
    assert _count(db, models.TaxDeferralPolicy) == 2


# --- release at record_supplier_payment ---------------------------------------


def test_a_single_full_payment_releases_the_deferred_tax_exactly(db: Session) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    payment = _pay(db, [(ob, "1075")])
    (row,) = _releases(db, payment)
    assert (row.deferral_account_ref, row.release_account_ref, row.amount) == (
        DEFERRAL,
        RELEASE,
        Decimal("75"),
    )
    assert row.invoice_id == invoice.id
    assert _shape(_consequence(db, payment)) == [
        (LIABILITY, Decimal("1075"), Decimal("0")),
        (CASH, Decimal("0"), Decimal("1075")),
        (RELEASE, Decimal("75"), Decimal("0")),
        (DEFERRAL, Decimal("0"), Decimal("75")),
    ]
    position = _position(db, invoice)
    assert position.remaining == Decimal(
        "0"
    ) and position.released_by_payments == Decimal("75")


def test_partial_payments_sum_to_the_deferred_tax_exactly(db: Session) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    first = _pay(db, [(ob, "333.33")])
    second = _pay(db, [(ob, "741.67")])
    assert [r.amount for r in _releases(db, first)] == [Decimal("23.26")]
    assert [r.amount for r in _releases(db, second)] == [Decimal("51.74")]
    assert sum(r.amount for r in _releases(db)) == Decimal("75")
    assert _position(db, invoice).remaining == Decimal("0")
    assert ob.status == ObligationStatus.SETTLED


def test_a_three_way_split_sums_to_the_deferred_tax_exactly(db: Session) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    for amount in ("300", "400", "375"):
        _pay(db, [(ob, amount)])
    amounts = [r.amount for r in _releases(db)]
    assert amounts == [Decimal("20.93"), Decimal("27.91"), Decimal("26.16")]
    assert sum(amounts) == Decimal("75")


def test_a_multi_invoice_payment_releases_per_invoice(db: Session) -> None:
    _declare(db)
    one, (ob_one,) = _invoice(db)
    two, (ob_two,) = _invoice(db, lines=[("2000", "150", DEFERRAL)])
    payment = _pay(db, [(ob_one, "1075"), (ob_two, "2150")])
    rows = _releases(db, payment)
    assert {r.invoice_id: r.amount for r in rows} == {
        one.id: Decimal("75"),
        two.id: Decimal("150"),
    }
    assert sorted(r.line_number for r in rows) == [1, 2]
    consequence = _consequence(db, payment)
    assert sum(e.debit for e in consequence.entries) == sum(
        e.credit for e in consequence.entries
    )


def test_a_multi_obligation_invoice_releases_the_rest_only_at_the_end(
    db: Session,
) -> None:
    _declare(db)
    invoice, (first, second) = _invoice(db, schedule=["600", "475"])
    one = _pay(db, [(first, "600")])
    assert [r.amount for r in _releases(db, one)] == [Decimal("41.86")]
    assert _position(db, invoice).remaining == Decimal("33.14")
    two = _pay(db, [(second, "475")])
    assert [r.amount for r in _releases(db, two)] == [Decimal("33.14")]
    assert _position(db, invoice).remaining == Decimal("0")


def test_one_payment_across_obligations_of_one_invoice_is_final_only_when_all_paid(
    db: Session,
) -> None:
    _declare(db)
    invoice, (first, second) = _invoice(db, schedule=["600", "475"])
    payment = _pay(db, [(first, "600"), (second, "475")])
    (row,) = _releases(db, payment)
    assert row.amount == Decimal("75")


def test_folded_and_immediate_tax_lines_release_nothing(db: Session) -> None:
    _declare(db)
    # folded: the tax account is the line's own posting account (A2/A3/B2);
    # immediate: the tax is on the release account itself (not a deferral account).
    _, (folded,) = _invoice(db, lines=[("1000", "75", EXPENSE)])
    _, (immediate,) = _invoice(db, lines=[("1000", "75", RELEASE)])
    for ob in (folded, immediate):
        payment = _pay(db, [(ob, "1075")])
        assert _releases(db, payment) == []
        assert _shape(_consequence(db, payment)) == [
            (LIABILITY, Decimal("1075"), Decimal("0")),
            (CASH, Decimal("0"), Decimal("1075")),
        ]


def test_a_mixed_folded_and_deferred_invoice_releases_only_the_deferred_part(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(
        db, lines=[("1000", "75", DEFERRAL), ("500", "37.5", EXPENSE)]
    )
    position = _position(db, invoice)
    assert position.deferred == Decimal("75")
    payment = _pay(db, [(ob, "1612.5")])
    assert [r.amount for r in _releases(db, payment)] == [Decimal("75")]


def test_without_a_policy_nothing_is_released_and_the_consequence_is_a5(
    db: Session,
) -> None:
    _, (ob,) = _invoice(db)
    payment = _pay(db, [(ob, "1075")])
    assert _releases(db) == []
    assert _shape(_consequence(db, payment)) == [
        (LIABILITY, Decimal("1075"), Decimal("0")),
        (CASH, Decimal("0"), Decimal("1075")),
    ]


def test_rows_are_frozen_the_consequence_ignores_later_policy(db: Session) -> None:
    _declare(db)
    _, (ob,) = _invoice(db)
    payment = _pay(db, [(ob, "1075")])
    before = _consequence(db, payment).fingerprint
    # A second invoice declared deferral later does not change the first.
    _declare(db, deferral="account:1456", release="account:1441")
    assert _consequence(db, payment).fingerprint == before


def test_release_replay_writes_no_rows(db: Session) -> None:
    _declare(db)
    _, (ob,) = _invoice(db)
    payment = _create(db, _command([(ob, "1075")], number="SPAY-REPLAY"), key="rp:c")
    _approve(db, payment, key="rp:a")
    _record(db, payment, ref="BANK-REPLAY", key="rp:r")
    assert _count(db, models.SupplierPaymentTaxRelease) == 1
    # The same key and facts replay the recorded result without running again.
    _record(db, payment, ref="BANK-REPLAY", key="rp:r")
    assert _count(db, models.SupplierPaymentTaxRelease) == 1
    assert _count(db, models.SettlementObservation) == 1


# --- the credit-note hook -----------------------------------------------------


def _credit(
    db: Session,
    *,
    invoice=None,
    net: str = "400",
    tax: str = "30",
    account: str | None = DEFERRAL,
    approve: bool = True,
    lines: list[tuple[str, str, str | None]] | None = None,
):
    """A submitted (and by default approved) credit note, by default one line."""
    n = _n()
    lines = lines or [(net, tax, account)]
    credit = create_credit_note(
        db,
        scope=SCOPE,
        command=CreateCreditNote(
            number=f"CN-A6-{n}",
            supplier_ref=SUPPLIER,
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number=f"CNDOC-A6-{n}",
            credit_date=date(2026, 8, 15),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref=LIABILITY,
            lines=tuple(
                PayableLineInput(
                    description=f"Credit {i}",
                    quantity=Decimal("1"),
                    unit_price=Decimal(line_net),
                    posting_account_ref=EXPENSE,
                    tax_amount=Decimal(line_tax),
                    tax_account_ref=line_account,
                )
                for i, (line_net, line_tax, line_account) in enumerate(lines, start=1)
            ),
            original_invoice_id=invoice.id if invoice is not None else None,
        ),
        idempotency_key=f"a6:cn:create:{n}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    submit_credit_note(
        db,
        scope=SCOPE,
        credit_note_id=credit.id,
        submitted_by="user:ap",
        submitted_at=NOW,
    )
    if approve:
        approve_credit_note(
            db,
            scope=SCOPE,
            credit_note_id=credit.id,
            approval_reference=f"approval:cn:{n}",
            approved_by="user:controller",
            idempotency_key=f"a6:cn:approve:{n}",
            idempotency_expires_at=None,
            approved_at=NOW,
        )
    return credit


def test_a_credit_with_deferral_tax_reduces_what_remains_to_release(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _credit(db, invoice=invoice)  # 400 + 30 deferral tax
    position = _position(db, invoice)
    assert (position.deferred, position.credited, position.remaining) == (
        Decimal("75"),
        Decimal("30"),
        Decimal("45"),
    )
    # Not applied: the obligation is still 1075, so a full payment releases R.
    payment = _pay(db, [(ob, "1075")])
    assert [r.amount for r in _releases(db, payment)] == [Decimal("45")]


def test_an_applied_credit_lowers_the_balance_and_the_final_payment_releases_r(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    credit = _credit(db, invoice=invoice)
    apply_credit_note(
        db,
        scope=SCOPE,
        command=ApplyCredit(credit.id, ob.id, Decimal("430"), "user:ap"),
        idempotency_key="a6:apply",
        idempotency_expires_at=None,
        applied_at=NOW,
    )
    assert ob.outstanding_amount == Decimal("645")
    payment = _pay(db, [(ob, "645")])
    assert [r.amount for r in _releases(db, payment)] == [Decimal("45")]
    assert _position(db, invoice).remaining == Decimal("0")


def test_a_credit_over_what_remains_is_refused(db: Session) -> None:
    _declare(db)
    invoice, _ = _invoice(db)
    with pytest.raises(InvalidAmount) as caught:
        _credit(db, invoice=invoice, net="100", tax="80")
    assert caught.value.code == "credit_tax_exceeds_invoice_tax"
    _credit(db, invoice=invoice, net="100", tax="45")
    with pytest.raises(InvalidAmount) as again:
        _credit(db, invoice=invoice, net="100", tax="31")  # only 30 of the tax is left
    assert again.value.code == "credit_tax_exceeds_invoice_tax"


def test_a_credit_with_deferral_tax_needs_its_invoice(db: Session) -> None:
    _declare(db)
    with pytest.raises(InvalidTransition) as caught:
        _credit(db, invoice=None)
    assert caught.value.code == "deferred_tax_credit_requires_invoice"


def test_a_credit_for_an_already_paid_invoice_reverses_the_release_account(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _pay(db, [(ob, "1075")])
    credit = _credit(db, invoice=invoice, net="10", tax="1")
    (split,) = db.scalars(select(models.CreditNoteTaxSplit)).all()
    assert (split.deferred_amount, split.released_amount) == (
        Decimal("0"),
        Decimal("1"),
    )
    assert _shape(
        build_credit_accounting_consequence(
            db, tenant_id=TENANT, credit_note_id=credit.id
        )
    ) == [
        (LIABILITY, Decimal("11"), Decimal("0")),
        (EXPENSE, Decimal("0"), Decimal("10")),
        (RELEASE, Decimal("0"), Decimal("1")),
    ]


def test_credits_without_deferral_tax_are_unaffected_by_the_hook(db: Session) -> None:
    _declare(db)
    credit = _credit(db, invoice=None, tax="0", account=None)
    assert credit.status == CreditNoteStatus.APPROVED


# --- reversal ------------------------------------------------------------------


def test_reversal_mirrors_the_rows_restores_r_and_a_repayment_releases_again(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    payment = _pay(db, [(ob, "1075")])
    _receipt(db, payment)
    reverse_supplier_payment(
        db,
        scope=SCOPE,
        payment_id=payment.id,
        reason="bounced",
        reversal_date=date(2026, 9, 5),
        reversed_by="user:reverser",
        idempotency_key="a6:rev",
        idempotency_expires_at=None,
        reversed_at=LATER,
    )
    assert payment.status == SupplierPaymentStatus.REVERSED
    reversal = build_payment_reversal_accounting_consequence(
        db, tenant_id=TENANT, payment_id=payment.id
    )
    assert _shape(reversal) == [
        (CASH, Decimal("1075"), Decimal("0")),
        (LIABILITY, Decimal("0"), Decimal("1075")),
        (DEFERRAL, Decimal("75"), Decimal("0")),
        (RELEASE, Decimal("0"), Decimal("75")),
    ]
    # The rows stay (append-only), but the REVERSED payment no longer counts.
    assert len(_releases(db, payment)) == 1
    assert _position(db, invoice).remaining == Decimal("75")
    # The original consequence stays rebuildable, rows included.
    assert (RELEASE, Decimal("75"), Decimal("0")) in _shape(_consequence(db, payment))
    again = _pay(db, [(ob, "1075")])
    assert [r.amount for r in _releases(db, again)] == [Decimal("75")]
    assert _position(db, invoice).remaining == Decimal("0")


def test_reversal_with_wht_and_release_mirrors_every_line(db: Session) -> None:
    _declare(db)
    _, (ob,) = _invoice(db)
    wht = (SupplierPaymentWithholdingInput(WHT, Decimal("50"), "WHT 5%"),)
    payment = _pay(db, [(ob, "1075")], wht=wht)
    _receipt(db, payment)
    original = _shape(_consequence(db, payment))
    reverse_supplier_payment(
        db,
        scope=SCOPE,
        payment_id=payment.id,
        reason="bounced",
        reversal_date=date(2026, 9, 5),
        reversed_by="user:reverser",
        idempotency_key="a6:rev2",
        idempotency_expires_at=None,
        reversed_at=LATER,
    )
    reversal = _shape(
        build_payment_reversal_accounting_consequence(
            db, tenant_id=TENANT, payment_id=payment.id
        )
    )
    # Every original (account, debit, credit) appears with the sides swapped.
    assert sorted((a, c, d) for a, d, c in original) == sorted(reversal)


# --- settled and unpaid history ------------------------------------------------


def _settled_with_deferral(db: Session):
    invoice, evidence = _settled_case(db)
    line = db.scalars(select(models.SupplierInvoiceLine)).one()
    line.tax_account_ref = DEFERRAL
    db.flush()
    admit_historical_settled_invoice(
        db,
        scope=SCOPE,
        command=HistoricalSettledInvoiceCommand(
            evidence.source_owner,
            evidence.source_invoice_id,
            evidence.invoice_id,
            evidence.source_snapshot_sha256,
            evidence.fingerprint(),
        ),
        verifier=_RecVerifier(evidence),
        idempotency_key="a6:settled",
        idempotency_expires_at=None,
        admitted_at=NOW + timedelta(days=30),
    )
    (ob,) = db.scalars(select(models.PaymentObligation)).all()
    return invoice, evidence, ob


def _deferral_evidence(invoice, source_invoice_id, *, released="25.00", **over):
    released_amount = Decimal(released)
    releases = (
        (
            HistoricalTaxDeferralRelease(
                "opening-schedule:row-7", released_amount, "cv1:" + "3" * 64
            ),
        )
        if released_amount > 0
        else ()
    )
    values = {
        "tenant_id": TENANT,
        "source_owner": "legacy",
        "source_invoice_id": source_invoice_id,
        "source_snapshot_sha256": "a" * 64,
        "invoice_id": invoice.id,
        "deferral_account_ref": DEFERRAL,
        "deferred_tax_total": Decimal("75.00"),
        "releases": releases,
        "released_before_admission": released_amount,
        "remaining": Decimal("75.00") - released_amount,
        "accounting_evidence_fingerprint": "cv1:" + "4" * 64,
        "facts_fingerprint": "cv1:" + "5" * 64,
    }
    values.update(over)
    return HistoricalTaxDeferralEvidence(**values)


def _deferral_command(evidence) -> HistoricalTaxDeferralCommand:
    return HistoricalTaxDeferralCommand(
        evidence.source_owner,
        evidence.source_invoice_id,
        evidence.invoice_id,
        evidence.deferral_account_ref,
        evidence.source_snapshot_sha256,
        evidence.fingerprint(),
    )


def _admit_deferral(db, evidence, *, key="a6:def", command=None, verifier=None):
    return admit_historical_tax_deferral(
        db,
        scope=SCOPE,
        command=command or _deferral_command(evidence),
        verifier=verifier or _RecVerifier(evidence),
        idempotency_key=key,
        idempotency_expires_at=None,
        admitted_at=NOW + timedelta(days=31),
    )


def test_settled_history_without_a_deferral_position_refuses_the_payment(
    db: Session,
) -> None:
    _declare(db)
    _, _, ob = _settled_with_deferral(db)
    payment = _create(db, _command([(ob, "575")]), key="a6:hc")
    _approve(db, payment, key="a6:ha")
    with pytest.raises(InvalidTransition) as caught:
        _record(db, payment, key="a6:hr", ref="BANK-H")
    assert caught.value.code == "historical_deferral_unproven"
    assert "(historical_deferral_unproven)" in str(caught.value)
    assert payment.status == SupplierPaymentStatus.APPROVED
    assert _releases(db) == []
    db.refresh(ob)
    assert ob.outstanding_amount == Decimal("575")


def test_settled_history_with_its_position_releases_the_rest_only(
    db: Session,
) -> None:
    _declare(db)
    invoice, evidence, ob = _settled_with_deferral(db)
    row = _admit_deferral(db, _deferral_evidence(invoice, evidence.source_invoice_id))
    assert (row.deferred_tax_total, row.released_before_admission, row.remaining) == (
        Decimal("75"),
        Decimal("25"),
        Decimal("50"),
    )
    assert row.evidence["releases"][0]["evidence_ref"] == "opening-schedule:row-7"
    position = _position(db, invoice)
    assert (position.released_before_admission, position.remaining) == (
        Decimal("25"),
        Decimal("50"),
    )
    payment = _pay(db, [(ob, "575")])  # the whole residual: the exact remainder
    assert [r.amount for r in _releases(db, payment)] == [Decimal("50")]


def test_settled_history_with_nothing_released_before_admission(db: Session) -> None:
    _declare(db)
    invoice, evidence, ob = _settled_with_deferral(db)
    _admit_deferral(
        db, _deferral_evidence(invoice, evidence.source_invoice_id, released="0.00")
    )
    payment = _pay(db, [(ob, "575")])
    assert [r.amount for r in _releases(db, payment)] == [Decimal("75")]


def test_unpaid_history_releases_the_whole_deferral(db: Session) -> None:
    _declare(db)
    n = _n()
    # The importer path: a direct APPROVED invoice with no obligation yet.
    imported = create_invoice(
        db,
        scope=SCOPE,
        command=CreateSupplierInvoice(
            number=f"PINV-U-{n}",
            supplier_ref=SUPPLIER,
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number=f"U-{n}",
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref=LIABILITY,
            lines=(
                PayableLineInput(
                    "Fibre",
                    Decimal("1"),
                    Decimal("1000"),
                    EXPENSE,
                    Decimal("75"),
                    DEFERRAL,
                ),
            ),
            schedule=(ObligationSchedule(date(2026, 8, 31), Decimal("1075")),),
        ),
        idempotency_key=f"a6:u:{n}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    imported.status = InvoiceStatus.APPROVED
    db.flush()
    names = (
        "number supplier_ref supplier_name_snapshot supplier_document_number "
        "invoice_date received_date currency_code exchange_rate subtotal tax_amount "
        "total_amount liability_account_ref procurement_ref "
        "receipt_evidence_fingerprint submitted_by submitted_at approved_by "
        "approved_at approval_reference"
    ).split()
    evidence = HistoricalUnpaidInvoiceEvidence(
        tenant_id=TENANT,
        invoice_id=imported.id,
        source_owner="legacy",
        source_invoice_id=f"unpaid:{n}",
        source_snapshot_sha256="a" * 64,
        source_status="POSTED",
        invoice_type="STANDARD",
        paid_amount=Decimal(0),
        allocation_count=0,
        withholding_tax_amount=Decimal(0),
        prepayment_amount=Decimal(0),
        due_date=date(2026, 8, 31),
        original_posted_at=NOW,
        source_journal_ref="source:journal",
        destination_journal_ref="destination:journal",
        accounting_evidence_fingerprint="cv1:" + "b" * 64,
        facts_fingerprint="cv1:" + "c" * 64,
        **{name: getattr(imported, name) for name in names},
    )
    admit_historical_unpaid_invoice(
        db,
        scope=SCOPE,
        command=HistoricalUnpaidInvoiceCommand(
            "legacy", f"unpaid:{n}", imported.id, "a" * 64, evidence.fingerprint()
        ),
        verifier=_RecVerifier(evidence),
        idempotency_key=f"a6:unpaid:{n}",
        idempotency_expires_at=None,
        admitted_at=NOW,
    )
    ob = db.scalars(
        select(models.PaymentObligation).where(
            models.PaymentObligation.invoice_id == imported.id
        )
    ).one()
    payment = _pay(db, [(ob, "1075")])
    assert [r.amount for r in _releases(db, payment)] == [Decimal("75")]


# --- item 1b: the historical deferral admission --------------------------------


def test_deferral_admission_is_idempotent_and_conflicts_on_changed_evidence(
    db: Session,
) -> None:
    invoice, evidence, _ = _settled_with_deferral(db)
    proof = _deferral_evidence(invoice, evidence.source_invoice_id)
    first = _admit_deferral(db, proof)
    again = _admit_deferral(db, proof, key="a6:def:2")
    assert again.id == first.id
    assert _count(db, models.HistoricalTaxDeferralRecord) == 1
    changed = _deferral_evidence(
        invoice,
        evidence.source_invoice_id,
        facts_fingerprint="cv1:" + "9" * 64,
    )
    with pytest.raises(Conflict, match="different permanent deferral evidence"):
        _admit_deferral(db, changed, key="a6:def:3")


def test_deferral_admission_refusals(db: Session) -> None:
    invoice, evidence, _ = _settled_with_deferral(db)
    src = evidence.source_invoice_id
    good = _deferral_evidence(invoice, src)
    # D is recomputed from the invoice's own lines.
    with pytest.raises(Conflict, match="differs from the invoice lines"):
        _admit_deferral(
            db,
            _deferral_evidence(
                invoice,
                src,
                deferred_tax_total=Decimal("80.00"),
                remaining=Decimal("55.00"),
            ),
        )
    # releases must add up to released_before_admission, and remaining to D - L.
    for bad in (
        replace(good, released_before_admission=Decimal("24.00")),
        replace(good, remaining=Decimal("49.00")),
        replace(good, releases=()),
        replace(
            good,
            released_before_admission=Decimal("80.00"),
            remaining=Decimal("-5.00"),
            releases=(
                HistoricalTaxDeferralRelease("x", Decimal("80.00"), "cv1:" + "3" * 64),
            ),
        ),
    ):
        with pytest.raises(InvalidAmount):
            _admit_deferral(db, bad)
    # two-decimal money only
    with pytest.raises(InvalidAmount):
        _admit_deferral(
            db,
            replace(
                good,
                deferred_tax_total=Decimal("75.001"),
                remaining=Decimal("50.001"),
            ),
        )
    # identity binding
    wrong = replace(good, source_invoice_id="other")
    with pytest.raises(Conflict, match="identity differs"):
        _admit_deferral(db, wrong, command=_deferral_command(good))
    with pytest.raises(Conflict, match="fingerprint differs"):
        _admit_deferral(
            db,
            replace(good, facts_fingerprint="cv1:" + "8" * 64),
            command=_deferral_command(good),
        )
    other_account = replace(good, deferral_account_ref="account:1456")
    with pytest.raises(Conflict, match="deferral account differs"):
        _admit_deferral(
            db,
            other_account,
            command=replace(
                _deferral_command(other_account), deferral_account_ref=DEFERRAL
            ),
        )
    assert _count(db, models.HistoricalTaxDeferralRecord) == 0


def test_deferral_admission_needs_a_settled_historical_invoice(db: Session) -> None:
    _declare(db)
    invoice, _ = _invoice(db)  # an ordinary invoice: no historical receipt
    with pytest.raises(InvalidTransition) as caught:
        _admit_deferral(db, _deferral_evidence(invoice, "src:ordinary"))
    assert caught.value.code == "historical_deferral_invoice_not_settled"


def test_deferral_admission_refuses_a_writing_verifier_and_a_scoped_session(
    db: Session,
) -> None:
    invoice, evidence, _ = _settled_with_deferral(db)
    proof = _deferral_evidence(invoice, evidence.source_invoice_id)

    class Writing:
        def verify(self, *, scope, command, destination):
            destination.add(
                models.TaxDeferralPolicy(
                    tenant_id=TENANT,
                    deferral_account_ref="account:9",
                    release_account_ref="account:8",
                    minor_units=2,
                    declared_by="v",
                    evidence_reference="v",
                    declared_at=NOW,
                )
            )
            return proof

    with pytest.raises(InvalidTransition, match="verifier must not write"):
        _admit_deferral(db, proof, verifier=Writing())
    db.expunge_all()
    assert _count(db, models.TaxDeferralPolicy) == 0
    assert _count(db, models.HistoricalTaxDeferralRecord) == 0
    with pytest.raises(InvalidTransition, match="plain Session"):
        admit_historical_tax_deferral(
            scoped_session(lambda: db),  # type: ignore[arg-type]
            scope=SCOPE,
            command=_deferral_command(proof),
            verifier=_RecVerifier(proof),
            idempotency_key="a6:scoped",
            idempotency_expires_at=None,
            admitted_at=NOW,
        )
    with pytest.raises(InvalidTransition, match="requires a verifier"):
        admit_historical_tax_deferral(
            db,
            scope=SCOPE,
            command=_deferral_command(proof),
            verifier=None,  # type: ignore[arg-type]
            idempotency_key="a6:noverifier",
            idempotency_expires_at=None,
            admitted_at=NOW,
        )


# --- item 2: WHT guards ---------------------------------------------------------


@pytest.mark.parametrize("account", [CASH, LIABILITY, DEFERRAL, RELEASE])
def test_withholding_account_conflicts_are_refused_at_creation(
    db: Session, account: str
) -> None:
    _declare(db)
    _, (ob,) = _invoice(db)
    wht = (SupplierPaymentWithholdingInput(account, Decimal("50"), "WHT"),)
    with pytest.raises(InvalidTransition) as caught:
        _create(db, _command([(ob, "1075")], wht=wht), key=f"a6:w:{account}")
    assert caught.value.code == "withholding_account_conflict"
    assert _count(db, models.SupplierPayment) == 0


def test_the_cash_account_is_checked_against_the_payments_own_cash(
    db: Session,
) -> None:
    _, (ob,) = _invoice(db)
    wht = (SupplierPaymentWithholdingInput("account:1011", Decimal("50"), "WHT"),)
    command = replace(
        _command([(ob, "1075")], wht=wht), cash_account_ref="account:1011"
    )
    with pytest.raises(InvalidTransition) as caught:
        _create(db, command, key="a6:w:cash")
    assert caught.value.code == "withholding_account_conflict"


def test_a_deferral_declared_after_the_draft_is_caught_at_approval(
    db: Session,
) -> None:
    _, (ob,) = _invoice(db)
    wht = (SupplierPaymentWithholdingInput("account:1456", Decimal("50"), "WHT"),)
    payment = _create(db, _command([(ob, "1075")], wht=wht), key="a6:w:late")
    _declare(db, deferral="account:1456", release="account:1441")
    with pytest.raises(InvalidTransition) as caught:
        _approve(db, payment, key="a6:w:late:a")
    assert caught.value.code == "withholding_account_conflict"
    assert payment.status == SupplierPaymentStatus.DRAFT


def test_wht_with_release_balances_in_one_payment(db: Session) -> None:
    _declare(db)
    _, (ob,) = _invoice(db)
    wht = (SupplierPaymentWithholdingInput(WHT, Decimal("50"), "WHT 5%"),)
    payment = _pay(db, [(ob, "1075")], wht=wht)
    consequence = _consequence(db, payment)
    assert _shape(consequence) == [
        (LIABILITY, Decimal("1075"), Decimal("0")),
        (CASH, Decimal("0"), Decimal("1025")),
        (WHT, Decimal("0"), Decimal("50")),
        (RELEASE, Decimal("75"), Decimal("0")),
        (DEFERRAL, Decimal("0"), Decimal("75")),
    ]
    # Dr 2000 gross + Dr 1440 r = Cr bank net + Cr 2110 w + Cr 1455 r
    assert sum(e.debit for e in consequence.entries) == Decimal("1150")
    assert sum(e.credit for e in consequence.entries) == Decimal("1150")
    # WHT does not change the release: it is on the gross discharged.
    assert [r.amount for r in _releases(db, payment)] == [Decimal("75")]


# --- the a5 regression: byte-identical consequences ----------------------------

_FIXED = {
    "tenant": uuid.UUID("00000000-0000-0000-0000-0000000000a1"),
    "invoice": uuid.UUID("00000000-0000-0000-0000-0000000000b1"),
    "credit": uuid.UUID("00000000-0000-0000-0000-0000000000c1"),
    "obligation": uuid.UUID("00000000-0000-0000-0000-0000000000d1"),
    "payment": uuid.UUID("00000000-0000-0000-0000-0000000000e1"),
}
# Computed once with the UNMODIFIED a5 service (origin/main, payables 0.1.0a5)
# from the fixed rows built below, read back from the database (expire_all).
# A6 must not move any of them.
A5_INVOICE_FINGERPRINT = (
    "c96d4f95472db1b47bff347813df2d2fd0ed81f704920bb0a3ce6093876f338a"
)
A5_CREDIT_FINGERPRINT = (
    "c5482d5270b4089cad118f59202df459432f2225fb5899c3d77da22b787220cc"
)
A5_PAYMENT_FINGERPRINT = (
    "309b81abab224b4195c1dbde31567974a10509d76594fd7ebaf384711405b739"
)
A5_REVERSAL_FINGERPRINT = (
    "a68d7fd1d0c9b22179404fc80552ad1e1bd3e1c9733ddea243eddbf1eccbdf50"
)


def _fixed_rows(db: Session) -> None:
    from dotmac_kernel.models import Tenant

    t = _FIXED["tenant"]
    db.add(Tenant(id=t, slug="a6pin", name="A6 pin"))
    db.flush()
    db.add(
        models.SupplierInvoice(
            id=_FIXED["invoice"],
            tenant_id=t,
            number="PINV-PIN",
            supplier_ref="party:s",
            supplier_name_snapshot="S",
            supplier_document_number="D1",
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref="account:2000",
            subtotal=Decimal("1000"),
            tax_amount=Decimal("75"),
            total_amount=Decimal("1075"),
            payment_schedule=[{"due_date": "2026-08-31", "amount": "1075.000000"}],
            request_fingerprint="c" * 64,
            status=InvoiceStatus.APPROVED,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.flush()
    db.add(
        models.SupplierInvoiceLine(
            tenant_id=t,
            invoice_id=_FIXED["invoice"],
            line_number=1,
            description="Fibre",
            quantity=Decimal(1),
            unit_price=Decimal(1000),
            line_amount=Decimal(1000),
            tax_amount=Decimal(75),
            posting_account_ref="account:5100",
            tax_account_ref="account:1455",
            dimension_refs=[],
            created_at=NOW,
        )
    )
    db.add(
        models.LiabilityEvent(
            tenant_id=t,
            event_kind="invoice_recognized",
            document_kind="supplier_invoice",
            document_id=_FIXED["invoice"],
            supplier_ref="party:s",
            currency_code="NGN",
            amount=Decimal("1075"),
            source_reference="a",
            source_fingerprint="d" * 64,
            occurred_at=NOW,
        )
    )
    db.add(
        models.CreditNote(
            id=_FIXED["credit"],
            tenant_id=t,
            number="CN-PIN",
            supplier_ref="party:s",
            supplier_name_snapshot="S",
            supplier_document_number="C1",
            credit_date=date(2026, 8, 10),
            currency_code="NGN",
            exchange_rate=Decimal(1),
            liability_account_ref="account:2000",
            subtotal=Decimal("100"),
            tax_amount=Decimal("0"),
            total_amount=Decimal("100"),
            available_amount=Decimal("100"),
            request_fingerprint="e" * 64,
            status=CreditNoteStatus.APPROVED,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.flush()
    db.add(
        models.CreditNoteLine(
            tenant_id=t,
            credit_note_id=_FIXED["credit"],
            line_number=1,
            description="Rebate",
            quantity=Decimal(1),
            unit_price=Decimal(100),
            line_amount=Decimal(100),
            tax_amount=Decimal(0),
            posting_account_ref="account:5100",
            dimension_refs=[],
            created_at=NOW,
        )
    )
    db.add(
        models.PaymentObligation(
            id=_FIXED["obligation"],
            tenant_id=t,
            invoice_id=_FIXED["invoice"],
            sequence=1,
            due_date=date(2026, 8, 31),
            currency_code="NGN",
            original_amount=Decimal("1075"),
            outstanding_amount=Decimal("0"),
            status=ObligationStatus.SETTLED,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.flush()
    db.add(
        models.SupplierPayment(
            id=_FIXED["payment"],
            tenant_id=t,
            number="SPAY-PIN",
            supplier_ref="party:s",
            supplier_name_snapshot="S",
            currency_code="NGN",
            exchange_rate=Decimal(1),
            value_date=date(2026, 9, 2),
            cash_account_ref="account:1010",
            payer_instrument_ref="instr",
            gross_amount=Decimal("1075"),
            wht_amount=Decimal("50"),
            net_amount=Decimal("1025"),
            request_fingerprint="f" * 64,
            status=SupplierPaymentStatus.RECORDED,
            prepared_by="a",
            prepared_at=NOW,
            approval_reference="x",
            approved_by="b",
            approved_at=NOW,
            bank_reference="BR",
            recorded_by="c",
            recorded_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.flush()
    db.add(
        models.SupplierPaymentAllocation(
            tenant_id=t,
            payment_id=_FIXED["payment"],
            line_number=1,
            obligation_id=_FIXED["obligation"],
            amount=Decimal("1075"),
            created_at=NOW,
        )
    )
    db.add(
        models.SupplierPaymentWithholding(
            tenant_id=t,
            payment_id=_FIXED["payment"],
            line_number=1,
            account_ref="account:2110",
            amount=Decimal("50"),
            description="WHT",
            created_at=NOW,
        )
    )
    db.flush()


def test_a5_consequence_fingerprints_are_byte_identical(db: Session) -> None:
    _fixed_rows(db)
    db.expire_all()
    t = _FIXED["tenant"]
    assert (
        build_invoice_accounting_consequence(
            db, tenant_id=t, invoice_id=_FIXED["invoice"]
        ).fingerprint
        == A5_INVOICE_FINGERPRINT
    )
    assert (
        build_credit_accounting_consequence(
            db, tenant_id=t, credit_note_id=_FIXED["credit"]
        ).fingerprint
        == A5_CREDIT_FINGERPRINT
    )
    # The WHT-only payment (no release rows) is the a5 payment consequence.
    assert (
        build_payment_accounting_consequence(
            db, tenant_id=t, payment_id=_FIXED["payment"]
        ).fingerprint
        == A5_PAYMENT_FINGERPRINT
    )
    payment = db.get(models.SupplierPayment, _FIXED["payment"])
    payment.status = SupplierPaymentStatus.REVERSED
    payment.reversed_by = "d"
    payment.reversed_at = NOW
    payment.reversal_reason = "r"
    payment.reversal_date = date(2026, 9, 5)
    db.flush()
    db.expire_all()
    assert (
        build_payment_reversal_accounting_consequence(
            db, tenant_id=t, payment_id=_FIXED["payment"]
        ).fingerprint
        == A5_REVERSAL_FINGERPRINT
    )


# --- item 5: zero-value line entries -------------------------------------------


def _invoice_entries(db: Session, invoice):
    return build_invoice_accounting_consequence(
        db, tenant_id=TENANT, invoice_id=invoice.id
    )


def test_an_invoice_zero_line_has_no_entry_and_the_totals_are_unchanged(
    db: Session,
) -> None:
    invoice, _ = _invoice(db, lines=[("0", "0", None), ("1000", "75", DEFERRAL)])
    consequence = _invoice_entries(db, invoice)
    assert [(e.account_ref, e.debit, e.credit) for e in consequence.entries] == [
        (EXPENSE, Decimal("1000"), Decimal("0")),
        (DEFERRAL, Decimal("75"), Decimal("0")),
        (LIABILITY, Decimal("0"), Decimal("1075")),
    ]
    assert all(e.debit != 0 or e.credit != 0 for e in consequence.entries)
    assert sum(e.debit for e in consequence.entries) == invoice.total_amount
    assert sum(e.credit for e in consequence.entries) == invoice.total_amount


def test_a_tax_only_invoice_line_keeps_exactly_one_tax_entry(db: Session) -> None:
    invoice, _ = _invoice(db, lines=[("0", "40", DEFERRAL), ("1000", "0", None)])
    consequence = _invoice_entries(db, invoice)
    assert [(e.account_ref, e.debit, e.credit) for e in consequence.entries] == [
        (DEFERRAL, Decimal("40"), Decimal("0")),
        (EXPENSE, Decimal("1000"), Decimal("0")),
        (LIABILITY, Decimal("0"), Decimal("1040")),
    ]


def test_credit_note_lines_follow_the_same_rule(db: Session) -> None:
    n = _n()
    credit = create_credit_note(
        db,
        scope=SCOPE,
        command=CreateCreditNote(
            number=f"CN-Z-{n}",
            supplier_ref=SUPPLIER,
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number=f"CNZ-{n}",
            credit_date=date(2026, 8, 15),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref=LIABILITY,
            lines=(
                PayableLineInput("Zero", Decimal("1"), Decimal("0"), EXPENSE),
                PayableLineInput(
                    "Tax only",
                    Decimal("1"),
                    Decimal("0"),
                    EXPENSE,
                    Decimal("40"),
                    "account:1460",
                ),
                PayableLineInput("Goods", Decimal("1"), Decimal("500"), EXPENSE),
            ),
        ),
        idempotency_key=f"a6:cnz:{n}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    submit_credit_note(
        db,
        scope=SCOPE,
        credit_note_id=credit.id,
        submitted_by="user:ap",
        submitted_at=NOW,
    )
    approve_credit_note(
        db,
        scope=SCOPE,
        credit_note_id=credit.id,
        approval_reference="approval:z",
        approved_by="user:controller",
        idempotency_key=f"a6:cnz:a:{n}",
        idempotency_expires_at=None,
        approved_at=NOW,
    )
    consequence = build_credit_accounting_consequence(
        db, tenant_id=TENANT, credit_note_id=credit.id
    )
    assert [(e.account_ref, e.debit, e.credit) for e in consequence.entries] == [
        (LIABILITY, Decimal("540"), Decimal("0")),
        ("account:1460", Decimal("0"), Decimal("40")),
        (EXPENSE, Decimal("0"), Decimal("500")),
    ]


def test_the_entry_invariant_refuses_empty_zero_and_unbalanced_shapes() -> None:
    good = [
        AccountingEntry("a", debit=Decimal("5")),
        AccountingEntry("b", credit=Decimal("5")),
    ]
    _check_consequence_entries(list(good))
    for bad in (
        [],
        [*good, AccountingEntry("z")],
        [
            AccountingEntry("a", debit=Decimal("5")),
            AccountingEntry("b", credit=Decimal("4")),
        ],
        [
            AccountingEntry("a", debit=Decimal("-5")),
            AccountingEntry("b", credit=Decimal("-5")),
        ],
    ):
        with pytest.raises(InvalidAmount) as caught:
            _check_consequence_entries(list(bad))
        assert caught.value.code == "consequence_entry_invalid"


def test_the_builder_backstop_refuses_a_forged_unbalanced_document(db: Session) -> None:
    invoice, _ = _invoice(db)
    # Lines that no longer add up to the liability can never be posted.
    line = db.scalars(select(models.SupplierInvoiceLine)).one()
    line.line_amount = Decimal("999")
    db.flush()
    with pytest.raises(InvalidAmount) as caught:
        _invoice_entries(db, invoice)
    assert caught.value.code == "consequence_entry_invalid"


# --- item 3: historical supplier credit ----------------------------------------


def _overpaid_case(
    db: Session,
    *,
    n: int | None = None,
    shell=None,
    credit: str = "100.00",
    **over,
):
    """A shell invoice plus the evidence that its settlements overpaid it by 100."""
    n = n or _n()
    invoice = shell or create_invoice(
        db,
        scope=SCOPE,
        command=CreateSupplierInvoice(
            number=f"PINV-OP-{n}",
            supplier_ref=SUPPLIER,
            supplier_name_snapshot="Fibre Vendor Ltd",
            supplier_document_number=f"OP-{n}",
            invoice_date=date(2026, 8, 1),
            received_date=date(2026, 8, 2),
            currency_code="NGN",
            exchange_rate=Decimal("1"),
            liability_account_ref=LIABILITY,
            lines=(PayableLineInput("Fibre", Decimal("1"), Decimal("1000"), EXPENSE),),
            schedule=(ObligationSchedule(date(2026, 8, 31), Decimal("1000")),),
        ),
        idempotency_key=f"a6:op:{n}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )
    invoice.status = InvoiceStatus.APPROVED  # the importer's direct UPDATE
    db.flush()
    total = invoice.total_amount.quantize(Decimal("0.01"))
    first = Decimal("700.00")
    credit_amount = Decimal(credit)
    second = total + credit_amount - first
    settlements = (
        HistoricalSettlement(
            f"op-alloc:{n}:1",
            "PAY-200",
            date(2026, 8, 20),
            first,
            Decimal("0.00"),
            "NGN",
            Decimal("1"),
            "cv1:" + "1" * 64,
            "legacy:journal:p200",
            "v2:journal:p200",
        ),
        HistoricalSettlement(
            f"op-alloc:{n}:2",
            "PAY-201",
            date(2026, 8, 25),
            second,
            Decimal("20.00"),
            "NGN",
            Decimal("1"),
            "cv1:" + "2" * 64,
            "legacy:journal:p201",
            "v2:journal:p201",
        ),
    )
    values = {
        "tenant_id": TENANT,
        "source_owner": "legacy",
        "source_invoice_id": f"src:op:{n}",
        "source_snapshot_sha256": "a" * 64,
        "invoice_id": invoice.id,
        "source_status": "POSTED",
        "invoice_type": "STANDARD",
        "number": invoice.number,
        "supplier_ref": invoice.supplier_ref,
        "supplier_name_snapshot": invoice.supplier_name_snapshot,
        "currency_code": "NGN",
        "exchange_rate": Decimal("1"),
        "total_amount": total,
        "liability_account_ref": LIABILITY,
        "settlements": settlements,
        "settled_amount": total + credit_amount,
        "withholding_tax_amount": Decimal("20.00"),
        "credit_amount": credit_amount,
        "carried_control_amount": -credit_amount,
        "carried_evidence_ref": "opening-balance:schedule-3#row-41",
        "original_posted_at": NOW,
        "source_journal_ref": "legacy:journal:inv",
        "destination_journal_ref": "v2:journal:inv",
        "accounting_evidence_fingerprint": "cv1:" + "b" * 64,
        "facts_fingerprint": "cv1:" + "c" * 64,
    }
    values.update(over)
    return invoice, HistoricalSupplierCreditEvidence(**values)


def _credit_command(evidence, **over) -> HistoricalSupplierCreditCommand:
    values = {
        "source_owner": evidence.source_owner,
        "source_invoice_id": evidence.source_invoice_id,
        "invoice_id": evidence.invoice_id,
        "credit_number": f"HCN-{evidence.source_invoice_id.split(':')[-1]}",
        "source_snapshot_sha256": evidence.source_snapshot_sha256,
        "evidence_fingerprint": evidence.fingerprint(),
        "evidence_reference": "finance:r5b-review#19",
        "admitted_by": "user:finance",
    }
    values.update(over)
    return HistoricalSupplierCreditCommand(**values)


def _admit_credit(db, evidence, *, key="a6:hc", command=None, verifier=None):
    return admit_historical_supplier_credit(
        db,
        scope=SCOPE,
        command=command or _credit_command(evidence),
        verifier=verifier or _RecVerifier(evidence),
        idempotency_key=key,
        idempotency_expires_at=None,
        admitted_at=NOW + timedelta(days=30),
    )


def test_credit_admission_writes_an_approved_credit_with_no_gl(db: Session) -> None:
    invoice, evidence = _overpaid_case(db)
    credit = _admit_credit(db, evidence)
    assert credit.status == CreditNoteStatus.APPROVED
    assert (credit.subtotal, credit.total_amount, credit.available_amount) == (
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
    )
    assert credit.tax_amount == 0
    assert credit.original_invoice_id == invoice.id
    assert credit.supplier_document_number is None
    assert credit.approval_reference == "finance:r5b-review#19"
    assert credit.approved_by == "user:finance"
    assert credit.number == _credit_command(evidence).credit_number.upper()
    assert _count(db, models.CreditNoteLine) == 0
    receipt = db.scalars(select(models.HistoricalCreditReceipt)).one()
    assert receipt.credit_note_id == credit.id and receipt.invoice_id == invoice.id
    assert receipt.credit_amount == Decimal("100")
    assert receipt.carried_control_amount == Decimal("-100")
    assert receipt.carried_evidence_ref == "opening-balance:schedule-3#row-41"
    assert receipt.evidence["settlements"][1]["payment_ref"] == "PAY-201"
    rows = db.scalars(
        select(models.HistoricalCreditSettlementRecord).order_by(
            models.HistoricalCreditSettlementRecord.line_number
        )
    ).all()
    assert [(r.payment_ref, r.applied_amount, r.withholding_amount) for r in rows] == [
        ("PAY-200", Decimal("700"), Decimal("0")),
        ("PAY-201", Decimal("400"), Decimal("20")),
    ]
    event = db.scalars(select(models.LiabilityEvent)).one()
    assert (event.event_kind, event.document_kind, event.document_id) == (
        "historical_credit_recognized",
        "credit_note",
        credit.id,
    )
    assert event.amount == Decimal("-100")
    assert supplier_liability_balance(
        db, tenant_id=TENANT, supplier_ref=SUPPLIER, currency_code="NGN"
    ) == Decimal("-100")
    assert _count(db, models.AccountingReceipt) == 0
    assert _count(db, models.PaymentObligation) == 0


def test_credit_admission_is_idempotent_and_conflicts_on_changed_evidence(
    db: Session,
) -> None:
    _, evidence = _overpaid_case(db)
    first = _admit_credit(db, evidence)
    again = _admit_credit(db, evidence, key="a6:hc:2")
    assert again.id == first.id
    assert _count(db, models.CreditNote) == 1
    assert _count(db, models.HistoricalCreditSettlementRecord) == 2
    changed = replace(evidence, facts_fingerprint="cv1:" + "d" * 64)
    with pytest.raises(Conflict, match="different permanent historical evidence"):
        _admit_credit(db, changed, key="a6:hc:3")
    with pytest.raises(Conflict, match="different permanent historical evidence"):
        _admit_credit(db, changed)  # the same key with other facts


def test_credit_must_be_an_exact_overpayment(db: Session) -> None:
    _, evidence = _overpaid_case(db)
    exact = replace(
        evidence,
        settlements=evidence.settlements[:1]
        + (replace(evidence.settlements[1], applied_amount=Decimal("300.00")),),
        settled_amount=Decimal("1000.00"),
        credit_amount=Decimal("0.00"),
        carried_control_amount=Decimal("0.00"),
    )
    with pytest.raises(InvalidAmount) as caught:
        _admit_credit(db, exact)
    assert caught.value.code == "historical_credit_not_overpaid"
    wrong_credit = replace(evidence, credit_amount=Decimal("99.00"))
    with pytest.raises(InvalidAmount) as caught:
        _admit_credit(db, wrong_credit)
    assert caught.value.code == "historical_credit_not_overpaid"
    assert _count(db, models.CreditNote) == 0


@pytest.mark.parametrize("carried", ["-99.00", "100.00", "0.00", "-101.00"])
def test_the_carried_control_must_hold_exactly_the_credit(
    db: Session, carried: str
) -> None:
    _, evidence = _overpaid_case(db)
    with pytest.raises(InvalidAmount) as caught:
        _admit_credit(db, replace(evidence, carried_control_amount=Decimal(carried)))
    assert caught.value.code == "legacy_gl_does_not_hold_credit"


def test_credit_evidence_must_add_up(db: Session) -> None:
    _, evidence = _overpaid_case(db)
    with pytest.raises(InvalidAmount, match="do not sum"):
        _admit_credit(db, replace(evidence, settled_amount=Decimal("1101.00")))
    with pytest.raises(InvalidAmount, match="do not sum"):
        _admit_credit(db, replace(evidence, withholding_tax_amount=Decimal("21.00")))
    fx = replace(evidence.settlements[0], exchange_rate=Decimal("2"))
    with pytest.raises(InvalidAmount, match="fx_difference_unsupported"):
        _admit_credit(db, replace(evidence, settlements=(fx, evidence.settlements[1])))
    with pytest.raises(InvalidAmount):
        _admit_credit(db, replace(evidence, total_amount=Decimal("1000.001")))
    with pytest.raises(InvalidTransition, match="POSTED STANDARD"):
        _admit_credit(db, replace(evidence, source_status="VOID"))
    duplicate = replace(
        evidence.settlements[1],
        source_settlement_id=evidence.settlements[0].source_settlement_id,
    )
    with pytest.raises(Conflict, match="unique"):
        _admit_credit(
            db, replace(evidence, settlements=(evidence.settlements[0], duplicate))
        )
    late = replace(evidence.settlements[1], settled_on=date(2027, 1, 1))
    with pytest.raises(InvalidTransition, match="follow admission"):
        _admit_credit(
            db, replace(evidence, settlements=(evidence.settlements[0], late))
        )
    early = replace(evidence.settlements[0], settled_on=date(2026, 7, 1))
    with pytest.raises(InvalidTransition, match="precedes the invoice"):
        _admit_credit(
            db, replace(evidence, settlements=(early, evidence.settlements[1]))
        )
    assert _count(db, models.CreditNote) == 0


def test_credit_destination_must_match_the_invoice(db: Session) -> None:
    for field, value in (
        ("number", "OTHER"),
        ("supplier_ref", "party:other"),
        ("currency_code", "USD"),
        ("total_amount", Decimal("1001.00")),
        ("liability_account_ref", "account:9999"),
    ):
        _, evidence = _overpaid_case(db)
        if field == "total_amount":
            evidence = replace(
                evidence,
                total_amount=value,
                credit_amount=Decimal("99.00"),
                carried_control_amount=Decimal("-99.00"),
            )
        elif field == "currency_code":
            evidence = replace(
                evidence,
                currency_code="USD",
                settlements=tuple(
                    replace(s, currency_code="USD") for s in evidence.settlements
                ),
            )
        else:
            evidence = replace(evidence, **{field: value})
        with pytest.raises(Conflict, match=f"differs on {field}"):
            _admit_credit(db, evidence)
    assert _count(db, models.CreditNote) == 0


def test_credit_destination_must_be_an_unrecognized_shell(db: Session) -> None:
    invoice, evidence = _overpaid_case(db)
    db.add(
        models.PaymentObligation(
            tenant_id=TENANT,
            invoice_id=invoice.id,
            sequence=1,
            due_date=date(2026, 8, 31),
            currency_code="NGN",
            original_amount=Decimal("1000"),
            outstanding_amount=Decimal("1000"),
            status=ObligationStatus.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.flush()
    with pytest.raises(Conflict, match="owner recognition or accounting evidence"):
        _admit_credit(db, evidence)


def test_a_draft_destination_is_refused(db: Session) -> None:
    invoice, evidence = _overpaid_case(db)
    invoice.status = InvoiceStatus.DRAFT
    db.flush()
    with pytest.raises(InvalidTransition, match="must already be APPROVED"):
        _admit_credit(db, evidence)


def _settled_command(evidence) -> HistoricalSettledInvoiceCommand:
    return HistoricalSettledInvoiceCommand(
        evidence.source_owner,
        evidence.source_invoice_id,
        evidence.invoice_id,
        evidence.source_snapshot_sha256,
        evidence.fingerprint(),
    )


def _admit_settled(db, evidence, key):
    return admit_historical_settled_invoice(
        db,
        scope=SCOPE,
        command=_settled_command(evidence),
        verifier=_RecVerifier(evidence),
        idempotency_key=key,
        idempotency_expires_at=None,
        admitted_at=NOW + timedelta(days=30),
    )


def test_a_settlement_id_cannot_be_used_twice_across_both_admissions(
    db: Session,
) -> None:
    # settled admission first, credit admission second
    _, settled = _settled_case(db, n=2)
    _admit_settled(db, settled, "a6:s2")
    _, credit_evidence = _overpaid_case(db)
    reused = replace(credit_evidence.settlements[0], source_settlement_id="alloc:1")
    reused_evidence = replace(
        credit_evidence, settlements=(reused, credit_evidence.settlements[1])
    )
    with pytest.raises(Conflict, match="already admitted against an invoice"):
        _admit_credit(db, reused_evidence)
    # credit admission first, settled admission second
    _, fresh = _overpaid_case(db)
    _admit_credit(db, fresh, key="a6:hc:first")
    taken = fresh.settlements[0].source_settlement_id
    _, other = _settled_case(db, n=3)
    clash = replace(other.settlements[0], source_settlement_id=taken)
    other = replace(other, settlements=(clash, other.settlements[1]))
    with pytest.raises(Conflict, match="already admitted against an invoice"):
        _admit_settled(db, other, "a6:s3")


def test_a_historical_credit_has_no_consequence_and_no_receipt(db: Session) -> None:
    _, evidence = _overpaid_case(db)
    credit = _admit_credit(db, evidence)
    with pytest.raises(InvalidTransition) as caught:
        build_credit_accounting_consequence(
            db, tenant_id=TENANT, credit_note_id=credit.id
        )
    assert caught.value.code == "historical_credit_has_no_consequence"
    with pytest.raises(InvalidTransition) as via_receipt:
        record_accounting_receipt(
            db,
            scope=SCOPE,
            command=AccountingReceiptInput(
                "credit_note", credit.id, "a" * 64, "journal:x", "b" * 64
            ),
            idempotency_key="a6:cr",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )
    assert via_receipt.value.code == "historical_credit_has_no_consequence"
    assert _count(db, models.AccountingReceipt) == 0


def test_the_credit_applies_to_a_new_invoice_and_the_balance_falls_by_it(
    db: Session,
) -> None:
    _, evidence = _overpaid_case(db)
    _, (ob,) = _invoice(db, lines=[("1000", "0", None)])
    before = supplier_liability_balance(
        db, tenant_id=TENANT, supplier_ref=SUPPLIER, currency_code="NGN"
    )
    credit = _admit_credit(db, evidence)
    assert supplier_liability_balance(
        db, tenant_id=TENANT, supplier_ref=SUPPLIER, currency_code="NGN"
    ) == before - Decimal("100")
    apply_credit_note(
        db,
        scope=SCOPE,
        command=ApplyCredit(credit.id, ob.id, Decimal("100"), "user:ap"),
        idempotency_key="a6:apply:hc",
        idempotency_expires_at=None,
        applied_at=NOW,
    )
    assert ob.outstanding_amount == Decimal("900")
    assert credit.available_amount == Decimal("0")
    # applying nets inside the control account: no new liability event
    assert supplier_liability_balance(
        db, tenant_id=TENANT, supplier_ref=SUPPLIER, currency_code="NGN"
    ) == before - Decimal("100")


def test_the_credit_invoice_cannot_be_recognized_admitted_or_posted(
    db: Session,
) -> None:
    # The shell is the settled-case invoice, so a settled admission would match
    # its header exactly and only the credit receipt can stop it.
    shell, settled = _settled_case(db, n=9)
    _, evidence = _overpaid_case(db, shell=shell)
    _admit_credit(db, evidence)
    with pytest.raises(Conflict, match="owner recognition or accounting evidence"):
        _admit_settled(db, settled, "a6:adm")
    proof = _rec_evidence(shell)
    with pytest.raises(InvalidTransition, match="recognition, obligation or"):
        recognize_approved_invoice(
            db,
            scope=SCOPE,
            command=RecognizeApprovedInvoice(
                shell.id,
                "census:x",
                "user:finance",
                "appr:1",
                proof.source_owner,
                proof.source_invoice_id,
                proof.source_snapshot_sha256,
                proof.fingerprint(),
            ),
            verifier=_RecVerifier(proof),
            idempotency_key="a6:rec",
            idempotency_expires_at=None,
            recognized_at=LATER,
        )
    with pytest.raises(InvalidTransition) as caught:
        build_invoice_accounting_consequence(db, tenant_id=TENANT, invoice_id=shell.id)
    assert caught.value.code == "invoice_carries_historical_credit"
    assert _count(db, models.PaymentObligation) == 0


def test_credit_admission_refuses_a_writing_verifier_and_a_scoped_session(
    db: Session,
) -> None:
    _, evidence = _overpaid_case(db)

    class Writing:
        def verify(self, *, scope, command, destination):
            destination.add(
                models.AccountingReceipt(
                    tenant_id=TENANT,
                    document_kind="forged",
                    document_id=uuid.uuid4(),
                    consequence_fingerprint="e" * 64,
                    accounting_reference="forged",
                    accounting_evidence_fingerprint="f" * 64,
                    recorded_at=NOW,
                )
            )
            return evidence

    with pytest.raises(InvalidTransition, match="verifier must not write"):
        _admit_credit(db, evidence, verifier=Writing())
    db.expunge_all()
    assert _count(db, models.CreditNote) == 0
    assert _count(db, models.AccountingReceipt) == 0
    with pytest.raises(InvalidTransition, match="plain Session"):
        admit_historical_supplier_credit(
            scoped_session(lambda: db),  # type: ignore[arg-type]
            scope=SCOPE,
            command=_credit_command(evidence),
            verifier=_RecVerifier(evidence),
            idempotency_key="a6:sc",
            idempotency_expires_at=None,
            admitted_at=NOW,
        )


def test_credit_admission_binds_identity_and_the_command(db: Session) -> None:
    _, evidence = _overpaid_case(db)
    with pytest.raises(Conflict, match="identity differs"):
        _admit_credit(
            db,
            evidence,
            command=_credit_command(evidence, source_invoice_id="src:other"),
        )
    with pytest.raises(Conflict, match="fingerprint differs"):
        _admit_credit(
            db,
            evidence,
            command=_credit_command(evidence, evidence_fingerprint="cv1:" + "0" * 64),
        )
    with pytest.raises(PayablesError, match="evidence reference is required"):
        _admit_credit(
            db, evidence, command=_credit_command(evidence, evidence_reference=" ")
        )
    with pytest.raises(PayablesError, match="admitted by is required"):
        _admit_credit(db, evidence, command=_credit_command(evidence, admitted_by=" "))
    assert _count(db, models.CreditNote) == 0


def test_no_second_person_is_required_to_declare_or_admit(db: Session) -> None:
    # Michael 2026-11-17: maker-checker is optional and off. The declaring and
    # admitting actors are recorded as given; nothing compares them to anyone.
    policy = _declare(db, by="user:solo")
    assert policy.declared_by == "user:solo"
    _, evidence = _overpaid_case(db)
    credit = _admit_credit(
        db, evidence, command=_credit_command(evidence, admitted_by="user:solo")
    )
    assert credit.approved_by == credit.submitted_by == "user:solo"


# --- review fixes: replay identity, unproven credit hook ------------------------


@pytest.mark.parametrize(
    "change",
    [
        {"credit_number": "HCN-OTHER"},
        {"evidence_reference": "finance:other#1"},
        {"admitted_by": "user:someone-else"},
    ],
)
def test_a_credit_replay_with_other_number_reference_or_actor_is_a_conflict(
    db: Session, change: dict[str, str]
) -> None:
    _, evidence = _overpaid_case(db)
    first = _admit_credit(db, evidence)
    with pytest.raises(Conflict, match="different permanent historical evidence"):
        _admit_credit(
            db,
            evidence,
            key="a6:hc:replay",
            command=_credit_command(evidence, **change),
        )
    with pytest.raises(Conflict, match="different permanent historical evidence"):
        _admit_credit(
            db, evidence, key="a6:hc", command=_credit_command(evidence, **change)
        )
    # The identical command still replays.
    assert _admit_credit(db, evidence, key="a6:hc:again").id == first.id
    assert _count(db, models.CreditNote) == 1


def test_a_credit_with_deferral_tax_on_unproven_settled_history_is_refused(
    db: Session,
) -> None:
    _declare(db)
    invoice, _evidence, _ob = _settled_with_deferral(db)
    with pytest.raises(InvalidTransition) as caught:
        _credit(db, invoice=invoice, net="100", tax="10")
    assert caught.value.code == "historical_deferral_unproven"
    # Once the position is admitted the same credit is accepted.
    _admit_deferral(db, _deferral_evidence(invoice, _evidence.source_invoice_id))
    _credit(db, invoice=invoice, net="100", tax="10")


# --- spec 11: credit approval splits, credit applications, reversal refusal -----


def _apply(db: Session, credit, ob, amount: str):
    return apply_credit_note(
        db,
        scope=SCOPE,
        command=ApplyCredit(credit.id, ob.id, Decimal(amount), "user:ap"),
        idempotency_key=f"a6:ap:{_n()}",
        idempotency_expires_at=None,
        applied_at=NOW,
    )


def _cash_credit(db: Session, amount: str):
    """A CASH credit: an admitted historical overpayment (item 3)."""
    _, evidence = _overpaid_case(db, credit=amount)
    return _admit_credit(db, evidence, key=f"a6:cash:{_n()}")


def _identity(db: Session, invoice):
    p = _position(db, invoice)
    assert p.remaining >= 0 and p.claimed >= 0
    assert p.credited + p.credit_released <= p.deferred
    assert p.deferred == p.remaining + p.claimed + p.credited + p.credit_released
    return p


def _app_rows(db: Session) -> list[models.CreditApplicationTaxRelease]:
    return list(
        db.scalars(
            select(models.CreditApplicationTaxRelease).order_by(
                models.CreditApplicationTaxRelease.created_at,
                models.CreditApplicationTaxRelease.deferral_account_ref,
            )
        )
    )


def _splits(db: Session) -> list[models.CreditNoteTaxSplit]:
    return list(
        db.scalars(
            select(models.CreditNoteTaxSplit).order_by(
                models.CreditNoteTaxSplit.created_at,
                models.CreditNoteTaxSplit.line_number,
            )
        )
    )


def _credit_consequence(db: Session, credit):
    return build_credit_accounting_consequence(
        db, tenant_id=TENANT, credit_note_id=credit.id
    )


# split at approval


def test_split_on_an_unpaid_invoice_puts_all_tax_on_the_deferral_account_a5_bytes(
    db: Session,
) -> None:
    from sqlalchemy import delete

    _declare(db)
    invoice, _ = _invoice(db)
    credit = _credit(db, invoice=invoice)  # 400 + 30 deferral tax
    (split,) = _splits(db)
    assert (split.deferred_amount, split.released_amount) == (
        Decimal("30"),
        Decimal("0"),
    )
    consequence = _credit_consequence(db, credit)
    assert _shape(consequence) == [
        (LIABILITY, Decimal("430"), Decimal("0")),
        (EXPENSE, Decimal("0"), Decimal("400")),
        (DEFERRAL, Decimal("0"), Decimal("30")),
    ]
    # Nothing on the release account: byte-identical to the a5 builder, which has no
    # split rows at all.
    with_split = consequence.fingerprint
    db.execute(delete(models.CreditNoteTaxSplit))
    db.expire_all()
    assert _credit_consequence(db, credit).fingerprint == with_split


def test_split_on_a_fully_paid_invoice_puts_all_tax_on_the_release_account(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _pay(db, [(ob, "1075")])
    credit = _credit(db, invoice=invoice)
    (split,) = _splits(db)
    assert (split.deferred_amount, split.released_amount) == (
        Decimal("0"),
        Decimal("30"),
    )
    assert _shape(_credit_consequence(db, credit)) == [
        (LIABILITY, Decimal("430"), Decimal("0")),
        (EXPENSE, Decimal("0"), Decimal("400")),
        (RELEASE, Decimal("0"), Decimal("30")),
    ]
    p = _identity(db, invoice)
    assert (p.remaining, p.claimed, p.credited, p.credit_released) == (
        Decimal("0"),
        Decimal("45"),
        Decimal("0"),
        Decimal("30"),
    )


def test_split_on_a_half_paid_invoice_takes_min_of_tax_and_what_remains(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _pay(db, [(ob, "537.5")])  # releases 37.50, R = 37.50
    credit = _credit(db, invoice=invoice, net="100", tax="50")
    (split,) = _splits(db)
    assert (split.deferred_amount, split.released_amount) == (
        Decimal("37.5"),
        Decimal("12.5"),
    )
    assert _shape(_credit_consequence(db, credit)) == [
        (LIABILITY, Decimal("150"), Decimal("0")),
        (EXPENSE, Decimal("0"), Decimal("100")),
        (DEFERRAL, Decimal("0"), Decimal("37.5")),
        (RELEASE, Decimal("0"), Decimal("12.5")),
    ]
    p = _identity(db, invoice)
    assert (p.remaining, p.claimed) == (Decimal("0"), Decimal("25"))


def test_split_allocates_to_the_lines_in_line_number_order(db: Session) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _pay(db, [(ob, "537.5")])  # R = 37.50
    credit = _credit(
        db,
        invoice=invoice,
        lines=[("100", "20", DEFERRAL), ("100", "30", DEFERRAL)],
    )
    assert [
        (s.line_number, s.deferred_amount, s.released_amount) for s in _splits(db)
    ] == [
        (1, Decimal("20"), Decimal("0")),
        (2, Decimal("17.5"), Decimal("12.5")),
    ]
    assert _shape(_credit_consequence(db, credit)) == [
        (LIABILITY, Decimal("250"), Decimal("0")),
        (EXPENSE, Decimal("0"), Decimal("100")),
        (DEFERRAL, Decimal("0"), Decimal("20")),
        (EXPENSE, Decimal("0"), Decimal("100")),
        (DEFERRAL, Decimal("0"), Decimal("17.5")),
        (RELEASE, Decimal("0"), Decimal("12.5")),
    ]


def test_a_credit_taxed_to_the_release_account_is_refused(db: Session) -> None:
    _declare(db)
    invoice, _ = _invoice(db)
    with pytest.raises(InvalidTransition) as caught:
        _credit(db, invoice=invoice, net="100", tax="10", account=RELEASE)
    assert caught.value.code == "credit_tax_on_release_account"
    assert _splits(db) == []


def test_credit_approval_replay_writes_no_second_split(db: Session) -> None:
    _declare(db)
    invoice, _ = _invoice(db)
    credit = _credit(db, invoice=invoice, approve=False)
    for _ in range(2):
        approve_credit_note(
            db,
            scope=SCOPE,
            credit_note_id=credit.id,
            approval_reference="approval:replay",
            approved_by="user:controller",
            idempotency_key="a6:cn:replay",
            idempotency_expires_at=None,
            approved_at=NOW,
        )
    assert len(_splits(db)) == 1


# application rule a: CASH credits are payment-like


def test_the_high1_repro_pay_then_apply_a_cash_credit_releases_the_rest(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _pay(db, [(ob, "537.5")])  # releases 37.50
    credit = _cash_credit(db, "537.50")
    application = _apply(db, credit, ob, "537.5")
    assert ob.status == ObligationStatus.SETTLED
    (row,) = _app_rows(db)
    assert (row.amount, row.deferral_account_ref, row.release_account_ref) == (
        Decimal("37.5"),
        DEFERRAL,
        RELEASE,
    )
    p = _identity(db, invoice)
    assert (p.remaining, p.claimed, p.released_by_applications) == (
        Decimal("0"),
        Decimal("75"),
        Decimal("37.5"),
    )
    consequence = build_credit_application_accounting_consequence(
        db, tenant_id=TENANT, application_id=application.id
    )
    assert consequence.source.document_kind == "supplier_credit_application"
    assert consequence.posting_date == NOW.date()
    assert consequence.currency_code == "NGN"
    assert _shape(consequence) == [
        (RELEASE, Decimal("37.5"), Decimal("0")),
        (DEFERRAL, Decimal("0"), Decimal("37.5")),
    ]
    assert credit_application_tax_releases(db, TENANT, application.id) == (row,)
    # receipt round trip through the existing receipt command
    for key in ("a6:app:r1", "a6:app:r2"):
        record_accounting_receipt(
            db,
            scope=SCOPE,
            command=AccountingReceiptInput(
                "supplier_credit_application",
                application.id,
                consequence.fingerprint,
                "journal:app",
                "a" * 64,
            ),
            idempotency_key=key,
            idempotency_expires_at=None,
            recorded_at=NOW,
        )
    assert _count(db, models.AccountingReceipt) == 2  # the invoice's + this one
    with pytest.raises(Conflict):
        record_accounting_receipt(
            db,
            scope=SCOPE,
            command=AccountingReceiptInput(
                "supplier_credit_application",
                application.id,
                "b" * 64,
                "journal:app",
                "a" * 64,
            ),
            idempotency_key="a6:app:r3",
            idempotency_expires_at=None,
            recorded_at=NOW,
        )


def test_a_cash_credit_applied_as_a_partial_then_the_final(db: Session) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    credit = _cash_credit(db, "1075.00")
    _apply(db, credit, ob, "500")
    assert [r.amount for r in _app_rows(db)] == [Decimal("34.88")]
    _apply(db, credit, ob, "575")  # the final settlement releases exactly R
    assert [r.amount for r in _app_rows(db)] == [Decimal("34.88"), Decimal("40.12")]
    p = _identity(db, invoice)
    assert (p.remaining, p.claimed) == (Decimal("0"), Decimal("75"))
    assert credit.available_amount == 0


# application rule b: own DOCUMENT credits


def test_a_non_final_own_application_releases_nothing_and_the_payment_finishes(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    credit = _credit(db, invoice=invoice)  # 400 + 30, R = 45
    application = _apply(db, credit, ob, "430")
    assert _app_rows(db) == []
    with pytest.raises(InvalidTransition) as caught:
        build_credit_application_accounting_consequence(
            db, tenant_id=TENANT, application_id=application.id
        )
    assert caught.value.code == "credit_application_has_no_consequence"
    assert credit_application_tax_releases(db, TENANT, application.id) == ()
    payment = _pay(db, [(ob, "645")])
    assert [r.amount for r in _releases(db, payment)] == [Decimal("45")]
    p = _identity(db, invoice)
    assert (p.remaining, p.claimed, p.credited) == (
        Decimal("0"),
        Decimal("45"),
        Decimal("30"),
    )


def test_an_untaxed_own_discount_applied_last_releases_the_remainder(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _pay(db, [(ob, "537.5")])
    credit = _credit(db, invoice=invoice, net="537.5", tax="0", account=None)
    _apply(db, credit, ob, "537.5")
    (row,) = _app_rows(db)
    assert row.amount == Decimal("37.5")
    p = _identity(db, invoice)
    assert (p.remaining, p.claimed) == (Decimal("0"), Decimal("75"))


def test_a_taxed_own_credit_that_closes_the_invoice_ties_the_totals(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _pay(db, [(ob, "400")])  # releases 27.91, R = 47.09
    credit = _credit(db, invoice=invoice, net="625", tax="50")
    (split,) = _splits(db)
    assert (split.deferred_amount, split.released_amount) == (
        Decimal("47.09"),
        Decimal("2.91"),
    )
    _apply(db, credit, ob, "675")  # closes the invoice; R is already 0
    assert ob.status == ObligationStatus.SETTLED
    assert _app_rows(db) == []
    p = _identity(db, invoice)
    assert (p.remaining, p.claimed, p.credited, p.credit_released) == (
        Decimal("0"),
        Decimal("25"),
        Decimal("47.09"),
        Decimal("2.91"),
    )


# application rule c: set-off elsewhere


def test_a_document_credit_cannot_be_set_off_against_a_deferring_invoice(
    db: Session,
) -> None:
    _declare(db)
    _, (target,) = _invoice(db)
    unlinked = _credit(db, invoice=None, net="100", tax="0", account=None)
    other_invoice, _ = _invoice(db, lines=[("1000", "0", None)])
    linked_elsewhere = _credit(
        db, invoice=other_invoice, net="100", tax="0", account=None
    )
    for credit in (unlinked, linked_elsewhere):
        with pytest.raises(InvalidTransition) as caught:
            _apply(db, credit, target, "100")
        assert caught.value.code == "credit_setoff_on_deferred_invoice"
    assert target.outstanding_amount == Decimal("1075")
    assert _count(db, models.CreditApplication) == 0


def test_a_document_credit_set_off_is_allowed_once_nothing_is_deferred(
    db: Session,
) -> None:
    _declare(db)
    target_invoice, (target,) = _invoice(db, lines=[("1000", "75", DEFERRAL)])
    _credit(db, invoice=target_invoice, net="100", tax="75")  # R is now 0
    assert _identity(db, target_invoice).remaining == 0
    other = _credit(db, invoice=None, net="100", tax="0", account=None)
    _apply(db, other, target, "100")  # VAT-neutral
    assert target.outstanding_amount == Decimal("975")
    assert _app_rows(db) == []
    # and an invoice with no deferred tax at all is never affected
    _, (plain,) = _invoice(db, lines=[("1000", "0", None)])
    again = _credit(db, invoice=None, net="50", tax="0", account=None)
    _apply(db, again, plain, "50")
    assert plain.outstanding_amount == Decimal("950")


# interleavings: the end state is exact whatever the order


def test_interleaving_pay_credit_apply_pay(db: Session) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _pay(db, [(ob, "300")])  # releases 20.93
    credit = _credit(db, invoice=invoice, net="200", tax="15")
    _identity(db, invoice)
    _apply(db, credit, ob, "215")  # own, not final
    assert _app_rows(db) == []
    last = _pay(db, [(ob, "560")])  # final
    p = _identity(db, invoice)
    assert p.remaining == 0
    assert [r.amount for r in _releases(db, last)] == [Decimal("39.07")]
    assert p.released_by_payments + p.credited == Decimal("75")


def test_interleaving_credit_pay_cash_apply(db: Session) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    _credit(db, invoice=invoice)  # 400 + 30, never applied; R = 45
    _pay(db, [(ob, "500")])  # round(45 * 500 / 1075) = 20.93
    cash = _cash_credit(db, "575.00")
    _apply(db, cash, ob, "575")  # final: the exact remainder
    assert [r.amount for r in _app_rows(db)] == [Decimal("24.07")]
    p = _identity(db, invoice)
    assert (p.remaining, p.claimed) == (Decimal("0"), Decimal("45"))


def test_interleaving_on_a_multi_obligation_invoice(db: Session) -> None:
    _declare(db)
    invoice, (first, second) = _invoice(db, schedule=["600", "475"])
    cash = _cash_credit(db, "600.00")
    _apply(db, cash, first, "600")
    assert [r.amount for r in _app_rows(db)] == [Decimal("41.86")]
    payment = _pay(db, [(second, "475")])
    assert [r.amount for r in _releases(db, payment)] == [Decimal("33.14")]
    assert _identity(db, invoice).remaining == 0


# reversal


def test_a_payment_reversal_is_refused_after_a_credit_released_the_vat(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    payment = _pay(db, [(ob, "1075")])
    _receipt(db, payment)
    _credit(db, invoice=invoice)  # splits 30 onto the release account
    with pytest.raises(InvalidTransition) as caught:
        _reverse_payment(db, payment)
    assert caught.value.code == "payment_reversal_blocked_by_credit_vat"
    assert payment.status == SupplierPaymentStatus.RECORDED


def test_a_payment_reversal_is_refused_after_an_own_application_released_the_vat(
    db: Session,
) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    payment = _pay(db, [(ob, "537.5")])
    _receipt(db, payment)
    credit = _credit(db, invoice=invoice, net="537.5", tax="0", account=None)
    _apply(db, credit, ob, "537.5")
    assert len(_app_rows(db)) == 1
    with pytest.raises(InvalidTransition) as caught:
        _reverse_payment(db, payment)
    assert caught.value.code == "payment_reversal_blocked_by_credit_vat"


def test_a_payment_reversal_with_only_cash_applications_is_exact(db: Session) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    payment = _pay(db, [(ob, "537.5")])
    _receipt(db, payment)
    cash = _cash_credit(db, "537.50")
    _apply(db, cash, ob, "537.5")  # releases the other 37.50
    _reverse_payment(db, payment)
    assert payment.status == SupplierPaymentStatus.REVERSED
    p = _identity(db, invoice)
    assert (p.remaining, p.claimed) == (Decimal("37.5"), Decimal("37.5"))
    assert ob.outstanding_amount == Decimal("537.5")
    again = _pay(db, [(ob, "537.5")])  # re-payment releases the rest
    assert [r.amount for r in _releases(db, again)] == [Decimal("37.5")]
    assert _identity(db, invoice).remaining == 0


def _reverse_payment(db: Session, payment):
    return reverse_supplier_payment(
        db,
        scope=SCOPE,
        payment_id=payment.id,
        reason="bounced",
        reversal_date=date(2026, 9, 5),
        reversed_by="user:reverser",
        idempotency_key=f"a6:rv:{_n()}",
        idempotency_expires_at=None,
        reversed_at=LATER,
    )


# invariants over randomised event sequences


def test_the_position_invariants_hold_over_random_event_sequences(
    db: Session,
) -> None:
    import random

    _declare(db)
    for seed in range(40):
        rng = random.Random(seed)  # noqa: S311 - a reproducible test generator
        invoice, obligations = _invoice(
            db, schedule=["600", "475"] if seed % 2 else None
        )
        credits: list = []

        def open_obligations(obligations=obligations):
            for ob in obligations:
                db.refresh(ob)
            return [ob for ob in obligations if ob.outstanding_amount > 0]

        for _step in range(7):
            kind = rng.choice(["pay", "pay", "credit", "cash", "apply", "apply"])
            open_obs = open_obligations()
            try:
                if kind == "pay" and open_obs:
                    ob = rng.choice(open_obs)
                    cents = rng.randint(1, int(ob.outstanding_amount * 100))
                    _pay(db, [(ob, str(Decimal(cents) / 100))])
                elif kind == "credit":
                    tax = rng.randint(0, 40)
                    credits.append(
                        _credit(
                            db,
                            invoice=invoice,
                            net=str(rng.randint(1, 300)),
                            tax=str(tax),
                            account=DEFERRAL if tax else None,
                        )
                    )
                elif kind == "cash":
                    credits.append(_cash_credit(db, f"{rng.randint(50, 400)}.00"))
                elif kind == "apply" and open_obs and credits:
                    credit = rng.choice(credits)
                    db.refresh(credit)
                    ob = rng.choice(open_obs)
                    ceiling = min(credit.available_amount, ob.outstanding_amount)
                    if ceiling > 0:
                        cents = rng.randint(1, int(ceiling * 100))
                        _apply(db, credit, ob, str(Decimal(cents) / 100))
            except PayablesError:
                pass  # a refusal is a legal outcome; the invariants must still hold
            p = _identity(db, invoice)
            if not open_obligations():
                # Whatever closed the invoice, nothing may stay deferred.
                assert p.remaining == 0, (seed, p)
    # The generator really exercised every writer of the position.
    assert _count(db, models.SupplierPaymentTaxRelease) > 10
    assert _count(db, models.CreditApplicationTaxRelease) > 3
    assert any(s.released_amount > 0 for s in _splits(db))
    assert any(s.deferred_amount > 0 for s in _splits(db))


# --- review round 3: external observations, amount order, messages ---------------


def _observe(db: Session, ob, amount: str, *, owner: str = "bank-feed"):
    n = _n()
    return record_settlement(
        db,
        scope=SCOPE,
        command=SettlementObservationInput(
            obligation_id=ob.id,
            source_owner=owner,
            source_reference=f"obs:{n}",
            source_version="1",
            source_fingerprint="a" * 64,
            amount=Decimal(amount),
            occurred_at=NOW,
            currency_code="NGN",
        ),
        idempotency_key=f"a6:obs:{n}",
        idempotency_expires_at=None,
        recorded_at=NOW,
    )


def test_an_external_settlement_is_refused_on_an_invoice_that_still_defers_tax(
    db: Session,
) -> None:
    _declare(db)
    _, (ob,) = _invoice(db)
    with pytest.raises(InvalidTransition) as caught:
        _observe(db, ob, "100")
    assert caught.value.code == "settlement_observation_on_deferred_invoice"
    assert ob.outstanding_amount == Decimal("1075")
    assert _count(db, models.SettlementObservation) == 0


def test_an_external_settlement_is_allowed_when_nothing_is_deferred(
    db: Session,
) -> None:
    # no deferral tax at all
    _declare(db)
    _, (plain,) = _invoice(db, lines=[("1000", "0", None)])
    _observe(db, plain, "100")
    assert plain.outstanding_amount == Decimal("900")
    # tax that is not on a deferral account (folded)
    _, (folded,) = _invoice(db, lines=[("1000", "75", EXPENSE)])
    _observe(db, folded, "100")
    # a deferring invoice whose deferral is already fully released or credited
    invoice, (ob,) = _invoice(db)
    _credit(db, invoice=invoice, net="100", tax="75")  # R = 0
    _observe(db, ob, "100")
    assert ob.outstanding_amount == Decimal("975")
    # and without any policy the a5 behaviour is unchanged
    assert _count(db, models.SettlementObservation) == 3


def test_payments_still_settle_through_the_observation_path(db: Session) -> None:
    _declare(db)
    _, (ob,) = _invoice(db)
    payment = _pay(db, [(ob, "1075")])  # source owner is the payment owner
    assert payment.status == SupplierPaymentStatus.RECORDED
    assert _count(db, models.SettlementObservation) == 1


def test_a_credit_application_validates_the_amount_before_any_release(
    db: Session,
) -> None:
    _declare(db)
    _, (ob,) = _invoice(db)
    credit = _cash_credit(db, "2000.00")
    for amount, match in (("0", "must be positive"), ("1075.01", "outstanding")):
        with pytest.raises(InvalidAmount, match=match):
            _apply(db, credit, ob, amount)
    assert _app_rows(db) == [] and _count(db, models.CreditApplication) == 0
    assert ob.outstanding_amount == Decimal("1075")


def test_refusal_messages_for_reversal_and_invariants(db: Session) -> None:
    _declare(db)
    invoice, (ob,) = _invoice(db)
    payment = _pay(db, [(ob, "1075")])
    _receipt(db, payment)
    _credit(db, invoice=invoice)
    with pytest.raises(InvalidTransition, match="Finance exceptions") as caught:
        _reverse_payment(db, payment)
    assert "reverse the credit" not in str(caught.value)
    line = db.scalars(select(models.SupplierPaymentTaxRelease)).one()
    line.amount = Decimal("200")  # forge a position that breaks the invariants
    db.flush()
    with pytest.raises(InvalidAmount, match="violates its invariants"):
        invoice_deferred_tax_position(db, TENANT, invoice.id)


def test_the_public_settlement_path_refuses_the_reserved_payment_owner(
    db: Session,
) -> None:
    from dotmac_payables.service import PAYMENT_SETTLEMENT_OWNER

    _declare(db)
    _, (ob,) = _invoice(db)
    for owner in (PAYMENT_SETTLEMENT_OWNER, f"  {PAYMENT_SETTLEMENT_OWNER} "):
        with pytest.raises(InvalidTransition) as caught:
            _observe(db, ob, "100", owner=owner)
        assert caught.value.code == "settlement_owner_reserved"
    assert ob.outstanding_amount == Decimal("1075")
    assert _count(db, models.SettlementObservation) == 0
    # the payment path still settles the same invoice
    payment = _pay(db, [(ob, "1075")])
    assert payment.status == SupplierPaymentStatus.RECORDED
    assert ob.status == ObligationStatus.SETTLED
    assert [r.amount for r in _releases(db, payment)] == [Decimal("75")]
