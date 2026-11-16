"""a5: governed reversal of a bank-returned salary settlement."""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Money
from dotmac_payroll import (
    SETTLEMENT_REVERSAL_DOCUMENT_KIND,
    PayrollConflict,
    PayrollNotFound,
    PayrollRuleViolation,
    build_liability_settlement_consequence,
    build_settlement_reversal_consequence,
    record_liability_settlement,
    reverse_liability_settlement,
)
from dotmac_payroll.models import (
    TENANT_MODELS,
    PayrollLiabilitySettlement,
    PayrollSettlementReversal,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.unit.test_payroll_a4 import (
    _calc,
    _finalize,
    _liabilities,
    _run,
    _setup,
)
from tests.unit.test_payroll_service import NGN, NOW, _tenant

RECORDER = uuid4()
REVERSER = uuid4()
BANK = "ledger:bank-main"


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_payroll": None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[Tenant.__table__, *(model.__table__ for model in TENANT_MODELS)],
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _settled_net_pay(db: Session, *, loan_mode: str | None = None):
    tenant = _tenant(db)
    base, tax, loan = _setup(db, tenant.id, loan_mode=loan_mode)
    run = _run(db, tenant.id)
    _calc(db, tenant.id, run, base, tax, loan)
    _finalize(db, tenant.id, run)
    net = next(
        x for x in _liabilities(db, run) if x.liability_key == "employee-net-pay"
    )
    settlement = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=net.id,
        amount=Money.of(str(net.amount), NGN),
        settlement_ref="PAY-1",
        evidence_ref="bank:1",
        recorded_by_id=RECORDER,
        recorded_at=NOW,
    )
    return tenant, net, settlement


def _reverse(db, tenant, settlement, **kw):
    args = {
        "tenant_id": tenant.id,
        "settlement_id": settlement.id,
        "return_ref": "RET-1",
        "reason": "account closed",
        "reversed_by_id": REVERSER,
        "reversed_at": NOW,
    }
    args.update(kw)
    return reverse_liability_settlement(db, **args)


def test_reversal_reopens_liability_and_leaves_settlement_untouched(
    db: Session,
) -> None:
    tenant, net, settlement = _settled_net_pay(db)
    assert (net.status, net.amount_settled) == ("settled", Decimal("900.00"))
    reversal = _reverse(db, tenant, settlement, return_ref="  RET-1  ")
    assert reversal.return_ref == "RET-1"
    assert (reversal.amount, reversal.liability_id) == (Decimal("900.00"), net.id)
    assert (net.status, net.amount_settled) == ("outstanding", Decimal("0.00"))
    row = db.scalar(select(PayrollLiabilitySettlement))
    assert (row.amount, row.settlement_ref, row.recorded_by_id) == (
        Decimal("900.00"),
        "PAY-1",
        RECORDER,
    )


def test_liability_is_resettleable_after_reversal(db: Session) -> None:
    tenant, net, settlement = _settled_net_pay(db)
    _reverse(db, tenant, settlement)
    again = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=net.id,
        amount=Money.of("900", NGN),
        settlement_ref="PAY-2",
        evidence_ref="bank:2",
        recorded_by_id=RECORDER,
        recorded_at=NOW,
    )
    assert net.status == "settled"
    # The first settlement is already reversed; replaying it stays a no-op.
    assert _reverse(db, tenant, settlement).settlement_id == settlement.id
    assert net.status == "settled"
    # The second one can itself be returned and reversed.
    second = _reverse(db, tenant, again, return_ref="RET-2")
    assert second.settlement_id == again.id
    assert net.status == "outstanding"


def test_replay_is_noop_and_different_evidence_conflicts(db: Session) -> None:
    tenant, net, settlement = _settled_net_pay(db)
    first = _reverse(db, tenant, settlement)
    again = _reverse(db, tenant, settlement, return_ref=" RET-1")
    assert again.id == first.id
    assert db.scalars(select(PayrollSettlementReversal)).all() == [first]
    assert (net.status, net.amount_settled) == ("outstanding", Decimal("0.00"))
    with pytest.raises(PayrollConflict):
        _reverse(db, tenant, settlement, return_ref="RET-OTHER")
    with pytest.raises(PayrollConflict):
        _reverse(db, tenant, settlement, reason="different reason")


def test_repay_under_the_reversed_reference_is_a_conflict(db: Session) -> None:
    tenant, net, settlement = _settled_net_pay(db)
    _reverse(db, tenant, settlement)
    with pytest.raises(PayrollConflict, match="new reference"):
        record_liability_settlement(
            db,
            tenant_id=tenant.id,
            liability_id=net.id,
            amount=Money.of("900", NGN),
            settlement_ref="PAY-1",
            evidence_ref="bank:1",
            recorded_by_id=RECORDER,
            recorded_at=NOW,
        )
    assert (net.status, net.amount_settled) == ("outstanding", Decimal("0.00"))


def test_replay_with_a_different_reverser_is_a_conflict(db: Session) -> None:
    tenant, _, settlement = _settled_net_pay(db)
    first = _reverse(db, tenant, settlement)
    assert _reverse(db, tenant, settlement).id == first.id
    with pytest.raises(PayrollConflict):
        _reverse(db, tenant, settlement, reversed_by_id=uuid4())


def test_reversed_at_must_be_aware_and_not_before_recorded_at(db: Session) -> None:
    tenant, net, settlement = _settled_net_pay(db)
    with pytest.raises(PayrollRuleViolation, match="timezone-aware"):
        _reverse(db, tenant, settlement, reversed_at=datetime(2026, 8, 20, 12))
    with pytest.raises(PayrollRuleViolation, match="before it was recorded"):
        _reverse(db, tenant, settlement, reversed_at=NOW - timedelta(seconds=1))
    assert net.status == "settled"
    assert _reverse(db, tenant, settlement, reversed_at=NOW).reversed_at == NOW


def test_reverser_equal_to_recorder_is_refused(db: Session) -> None:
    tenant, net, settlement = _settled_net_pay(db)
    with pytest.raises(PayrollRuleViolation, match="different person"):
        _reverse(db, tenant, settlement, reversed_by_id=RECORDER)
    assert net.status == "settled"
    assert db.scalars(select(PayrollSettlementReversal)).all() == []


def test_blank_reference_reason_and_unknown_settlement_refused(db: Session) -> None:
    tenant, _, settlement = _settled_net_pay(db)
    with pytest.raises(PayrollRuleViolation):
        _reverse(db, tenant, settlement, return_ref="  ")
    with pytest.raises(PayrollRuleViolation):
        _reverse(db, tenant, settlement, reason="")
    with pytest.raises(PayrollNotFound):
        _reverse(db, tenant, settlement, settlement_id=uuid4())
    with pytest.raises(PayrollNotFound):
        _reverse(db, _tenant(db), settlement)


def test_internal_recovery_is_refused(db: Session) -> None:
    tenant, net, _ = _settled_net_pay(db, loan_mode="internal_recovery")
    recovery = next(
        x
        for x in _liabilities(db, _run_of(db, net))
        if x.settlement_mode == "internal_recovery"
    )
    # An internal recovery cannot be settled by the API; forge a settlement row
    # directly to prove the reversal refuses it independently.
    forged = PayrollLiabilitySettlement(
        tenant_id=tenant.id,
        liability_id=recovery.id,
        amount=recovery.amount,
        settlement_ref="FORGED",
        evidence_ref="x",
        recorded_by_id=RECORDER,
        recorded_at=NOW,
    )
    db.add(forged)
    db.flush()
    with pytest.raises(PayrollRuleViolation, match="internal recovery"):
        _reverse(db, tenant, forged)
    assert (recovery.status, recovery.amount_settled) == ("settled", recovery.amount)


def _run_of(db: Session, liability):
    from dotmac_payroll.models import PayrollRun

    return db.get(PayrollRun, liability.run_id)


def test_partially_settled_liability_is_refused(db: Session) -> None:
    tenant = _tenant(db)
    base, tax, loan = _setup(db, tenant.id, loan_mode=None)
    run = _run(db, tenant.id)
    _calc(db, tenant.id, run, base, tax, loan)
    _finalize(db, tenant.id, run)
    net = next(
        x for x in _liabilities(db, run) if x.liability_key == "employee-net-pay"
    )
    part = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=net.id,
        amount=Money.of("400", NGN),
        settlement_ref="PART-1",
        evidence_ref="bank:1",
        recorded_by_id=RECORDER,
        recorded_at=NOW,
    )
    assert net.status == "partially_settled"
    with pytest.raises(PayrollRuleViolation, match="fully settle"):
        _reverse(db, tenant, part)
    # Completing it with a second settlement: neither piece fully settles.
    rest = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=net.id,
        amount=Money.of("500", NGN),
        settlement_ref="PART-2",
        evidence_ref="bank:2",
        recorded_by_id=RECORDER,
        recorded_at=NOW,
    )
    for piece in (part, rest):
        with pytest.raises(PayrollRuleViolation, match="fully settle"):
            _reverse(db, tenant, piece)
    assert (net.status, net.amount_settled) == ("settled", Decimal("900.00"))


def test_reversal_consequence_balances_and_is_stable(db: Session) -> None:
    tenant, _net, settlement = _settled_net_pay(db)
    reversal = _reverse(db, tenant, settlement)
    fact = build_settlement_reversal_consequence(
        db,
        tenant_id=tenant.id,
        reversal_id=reversal.id,
        bank_account_ref=BANK,
        posting_date=date(2026, 8, 5),
    )
    assert [(e.account_ref, e.debit, e.credit) for e in fact.entries] == [
        (BANK, Decimal("900.00"), Decimal("0")),
        ("2040", Decimal("0"), Decimal("900.00")),
    ]
    assert sum(e.debit for e in fact.entries) == sum(e.credit for e in fact.entries)
    assert fact.source.document_kind == SETTLEMENT_REVERSAL_DOCUMENT_KIND
    assert fact.source.document_id == str(reversal.id)
    canonical = "|".join(
        [
            str(tenant.id),
            "payroll",
            "payroll_settlement_reversal",
            str(reversal.id),
            "1",
            "RET-1",
            "2026-08-05",
            "NGN",
            "2",
            f"{BANK}:900.00:0",
            "2040:0:900.00",
        ]
    )
    assert fact.fingerprint == hashlib.sha256(canonical.encode()).hexdigest()
    assert fact.idempotency_key == (
        f"payroll:payroll_settlement_reversal:{tenant.id}:{reversal.id}"
    )
    again = build_settlement_reversal_consequence(
        db,
        tenant_id=tenant.id,
        reversal_id=reversal.id,
        bank_account_ref=BANK,
        posting_date=date(2026, 8, 5),
    )
    assert again == fact
    other_day = build_settlement_reversal_consequence(
        db,
        tenant_id=tenant.id,
        reversal_id=reversal.id,
        bank_account_ref=BANK,
        posting_date=date(2026, 8, 6),
    )
    assert other_day.fingerprint != fact.fingerprint
    with pytest.raises(PayrollRuleViolation, match="differ"):
        build_settlement_reversal_consequence(
            db,
            tenant_id=tenant.id,
            reversal_id=reversal.id,
            bank_account_ref="2040",
            posting_date=date(2026, 8, 5),
        )
    with pytest.raises(PayrollRuleViolation, match="bank account reference"):
        build_settlement_reversal_consequence(
            db,
            tenant_id=tenant.id,
            reversal_id=reversal.id,
            bank_account_ref=" ",
            posting_date=date(2026, 8, 5),
        )
    with pytest.raises(PayrollNotFound):
        build_settlement_reversal_consequence(
            db,
            tenant_id=tenant.id,
            reversal_id=uuid4(),
            bank_account_ref=BANK,
            posting_date=date(2026, 8, 5),
        )


def test_settlement_and_reversal_facts_net_to_zero_on_the_liability_account(
    db: Session,
) -> None:
    tenant, _, settlement = _settled_net_pay(db)
    reversal = _reverse(db, tenant, settlement)
    paid = build_liability_settlement_consequence(
        db,
        tenant_id=tenant.id,
        settlement_id=settlement.id,
        bank_account_ref=BANK,
        posting_date=date(2026, 8, 1),
    )
    back = build_settlement_reversal_consequence(
        db,
        tenant_id=tenant.id,
        reversal_id=reversal.id,
        bank_account_ref=BANK,
        posting_date=date(2026, 8, 5),
    )
    net: dict[str, Decimal] = {}
    for fact in (paid, back):
        for e in fact.entries:
            net[e.account_ref] = net.get(e.account_ref, Decimal(0)) + e.debit - e.credit
    assert set(net.values()) == {Decimal(0)}


def test_a3_a4_settlement_fact_is_byte_identical(db: Session) -> None:
    """a5 adds no field to the settlement fact: pin its a3 canonical form."""
    tenant, _, settlement = _settled_net_pay(db)
    fact = build_liability_settlement_consequence(
        db,
        tenant_id=tenant.id,
        settlement_id=settlement.id,
        bank_account_ref=BANK,
        posting_date=date(2026, 8, 1),
    )
    canonical = "|".join(
        [
            str(tenant.id),
            "payroll",
            "payroll_liability_settlement",
            str(settlement.id),
            "1",
            "PAY-1",
            "2026-08-01",
            "NGN",
            "2",
            "2040:900.00:0",
            f"{BANK}:0:900.00",
        ]
    )
    assert fact.fingerprint == hashlib.sha256(canonical.encode()).hexdigest()
    assert (fact.source.owner, fact.source.version) == ("payroll", "1")
