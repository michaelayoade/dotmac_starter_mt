"""a4: deduction settlement mode, run cancellation and mid-month leavers."""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.money import Money
from dotmac_payroll import (
    EmployeeComponentInput,
    PayComponentInput,
    PayrollConflict,
    PayrollNotFound,
    PayrollRuleViolation,
    PayRuleInput,
    approve_payroll_run,
    assign_employee_pay_structure,
    build_run_accounting_consequence,
    calculate_employee_payroll,
    cancel_payroll_run,
    create_pay_component,
    create_pay_structure,
    create_payroll_run,
    finalize_payroll_run,
    publish_pay_structure_revision,
    record_liability_settlement,
)
from dotmac_payroll.models import TENANT_MODELS, PayrollLiability
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.unit.test_payroll_service import NGN, NOW, _tenant

EMP = "employee:opaque-a4"
LOAN_ASSET = "1410"  # documentation-style ref for a staff-loan asset account


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


def _rule(component_id: UUID, sequence: int, **kw) -> PayRuleInput:
    return PayRuleInput(
        component_id=component_id,
        sequence=sequence,
        calculation_method="input",
        fixed_amount=None,
        rate=None,
        basis_component_ids=(),
        prorates=False,
        **kw,
    )


def _setup(
    db: Session,
    tenant_id: UUID,
    *,
    loan_mode: str | None,
    assignment_to: date | None = None,
    revision_to: date | None = None,
):
    """Salary + tax deduction (+ optional loan deduction in the given mode)."""
    base = create_pay_component(
        db,
        tenant_id=tenant_id,
        command=PayComponentInput(
            "BASE", "Base", "earning", "ledger:salary", None, None
        ),
    )
    tax = create_pay_component(
        db,
        tenant_id=tenant_id,
        command=PayComponentInput(
            "TAX", "Tax", "deduction", None, "ledger:tax-payable", "authority:x"
        ),
    )
    rules = [_rule(base.id, 1), _rule(tax.id, 2)]
    loan = None
    if loan_mode is not None:
        loan = create_pay_component(
            db,
            tenant_id=tenant_id,
            command=PayComponentInput(
                "LOAN", "Staff loan", "deduction", None, LOAN_ASSET, "loan:desk"
            ),
        )
        rules.append(_rule(loan.id, 3, settlement_mode=loan_mode))
    structure = create_pay_structure(
        db, tenant_id=tenant_id, code="S", name="S", currency=NGN
    )
    revision = publish_pay_structure_revision(
        db,
        tenant_id=tenant_id,
        structure_id=structure.id,
        version=1,
        effective_from=date(2026, 1, 1),
        effective_to=revision_to,
        net_payable_account_ref="2040",
        rules=tuple(rules),
        published_by_id=uuid4(),
        published_at=NOW,
    )
    assign_employee_pay_structure(
        db,
        tenant_id=tenant_id,
        employee_ref=EMP,
        revision_id=revision.id,
        effective_from=date(2026, 1, 1),
        effective_to=assignment_to,
        source_ref="people:1",
        source_version="1",
    )
    return base, tax, loan


def _run(db: Session, tenant_id: UUID, ref: str = "RUN-A4", creator=None):
    return create_payroll_run(
        db,
        tenant_id=tenant_id,
        run_ref=ref,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
        currency=NGN,
        created_by_id=creator or uuid4(),
        created_at=NOW,
    )


def _calc(db, tenant_id, run, base, tax, loan=None, loan_amount="200"):
    inputs = [
        EmployeeComponentInput(base.id, Money.of("1000", NGN), "comp:1"),
        EmployeeComponentInput(tax.id, Money.of("100", NGN), "tax:1"),
    ]
    if loan is not None:
        inputs.append(
            EmployeeComponentInput(loan.id, Money.of(loan_amount, NGN), "loan:1")
        )
    return calculate_employee_payroll(
        db,
        tenant_id=tenant_id,
        run_id=run.id,
        employee_ref=EMP,
        proration_factor=Decimal("1"),
        inputs=tuple(inputs),
        calculated_at=NOW,
    )


def _finalize(db, tenant_id, run):
    approve_payroll_run(
        db, tenant_id=tenant_id, run_id=run.id, approved_by_id=uuid4(), approved_at=NOW
    )
    finalize_payroll_run(
        db,
        tenant_id=tenant_id,
        run_id=run.id,
        finalized_by_id=uuid4(),
        finalized_at=NOW,
    )


def _liabilities(db, run):
    return db.scalars(
        select(PayrollLiability)
        .where(PayrollLiability.run_id == run.id)
        .order_by(PayrollLiability.liability_key)
    ).all()


# ---- A: settlement mode -------------------------------------------------


def test_external_path_is_unchanged_and_fingerprint_matches_a3_formula(
    db: Session,
) -> None:
    """Default (external) behaviour is identical to a3, down to the fact hash."""
    tenant = _tenant(db)
    base, tax, _ = _setup(db, tenant.id, loan_mode=None)
    run = _run(db, tenant.id)
    calc = _calc(db, tenant.id, run, base, tax)
    assert calc.net_amount == Decimal("900.00")
    _finalize(db, tenant.id, run)

    rows = {x.liability_key: x for x in _liabilities(db, run)}
    assert set(rows) == {"employee-net-pay", f"component:{tax.id}"}
    ext = rows[f"component:{tax.id}"]
    assert (ext.beneficiary_type, ext.status, ext.amount_settled) == (
        "external",
        "outstanding",
        Decimal("0"),
    )
    assert ext.beneficiary_ref == "authority:x"
    assert ext.settlement_mode == "external"
    net = rows["employee-net-pay"]
    assert (net.beneficiary_type, net.status) == ("employee", "outstanding")

    fact = build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    assert [(e.account_ref, e.debit, e.credit) for e in fact.entries] == [
        ("ledger:salary", Decimal("1000.00"), Decimal("0")),
        ("2040", Decimal("0"), Decimal("900.00")),
        ("ledger:tax-payable", Decimal("0"), Decimal("100.00")),
    ]
    # Independent re-computation of the a3 canonical form.
    canonical = "|".join(
        [
            str(tenant.id),
            "payroll",
            "payroll_run",
            str(run.id),
            "1",
            "RUN-A4",
            "2026-07-31",
            "NGN",
            "2",
            "ledger:salary:1000.00:0",
            "2040:0:900.00",
            "ledger:tax-payable:0:100.00",
        ]
    )
    assert fact.fingerprint == hashlib.sha256(canonical.encode()).hexdigest()
    # An external liability is still settled the a3 way.
    settlement = record_liability_settlement(
        db,
        tenant_id=tenant.id,
        liability_id=ext.id,
        amount=Money.of("100", NGN),
        settlement_ref="S-1",
        evidence_ref="bank:1",
        recorded_by_id=uuid4(),
        recorded_at=NOW,
    )
    assert settlement.amount == Decimal("100.00")
    assert ext.status == "settled"


def test_internal_recovery_is_booked_recovered_never_an_external_payable(
    db: Session,
) -> None:
    tenant = _tenant(db)
    base, tax, loan = _setup(db, tenant.id, loan_mode="internal_recovery")
    run = _run(db, tenant.id)
    calc = _calc(db, tenant.id, run, base, tax, loan)
    assert calc.net_amount == Decimal("700.00")
    _finalize(db, tenant.id, run)

    rows = {x.liability_key: x for x in _liabilities(db, run)}
    recovery = rows[f"component:{loan.id}"]
    assert recovery.settlement_mode == "internal_recovery"
    assert recovery.beneficiary_type == "employee"
    assert recovery.beneficiary_ref == EMP
    assert recovery.liability_account_ref == LOAN_ASSET
    assert recovery.status == "settled"
    assert recovery.amount_settled == recovery.amount == Decimal("200.00")
    # No outstanding external payable for the loan; tax stays external.
    outstanding_external = [
        x
        for x in rows.values()
        if x.beneficiary_type == "external" and x.status != "settled"
    ]
    assert [x.component_id for x in outstanding_external] == [tax.id]

    # Consequence still credits the asset ref (Cr 1410), and balances.
    fact = build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)
    credits = {e.account_ref: e.credit for e in fact.entries if e.credit}
    assert credits[LOAN_ASSET] == Decimal("200.00")
    assert credits["2040"] == Decimal("700.00")
    assert sum(e.debit for e in fact.entries) == sum(e.credit for e in fact.entries)


def test_record_liability_settlement_refuses_internal_recovery(db: Session) -> None:
    tenant = _tenant(db)
    base, tax, loan = _setup(db, tenant.id, loan_mode="internal_recovery")
    run = _run(db, tenant.id)
    _calc(db, tenant.id, run, base, tax, loan)
    _finalize(db, tenant.id, run)
    recovery = next(
        x for x in _liabilities(db, run) if x.settlement_mode == "internal_recovery"
    )
    with pytest.raises(PayrollRuleViolation, match="internal recovery"):
        record_liability_settlement(
            db,
            tenant_id=tenant.id,
            liability_id=recovery.id,
            amount=Money.of("1", NGN),
            settlement_ref="S-X",
            evidence_ref="bank:x",
            recorded_by_id=uuid4(),
            recorded_at=NOW,
        )
    assert recovery.amount_settled == Decimal("200.00")


def test_consequence_refuses_internal_recovery_drift(db: Session) -> None:
    tenant = _tenant(db)
    base, tax, loan = _setup(db, tenant.id, loan_mode="internal_recovery")
    run = _run(db, tenant.id)
    _calc(db, tenant.id, run, base, tax, loan)
    _finalize(db, tenant.id, run)
    recovery = next(
        x for x in _liabilities(db, run) if x.settlement_mode == "internal_recovery"
    )
    recovery.amount_settled = Decimal("0")
    db.flush()
    with pytest.raises(PayrollRuleViolation, match="internal recovery"):
        build_run_accounting_consequence(db, tenant_id=tenant.id, run_id=run.id)


def test_settlement_mode_validation(db: Session) -> None:
    tenant = _tenant(db)
    base = create_pay_component(
        db,
        tenant_id=tenant.id,
        command=PayComponentInput(
            "BASE", "Base", "earning", "ledger:salary", None, None
        ),
    )
    structure = create_pay_structure(
        db, tenant_id=tenant.id, code="S", name="S", currency=NGN
    )

    def publish(rule: PayRuleInput, version: int):
        return publish_pay_structure_revision(
            db,
            tenant_id=tenant.id,
            structure_id=structure.id,
            version=version,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            net_payable_account_ref="2040",
            rules=(rule,),
            published_by_id=uuid4(),
            published_at=NOW,
        )

    with pytest.raises(PayrollRuleViolation, match="only to deduction"):
        publish(_rule(base.id, 1, settlement_mode="internal_recovery"), 1)
    with pytest.raises(PayrollRuleViolation, match="unknown deduction settlement"):
        publish(_rule(base.id, 1, settlement_mode="netting"), 2)


def test_default_rule_and_line_mode_is_external(db: Session) -> None:
    tenant = _tenant(db)
    base, tax, _ = _setup(db, tenant.id, loan_mode=None)
    assert PayRuleInput(base.id, 1, "input", None, None, (), False).settlement_mode == (
        "external"
    )
    run = _run(db, tenant.id)
    calc = _calc(db, tenant.id, run, base, tax)
    from dotmac_payroll.models import PayrollCalculationLine

    modes = db.scalars(
        select(PayrollCalculationLine.settlement_mode).where(
            PayrollCalculationLine.calculation_id == calc.id
        )
    ).all()
    assert modes == ["external", "external"]


# ---- B: cancel ----------------------------------------------------------


def test_cancel_draft_and_approved_with_actor_reason_and_idempotency(
    db: Session,
) -> None:
    tenant = _tenant(db)
    base, tax, _ = _setup(db, tenant.id, loan_mode=None)
    actor = uuid4()
    draft = _run(db, tenant.id, "RUN-D")
    cancelled = cancel_payroll_run(
        db,
        tenant_id=tenant.id,
        run_id=draft.id,
        cancelled_by_id=actor,
        reason="  wrong period  ",
        cancelled_at=NOW,
    )
    assert cancelled.status == "cancelled"
    assert cancelled.cancelled_by_id == actor
    assert cancelled.cancelled_at is not None
    assert cancelled.cancel_reason == "wrong period"
    # Idempotent replay (same actor and reason) is a no-op.
    again = cancel_payroll_run(
        db,
        tenant_id=tenant.id,
        run_id=draft.id,
        cancelled_by_id=actor,
        reason="wrong period",
        cancelled_at=NOW,
    )
    assert again is cancelled
    with pytest.raises(PayrollConflict, match="differently"):
        cancel_payroll_run(
            db,
            tenant_id=tenant.id,
            run_id=draft.id,
            cancelled_by_id=actor,
            reason="another reason",
            cancelled_at=NOW,
        )
    # A cancelled run can no longer move.
    with pytest.raises(PayrollConflict):
        approve_payroll_run(
            db,
            tenant_id=tenant.id,
            run_id=draft.id,
            approved_by_id=uuid4(),
            approved_at=NOW,
        )

    approved = _run(db, tenant.id, "RUN-AP")
    _calc(db, tenant.id, approved, base, tax)
    approve_payroll_run(
        db,
        tenant_id=tenant.id,
        run_id=approved.id,
        approved_by_id=uuid4(),
        approved_at=NOW,
    )
    assert (
        cancel_payroll_run(
            db,
            tenant_id=tenant.id,
            run_id=approved.id,
            cancelled_by_id=actor,
            reason="payroll data error",
            cancelled_at=NOW,
        ).status
        == "cancelled"
    )
    with pytest.raises(PayrollConflict):
        finalize_payroll_run(
            db,
            tenant_id=tenant.id,
            run_id=approved.id,
            finalized_by_id=uuid4(),
            finalized_at=NOW,
        )


def test_cancel_refused_when_finalized_blank_reason_or_wrong_tenant(
    db: Session,
) -> None:
    tenant = _tenant(db)
    base, tax, _ = _setup(db, tenant.id, loan_mode=None)
    run = _run(db, tenant.id)
    _calc(db, tenant.id, run, base, tax)
    with pytest.raises(PayrollRuleViolation, match="cancel reason"):
        cancel_payroll_run(
            db,
            tenant_id=tenant.id,
            run_id=run.id,
            cancelled_by_id=uuid4(),
            reason="   ",
            cancelled_at=NOW,
        )
    other = _tenant(db)
    with pytest.raises(PayrollNotFound):
        cancel_payroll_run(
            db,
            tenant_id=other.id,
            run_id=run.id,
            cancelled_by_id=uuid4(),
            reason="x",
            cancelled_at=NOW,
        )
    _finalize(db, tenant.id, run)
    with pytest.raises(PayrollConflict, match="draft or approved"):
        cancel_payroll_run(
            db,
            tenant_id=tenant.id,
            run_id=run.id,
            cancelled_by_id=uuid4(),
            reason="too late",
            cancelled_at=NOW,
        )
    assert run.status == "finalized"
    assert run.cancelled_by_id is None


# ---- C: mid-month leavers ----------------------------------------------


def test_mid_month_leaver_uses_min_of_period_end_and_assignment_end(
    db: Session,
) -> None:
    tenant = _tenant(db)
    base, tax, _ = _setup(
        db,
        tenant.id,
        loan_mode=None,
        assignment_to=date(2026, 7, 15),
        revision_to=date(2026, 7, 15),
    )
    run = _run(db, tenant.id)
    calc = _calc(db, tenant.id, run, base, tax)
    assert calc.net_amount == Decimal("900.00")


def test_leaver_who_left_before_the_period_still_has_no_assignment(
    db: Session,
) -> None:
    tenant = _tenant(db)
    base, tax, _ = _setup(
        db, tenant.id, loan_mode=None, assignment_to=date(2026, 6, 30)
    )
    run = _run(db, tenant.id)
    with pytest.raises(PayrollNotFound, match="no active pay assignment"):
        _calc(db, tenant.id, run, base, tax)


def test_leaver_with_unexpired_revision_check_unchanged_for_stayers(
    db: Session,
) -> None:
    # Assignment open-ended but revision ended mid-month: still refused as a3.
    tenant = _tenant(db)
    base, tax, _ = _setup(
        db, tenant.id, loan_mode=None, assignment_to=None, revision_to=date(2026, 7, 15)
    )
    run = _run(db, tenant.id)
    with pytest.raises(PayrollRuleViolation, match="not effective"):
        _calc(db, tenant.id, run, base, tax)
