"""Behavior of data-driven payroll calculations and employee liabilities."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Currency, Money
from dotmac_payroll import (
    EmployeeComponentInput,
    PayComponentInput,
    PayrollConflict,
    PayrollRuleViolation,
    PayRuleInput,
    approve_payroll_run,
    assign_employee_pay_structure,
    calculate_employee_payroll,
    create_pay_component,
    create_pay_structure,
    create_payroll_run,
    finalize_payroll_run,
    publish_pay_structure_revision,
    record_liability_settlement,
    update_pay_component,
)
from dotmac_payroll.models import (
    TENANT_MODELS,
    PayrollCalculationLine,
    PayrollLiability,
)
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

NGN = Currency("NGN", 2)
NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)


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


def _tenant(db: Session) -> Tenant:
    tenant = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(tenant)
    db.flush()
    return tenant


def _configured_payroll(db: Session, tenant_id):
    base = create_pay_component(
        db,
        tenant_id=tenant_id,
        command=PayComponentInput(
            component_code="TENANT-BASE",
            name="Configured base pay",
            kind="earning",
            expense_account_ref="ledger:salary-expense",
            liability_account_ref=None,
            liability_destination_ref=None,
        ),
    )
    allowance = create_pay_component(
        db,
        tenant_id=tenant_id,
        command=PayComponentInput(
            component_code="TENANT-ALLOWANCE",
            name="Configured allowance",
            kind="earning",
            expense_account_ref="ledger:allowance-expense",
            liability_account_ref=None,
            liability_destination_ref=None,
        ),
    )
    deduction = create_pay_component(
        db,
        tenant_id=tenant_id,
        command=PayComponentInput(
            component_code="TENANT-DEDUCTION",
            name="Configured deduction",
            kind="deduction",
            expense_account_ref=None,
            liability_account_ref="ledger:deduction-payable",
            liability_destination_ref="authority:configured",
        ),
    )
    structure = create_pay_structure(
        db,
        tenant_id=tenant_id,
        code="MONTHLY-CONFIGURED",
        name="Configured monthly structure",
        currency=NGN,
    )
    revision = publish_pay_structure_revision(
        db,
        tenant_id=tenant_id,
        structure_id=structure.id,
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        net_payable_account_ref="ledger:employee-payable",
        rules=(
            PayRuleInput(
                component_id=base.id,
                sequence=1,
                calculation_method="input",
                fixed_amount=None,
                rate=None,
                basis_component_ids=(),
                prorates=True,
            ),
            PayRuleInput(
                component_id=allowance.id,
                sequence=2,
                calculation_method="percentage",
                fixed_amount=None,
                rate=Decimal("0.10"),
                basis_component_ids=(base.id,),
                prorates=False,
            ),
            PayRuleInput(
                component_id=deduction.id,
                sequence=3,
                calculation_method="input",
                fixed_amount=None,
                rate=None,
                basis_component_ids=(),
                prorates=False,
            ),
        ),
        published_by_id=uuid4(),
        published_at=NOW,
    )
    assignment = assign_employee_pay_structure(
        db,
        tenant_id=tenant_id,
        employee_ref="employee:opaque-1",
        revision_id=revision.id,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        source_ref="people-assignment:1",
        source_version="4",
    )
    return base, allowance, deduction, structure, revision, assignment


def test_pay_components_are_tenant_crud_not_a_fixed_enum(db: Session) -> None:
    tenant = _tenant(db)
    base, *_ = _configured_payroll(db, tenant.id)
    update_pay_component(
        db,
        tenant_id=tenant.id,
        component_id=base.id,
        name="Renamed configured base",
        expense_account_ref="ledger:renamed-expense",
    )
    assert base.name == "Renamed configured base"
    assert base.expense_account_ref == "ledger:renamed-expense"


def test_calculation_uses_typed_rules_and_snapshots_external_tax_input(
    db: Session,
) -> None:
    tenant = _tenant(db)
    base, allowance, deduction, *_ = _configured_payroll(db, tenant.id)
    creator = uuid4()
    run = create_payroll_run(
        db,
        tenant_id=tenant.id,
        run_ref="PAYRUN-2026-07",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        currency=NGN,
        created_by_id=creator,
        created_at=NOW,
    )
    calculation = calculate_employee_payroll(
        db,
        tenant_id=tenant.id,
        run_id=run.id,
        employee_ref="employee:opaque-1",
        proration_factor=Decimal("0.5"),
        inputs=(
            EmployeeComponentInput(
                component_id=base.id,
                amount=Money.of("1000", NGN),
                evidence_ref="compensation:1",
            ),
            EmployeeComponentInput(
                component_id=deduction.id,
                amount=Money.of("50", NGN),
                evidence_ref="tax-determination:1",
            ),
        ),
        calculated_at=NOW,
    )

    assert calculation.gross_amount == Decimal("600.00")
    assert calculation.deduction_amount == Decimal("50.00")
    assert calculation.net_amount == Decimal("550.00")
    lines = db.scalars(
        select(PayrollCalculationLine)
        .where(PayrollCalculationLine.calculation_id == calculation.id)
        .order_by(PayrollCalculationLine.sequence)
    ).all()
    assert [(line.component_code, line.amount) for line in lines] == [
        (base.component_code, Decimal("500.00")),
        (allowance.component_code, Decimal("100.00")),
        (deduction.component_code, Decimal("50.00")),
    ]
    assert lines[-1].evidence_ref == "tax-determination:1"


def test_deductions_above_gross_fail_closed_instead_of_rewriting_rules(
    db: Session,
) -> None:
    tenant = _tenant(db)
    base, _, deduction, *_ = _configured_payroll(db, tenant.id)
    run = create_payroll_run(
        db,
        tenant_id=tenant.id,
        run_ref="PAYRUN-OVER",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        currency=NGN,
        created_by_id=uuid4(),
        created_at=NOW,
    )
    with pytest.raises(PayrollRuleViolation, match="deductions exceed gross"):
        calculate_employee_payroll(
            db,
            tenant_id=tenant.id,
            run_id=run.id,
            employee_ref="employee:opaque-1",
            proration_factor=Decimal("1"),
            inputs=(
                EmployeeComponentInput(
                    component_id=base.id,
                    amount=Money.of("100", NGN),
                    evidence_ref="compensation:1",
                ),
                EmployeeComponentInput(
                    component_id=deduction.id,
                    amount=Money.of("200", NGN),
                    evidence_ref="tax-determination:2",
                ),
            ),
            calculated_at=NOW,
        )


def test_finalization_creates_employee_and_external_liabilities_and_settles_partially(
    db: Session,
) -> None:
    tenant = _tenant(db)
    base, _, deduction, *_ = _configured_payroll(db, tenant.id)
    creator, approver, finalizer = uuid4(), uuid4(), uuid4()
    run = create_payroll_run(
        db,
        tenant_id=tenant.id,
        run_ref="PAYRUN-LIAB",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        currency=NGN,
        created_by_id=creator,
        created_at=NOW,
    )
    calculate_employee_payroll(
        db,
        tenant_id=tenant.id,
        run_id=run.id,
        employee_ref="employee:opaque-1",
        proration_factor=Decimal("1"),
        inputs=(
            EmployeeComponentInput(
                component_id=base.id,
                amount=Money.of("1000", NGN),
                evidence_ref="compensation:1",
            ),
            EmployeeComponentInput(
                component_id=deduction.id,
                amount=Money.of("100", NGN),
                evidence_ref="tax-determination:3",
            ),
        ),
        calculated_at=NOW,
    )
    with pytest.raises(PayrollRuleViolation, match="creator cannot approve"):
        approve_payroll_run(
            db,
            tenant_id=tenant.id,
            run_id=run.id,
            approved_by_id=creator,
            approved_at=NOW,
        )
    approve_payroll_run(
        db, tenant_id=tenant.id, run_id=run.id, approved_by_id=approver, approved_at=NOW
    )
    finalize_payroll_run(
        db,
        tenant_id=tenant.id,
        run_id=run.id,
        finalized_by_id=finalizer,
        finalized_at=NOW,
    )

    liabilities = db.scalars(
        select(PayrollLiability)
        .where(PayrollLiability.run_id == run.id)
        .order_by(PayrollLiability.beneficiary_type)
    ).all()
    assert [(row.beneficiary_type, row.amount) for row in liabilities] == [
        ("employee", Decimal("1000.00")),
        ("external", Decimal("100.00")),
    ]
    employee_liability = liabilities[0]
    settlement = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=employee_liability.id,
        amount=Money.of("400", NGN),
        settlement_ref="payment:1",
        evidence_ref="bank-observation:1",
        recorded_by_id=uuid4(),
        recorded_at=NOW,
    )
    assert employee_liability.amount_settled == Decimal("400.00")
    assert employee_liability.status == "partially_settled"
    replay = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=employee_liability.id,
        amount=Money.of("400", NGN),
        settlement_ref="payment:1",
        evidence_ref="bank-observation:1",
        recorded_by_id=uuid4(),
        recorded_at=NOW,
    )
    assert replay.id == settlement.id
    assert employee_liability.amount_settled == Decimal("400.00")

    with pytest.raises(PayrollConflict, match="different evidence"):
        record_liability_settlement(
            db,
            tenant_id=tenant.id,
            liability_id=employee_liability.id,
            amount=Money.of("399", NGN),
            settlement_ref="payment:1",
            evidence_ref="bank-observation:changed",
            recorded_by_id=uuid4(),
            recorded_at=NOW,
        )
