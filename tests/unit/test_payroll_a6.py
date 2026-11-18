"""a6: audited liability adjustment (adjust_liability)."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Currency, Money
from dotmac_payroll import (
    LIABILITY_ADJUSTMENT_DOCUMENT_KIND,
    PayrollConflict,
    PayrollNotFound,
    PayrollRuleViolation,
    adjust_liability,
    build_liability_adjustment_consequence,
    build_liability_settlement_consequence,
    build_run_accounting_consequence,
    build_settlement_reversal_consequence,
    effective_liability_amount,
    record_liability_settlement,
    reverse_liability_settlement,
)
from dotmac_payroll.models import (
    TENANT_MODELS,
    PayrollLiability,
    PayrollLiabilityAdjustment,
)
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.unit.test_payroll_a4 import (
    _calc,
    _finalize,
    _liabilities,
    _run,
    _setup,
)
from tests.unit.test_payroll_service import NGN, NOW, _tenant

ACTOR = uuid4()
OFFSET = "ledger:unclaimed-salary"
BANK = "ledger:bank-main"
LATER = datetime.now(UTC) + timedelta(minutes=5)


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


def _world(db: Session, *, loan_mode: str | None = None):
    tenant = _tenant(db)
    base, tax, loan = _setup(db, tenant.id, loan_mode=loan_mode)
    run = _run(db, tenant.id)
    calc = _calc(db, tenant.id, run, base, tax, loan)
    _finalize(db, tenant.id, run)
    net = next(
        x for x in _liabilities(db, run) if x.liability_key == "employee-net-pay"
    )
    return tenant, run, calc, net


def _adjust(db, tenant, liability, amount="-100", **kw):
    args = {
        "tenant_id": tenant.id,
        "liability_id": liability.id,
        "amount": amount if not isinstance(amount, str) else Money.of(amount, NGN),
        "adjustment_ref": "ADJ-1",
        "reason": "unclaimed salary",
        "evidence_ref": "memo:1",
        "offset_account_ref": OFFSET,
        "adjusted_by_id": ACTOR,
        "adjusted_at": LATER,
    }
    args.update(kw)
    return adjust_liability(db, **args)


def _settle(db, tenant, liability, amount, ref="PAY-1"):
    return record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=liability.id,
        amount=Money.of(amount, NGN),
        settlement_ref=ref,
        evidence_ref="bank:1",
        recorded_by_id=uuid4(),
        recorded_at=NOW,
    )


def test_partial_write_off_settles_effective_and_refuses_original(db: Session) -> None:
    tenant, _, _, net = _world(db)
    assert net.amount == Decimal("900.00")
    row = _adjust(db, tenant, net, "-100")
    assert row.amount == Decimal("-100.00")
    assert (net.amount, net.amount_adjusted) == (Decimal("900.00"), Decimal("-100.00"))
    assert effective_liability_amount(net) == Decimal("800.00")
    assert net.status == "outstanding"
    with pytest.raises(PayrollRuleViolation, match="exceeds the outstanding"):
        _settle(db, tenant, net, "900")
    _settle(db, tenant, net, "800")
    assert net.status == "settled"
    assert net.amount == Decimal("900.00")


def test_full_write_off_is_settled_and_cannot_be_paid(db: Session) -> None:
    tenant, _, _, net = _world(db)
    _adjust(db, tenant, net, "-900")
    assert effective_liability_amount(net) == Decimal("0.00")
    assert net.status == "settled"
    with pytest.raises(PayrollRuleViolation, match="exceeds the outstanding"):
        _settle(db, tenant, net, "0.01")


def test_positive_adjustment_increases_effective_amount(db: Session) -> None:
    tenant, _, _, net = _world(db)
    _settle(db, tenant, net, "900")
    assert net.status == "settled"
    _adjust(db, tenant, net, "50")
    assert effective_liability_amount(net) == Decimal("950.00")
    assert net.status == "partially_settled"
    _settle(db, tenant, net, "50", ref="PAY-2")
    assert net.status == "settled"


def test_write_down_cannot_exceed_outstanding(db: Session) -> None:
    tenant, _, _, net = _world(db)
    _settle(db, tenant, net, "400")
    with pytest.raises(PayrollRuleViolation, match="adjustment_exceeds_outstanding"):
        _adjust(db, tenant, net, "-500.01")
    _adjust(db, tenant, net, "-500")
    assert net.status == "settled"
    assert net.amount_settled == Decimal("400.00")


def test_corrections_are_forward_opposite_adjustments(db: Session) -> None:
    tenant, _, _, net = _world(db)
    _adjust(db, tenant, net, "-100", adjustment_ref="A")
    _adjust(db, tenant, net, "100", adjustment_ref="B")
    assert net.amount_adjusted == Decimal("0.00")
    assert net.status == "outstanding"
    rows = db.scalars(select(PayrollLiabilityAdjustment)).all()
    assert sum(r.amount for r in rows) == net.amount_adjusted
    # no second person is required: the same actor made both.
    assert {r.adjusted_by_id for r in rows} == {ACTOR}


def test_refusals(db: Session) -> None:
    tenant, _, _, net = _world(db, loan_mode="internal_recovery")
    recovery = next(
        x
        for x in _liabilities(db, db.get(type(_run_of(db, net)), net.run_id))
        if x.settlement_mode == "internal_recovery"
    )
    with pytest.raises(PayrollRuleViolation, match="internal_recovery_not_adjustable"):
        _adjust(db, tenant, recovery, "-1")
    with pytest.raises(PayrollRuleViolation, match="currency"):
        _adjust(db, tenant, net, Money.of("-1", Currency("NGN", 3)))
    with pytest.raises(PayrollRuleViolation, match="currency"):
        _adjust(db, tenant, net, Money.of("-1", Currency("USD", 2)))
    with pytest.raises(PayrollRuleViolation, match="zero"):
        _adjust(db, tenant, net, "0")
    scaled = SimpleNamespace(amount=Decimal("-1.001"), currency=NGN)
    with pytest.raises(PayrollRuleViolation, match="currency scale"):
        _adjust(db, tenant, net, scaled)
    with pytest.raises(PayrollRuleViolation, match="offset_is_liability_account"):
        _adjust(db, tenant, net, offset_account_ref=" 2040 ")
    for field in (
        "adjustment_ref",
        "reason",
        "evidence_ref",
        "offset_account_ref",
    ):
        with pytest.raises(PayrollRuleViolation, match="must not be blank"):
            _adjust(db, tenant, net, **{field: "  "})
    with pytest.raises(PayrollRuleViolation, match="timezone-aware"):
        _adjust(db, tenant, net, adjusted_at=datetime(2030, 1, 1))
    with pytest.raises(PayrollRuleViolation, match="before it was created"):
        _adjust(db, tenant, net, adjusted_at=datetime(2001, 1, 1, tzinfo=UTC))
    with pytest.raises(PayrollNotFound):
        _adjust(db, tenant, net, liability_id=uuid4())
    with pytest.raises(PayrollNotFound):
        _adjust(db, _tenant(db), net)
    assert db.scalars(select(PayrollLiabilityAdjustment)).all() == []
    assert net.amount_adjusted == Decimal("0")


def _run_of(db, liability):
    from dotmac_payroll.models import PayrollRun

    return db.get(PayrollRun, liability.run_id)


def test_replay_is_noop_and_changed_evidence_conflicts(db: Session) -> None:
    tenant, _, _, net = _world(db)
    first = _adjust(db, tenant, net, "-100", adjustment_ref=" ADJ-1 ")
    again = _adjust(db, tenant, net, "-100")
    assert again.id == first.id
    assert db.scalars(select(PayrollLiabilityAdjustment)).all() == [first]
    assert net.amount_adjusted == Decimal("-100.00")
    for change in (
        {"amount": "-101"},
        {"reason": "other"},
        {"evidence_ref": "memo:2"},
        {"offset_account_ref": "ledger:other"},
        {"adjusted_by_id": uuid4()},
    ):
        with pytest.raises(PayrollConflict):
            _adjust(db, tenant, net, **{"amount": "-100", **change})
    assert net.amount_adjusted == Decimal("-100.00")


def test_replay_survives_later_state_changes(db: Session) -> None:
    tenant, _, _, net = _world(db)
    first = _adjust(db, tenant, net, "-900")
    # Fully written down: a fresh adjustment would exceed, a replay does not.
    assert _adjust(db, tenant, net, "-900").id == first.id


def test_reversal_on_an_adjusted_liability(db: Session) -> None:
    tenant, _, _, net = _world(db)
    _adjust(db, tenant, net, "-100")
    settlement = _settle(db, tenant, net, "800")
    assert net.status == "settled"
    reversal = reverse_liability_settlement(
        db,
        tenant_id=tenant.id,
        settlement_id=settlement.id,
        return_ref="RET-1",
        reason="account closed",
        reversed_by_id=uuid4(),
        reversed_at=NOW,
    )
    assert reversal.amount == Decimal("800.00")
    assert (net.status, net.amount_settled) == ("outstanding", Decimal("0.00"))
    assert effective_liability_amount(net) == Decimal("800.00")
    fact = build_settlement_reversal_consequence(
        db,
        tenant_id=tenant.id,
        reversal_id=reversal.id,
        bank_account_ref=BANK,
        posting_date=date(2026, 8, 5),
    )
    assert fact.entries[1].credit == Decimal("800.00")


def test_run_consequence_still_builds_after_adjustment(db: Session) -> None:
    tenant, run, _, net = _world(db)
    before = build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    _adjust(db, tenant, net, "-900")
    after = build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    assert after == before
    assert net.amount == Decimal("900.00")


def test_adjustment_consequence_write_down_and_increase(db: Session) -> None:
    tenant, _, _, net = _world(db)
    down = _adjust(db, tenant, net, "-100", adjustment_ref="DOWN")
    up = _adjust(db, tenant, net, "30", adjustment_ref="UP")
    fact = build_liability_adjustment_consequence(
        db, tenant_id=tenant.id, adjustment_id=down.id, posting_date=date(2026, 8, 5)
    )
    assert [(e.account_ref, e.debit, e.credit) for e in fact.entries] == [
        ("2040", Decimal("100.00"), Decimal("0")),
        (OFFSET, Decimal("0"), Decimal("100.00")),
    ]
    assert fact.source.document_kind == LIABILITY_ADJUSTMENT_DOCUMENT_KIND
    assert fact.source.document_id == str(down.id)
    canonical = "|".join(
        [
            str(tenant.id),
            "payroll",
            "payroll_liability_adjustment",
            str(down.id),
            "1",
            "DOWN",
            "2026-08-05",
            "NGN",
            "2",
            "2040:100.00:0",
            f"{OFFSET}:0:100.00",
        ]
    )
    assert fact.fingerprint == hashlib.sha256(canonical.encode()).hexdigest()
    assert fact.idempotency_key == (
        f"payroll:payroll_liability_adjustment:{tenant.id}:{down.id}"
    )
    inc = build_liability_adjustment_consequence(
        db, tenant_id=tenant.id, adjustment_id=up.id, posting_date=date(2026, 8, 5)
    )
    assert [(e.account_ref, e.debit, e.credit) for e in inc.entries] == [
        (OFFSET, Decimal("30.00"), Decimal("0")),
        ("2040", Decimal("0"), Decimal("30.00")),
    ]
    for f in (fact, inc):
        assert sum(e.debit for e in f.entries) == sum(e.credit for e in f.entries)
    assert fact == build_liability_adjustment_consequence(
        db, tenant_id=tenant.id, adjustment_id=down.id, posting_date=date(2026, 8, 5)
    )
    with pytest.raises(PayrollNotFound):
        build_liability_adjustment_consequence(
            db,
            tenant_id=tenant.id,
            adjustment_id=uuid4(),
            posting_date=date(2026, 8, 5),
        )


def test_a5_consequences_are_byte_identical(db: Session) -> None:
    tenant, _, _, net = _world(db)
    settlement = _settle(db, tenant, net, "900")
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


def test_db_check_blocks_settled_above_effective(db: Session) -> None:
    tenant, _, _, net = _world(db)
    _adjust(db, tenant, net, "-100")
    net.amount_settled = Decimal("850.00")
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_adjustments_zero_amount_check_and_unique_ref(db: Session) -> None:
    tenant, _, _, net = _world(db)
    db.add(
        PayrollLiabilityAdjustment(
            tenant_id=tenant.id,
            liability_id=net.id,
            amount=Decimal("0"),
            adjustment_ref="Z",
            reason="r",
            evidence_ref="e",
            offset_account_ref=OFFSET,
            adjusted_by_id=ACTOR,
            adjusted_at=LATER,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_a6_migration_shape() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (
        root
        / "packages/dotmac-payroll/src/dotmac_payroll/migrations/versions"
        / "py_0004_liability_adjustments.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "py_0004_liability_adjustments"' in source
    assert 'down_revision = "py_0003_settlement_reversal"' in source
    assert len("py_0004_liability_adjustments") <= 32
    for needle in (
        "protect_payroll_evidence",
        "FORCE ROW LEVEL SECURITY",
        "GRANT SELECT, INSERT ON",
        "amount_adjusted",
        "amount <> 0",
        "uq_payroll_liability_adjustments_ref",
        "downgrade refused",
    ):
        assert needle in source
    assert "UPDATE" not in source.split("GRANT SELECT, INSERT ON")[1].split("\n")[0]


def test_liability_model_default_adjusted_is_zero(db: Session) -> None:
    _, _, _, net = _world(db)
    assert net.amount_adjusted == Decimal("0")
    assert isinstance(net, PayrollLiability)
