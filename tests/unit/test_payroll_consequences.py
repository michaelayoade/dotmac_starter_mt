"""GL consequence facts for finalized payroll runs and settlements."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Money
from dotmac_payroll import (
    EmployeeComponentInput,
    PayrollNotFound,
    PayrollRuleViolation,
    approve_payroll_run,
    build_liability_settlement_consequence,
    build_run_accounting_consequence,
    calculate_employee_payroll,
    create_payroll_run,
    finalize_payroll_run,
    record_liability_settlement,
)
from dotmac_payroll.models import (
    TENANT_MODELS,
    PayrollCalculationLine,
    PayrollLiability,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.unit.test_payroll_service import (
    NGN,
    NOW,
    _configured_payroll,
    _tenant,
)

VALUE_DATE = date(2026, 9, 1)


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


def _finalized(db: Session, tenant_id, *, finalize: bool = True):
    base, _, deduction, _, revision, _ = _configured_payroll(db, tenant_id)
    run = create_payroll_run(
        db,
        tenant_id=tenant_id,
        run_ref=f"RUN-{uuid4().hex[:6]}",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        currency=NGN,
        created_by_id=uuid4(),
        created_at=NOW,
    )
    calculate_employee_payroll(
        db,
        tenant_id=tenant_id,
        run_id=run.id,
        employee_ref="employee:opaque-1",
        proration_factor=Decimal("1"),
        inputs=(
            EmployeeComponentInput(base.id, Money.of("1000", NGN), "comp:1"),
            EmployeeComponentInput(deduction.id, Money.of("100", NGN), "tax:1"),
        ),
        calculated_at=NOW,
    )
    if finalize:
        approve_payroll_run(
            db,
            tenant_id=tenant_id,
            run_id=run.id,
            approved_by_id=uuid4(),
            approved_at=NOW,
        )
        finalize_payroll_run(
            db,
            tenant_id=tenant_id,
            run_id=run.id,
            finalized_by_id=uuid4(),
            finalized_at=NOW,
        )
    return run


def test_run_fact_is_balanced_idempotent_and_uses_configured_refs(db: Session) -> None:
    tenant = _tenant(db)
    run = _finalized(db, tenant.id)
    fact = build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    # 10% allowance on 1000 base: gross 1100, deduction 100, net 1000.
    debits = {x.account_ref: x.debit for x in fact.entries if x.debit}
    credits = {x.account_ref: x.credit for x in fact.entries if x.credit}
    assert debits == {
        "ledger:salary-expense": Decimal("1000.00"),
        "ledger:allowance-expense": Decimal("100.00"),
    }
    assert credits == {
        "ledger:deduction-payable": Decimal("100.00"),
        "ledger:employee-payable": Decimal("1000.00"),
    }
    assert sum(debits.values()) == sum(credits.values()) == Decimal("1100.00")
    assert fact.posting_date == date(2026, 7, 31)
    assert fact.description == run.run_ref
    assert fact.source.owner == "payroll"
    assert fact.source.document_kind == "payroll_run"
    assert fact.source.document_id == str(run.id)
    assert fact.source.version == "1"
    assert fact.source.fingerprint == fact.fingerprint
    assert not hasattr(fact, "exchange_rate")
    again = build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    assert again == fact
    assert fact.idempotency_key == f"payroll:payroll_run:{tenant.id}:{run.id}"
    assert len(fact.fingerprint) == 64


def test_run_fact_refuses_unfinalized_wrong_tenant_and_missing_run(db: Session) -> None:
    tenant = _tenant(db)
    run = _finalized(db, tenant.id, finalize=False)
    with pytest.raises(PayrollRuleViolation, match="only a finalized"):
        build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    other = _tenant(db)
    with pytest.raises(PayrollNotFound):
        build_run_accounting_consequence(db, tenant_id=other.id, run_id=run.id)
    with pytest.raises(PayrollNotFound):
        build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=uuid4())


def test_run_fact_refuses_missing_account_and_drift(db: Session) -> None:
    tenant = _tenant(db)
    run = _finalized(db, tenant.id)
    line = db.scalars(
        select(PayrollCalculationLine).where(
            PayrollCalculationLine.component_kind == "earning",
            PayrollCalculationLine.component_code == "TENANT-BASE",
        )
    ).one()
    line.expense_account_ref = " "
    db.flush()
    with pytest.raises(PayrollRuleViolation, match="expense account"):
        build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    line.expense_account_ref = "ledger:salary-expense"
    line.amount = Decimal("999.00")
    db.flush()
    with pytest.raises(PayrollRuleViolation, match="drift"):
        build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    line.amount = Decimal("1000.00")
    liability = db.scalars(
        select(PayrollLiability).where(
            PayrollLiability.liability_key == "employee-net-pay"
        )
    ).one()
    liability.amount = Decimal("1.00")
    db.flush()
    with pytest.raises(PayrollRuleViolation, match="net-pay liability drifts"):
        build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)


def test_settlement_fact_covers_net_pay_and_statutory_remittance(db: Session) -> None:
    tenant = _tenant(db)
    run = _finalized(db, tenant.id)
    liabilities = {
        row.liability_key: row
        for row in db.scalars(
            select(PayrollLiability).where(PayrollLiability.run_id == run.id)
        )
    }
    net = liabilities["employee-net-pay"]
    s1 = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=net.id,
        amount=Money.of("400", NGN),
        settlement_ref="pay:1",
        evidence_ref="bank:1",
        recorded_by_id=uuid4(),
        recorded_at=NOW,
    )
    fact = build_liability_settlement_consequence(
        db,
        tenant_id=tenant.id,
        settlement_id=s1.id,
        bank_account_ref="ledger:bank",
        posting_date=VALUE_DATE,
    )
    assert [(x.account_ref, x.debit, x.credit) for x in fact.entries] == [
        ("ledger:employee-payable", Decimal("400.00"), Decimal("0")),
        ("ledger:bank", Decimal("0"), Decimal("400.00")),
    ]
    assert fact.posting_date == VALUE_DATE != NOW.date()
    assert fact.source.document_kind == "payroll_liability_settlement"
    assert fact.description == "pay:1"
    moved = build_liability_settlement_consequence(
        db,
        tenant_id=tenant.id,
        settlement_id=s1.id,
        bank_account_ref="ledger:bank",
        posting_date=date(2026, 9, 2),
    )
    assert moved.fingerprint != fact.fingerprint
    paye = next(v for k, v in liabilities.items() if k.startswith("component:"))
    s2 = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=paye.id,
        amount=Money.of("100", NGN),
        settlement_ref="remit:1",
        evidence_ref="receipt:1",
        recorded_by_id=uuid4(),
        recorded_at=NOW,
    )
    f2 = build_liability_settlement_consequence(
        db,
        tenant_id=tenant.id,
        settlement_id=s2.id,
        bank_account_ref="ledger:bank",
        posting_date=VALUE_DATE,
    )
    assert f2.entries[0].account_ref == "ledger:deduction-payable"
    assert f2.idempotency_key != fact.idempotency_key
    assert fact == build_liability_settlement_consequence(
        db,
        tenant_id=tenant.id,
        settlement_id=s1.id,
        bank_account_ref="ledger:bank",
        posting_date=VALUE_DATE,
    )


def test_settlement_fact_refusals(db: Session) -> None:
    tenant = _tenant(db)
    run = _finalized(db, tenant.id)
    net = db.scalars(
        select(PayrollLiability).where(
            PayrollLiability.run_id == run.id,
            PayrollLiability.liability_key == "employee-net-pay",
        )
    ).one()
    s = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=net.id,
        amount=Money.of("1", NGN),
        settlement_ref="pay:1",
        evidence_ref="bank:1",
        recorded_by_id=uuid4(),
        recorded_at=NOW,
    )
    with pytest.raises(PayrollRuleViolation, match="bank account reference"):
        build_liability_settlement_consequence(
            db,
            tenant_id=tenant.id,
            settlement_id=s.id,
            bank_account_ref=" ",
            posting_date=VALUE_DATE,
        )
    with pytest.raises(PayrollRuleViolation, match="must differ"):
        build_liability_settlement_consequence(
            db,
            tenant_id=tenant.id,
            settlement_id=s.id,
            bank_account_ref="ledger:employee-payable",
            posting_date=VALUE_DATE,
        )
    other = _tenant(db)
    with pytest.raises(PayrollNotFound):
        build_liability_settlement_consequence(
            db,
            tenant_id=other.id,
            settlement_id=s.id,
            bank_account_ref="ledger:bank",
            posting_date=VALUE_DATE,
        )
    with pytest.raises(PayrollNotFound):
        build_liability_settlement_consequence(
            db,
            tenant_id=tenant.id,
            settlement_id=uuid4(),
            bank_account_ref="ledger:bank",
            posting_date=VALUE_DATE,
        )


def test_component_liability_drift_is_refused(db: Session) -> None:
    tenant = _tenant(db)
    run = _finalized(db, tenant.id)
    component = db.scalars(
        select(PayrollLiability).where(
            PayrollLiability.run_id == run.id,
            PayrollLiability.liability_key != "employee-net-pay",
        )
    ).one()
    original = component.liability_account_ref
    component.liability_account_ref = "ledger:other-payable"
    db.flush()
    with pytest.raises(PayrollRuleViolation, match="component liabilities drift"):
        build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    component.liability_account_ref = original
    component.amount = Decimal("99.00")
    db.flush()
    with pytest.raises(PayrollRuleViolation, match="component liabilities drift"):
        build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    db.delete(component)
    db.flush()
    with pytest.raises(PayrollRuleViolation, match="component liabilities drift"):
        build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
