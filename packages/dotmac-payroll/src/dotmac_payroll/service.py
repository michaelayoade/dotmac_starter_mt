"""Flush-only owner for payroll configuration, calculations and liabilities."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from dotmac_kernel.money import Currency, Money
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from dotmac_payroll.contracts import (
    EmployeeComponentInput,
    PayComponentInput,
    PayRuleInput,
)
from dotmac_payroll.models import (
    SETTLEMENT_MODES,
    EmployeePayAssignment,
    PayComponent,
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollLiability,
    PayrollLiabilityAdjustment,
    PayrollLiabilitySettlement,
    PayrollRun,
    PayrollSettlementReversal,
    PayRuleBasis,
    PayStructure,
    PayStructureRevision,
    PayStructureRule,
)
from dotmac_payroll.totals import (
    PayrollAmountSnapshot,
    PayrollRunTotals,
    aggregate_payroll_run_totals,
)


class PayrollNotFound(LookupError):
    """A tenant-local payroll record does not exist."""


class PayrollConflict(ValueError):
    """A payroll identity or immutable calculation conflicts with existing data."""


class PayrollRuleViolation(ValueError):
    """Payroll configuration, calculation or lifecycle policy was violated."""


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise PayrollRuleViolation(f"{label} must not be blank")
    return cleaned


def _round(value: Decimal, minor_units: int) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-minor_units), rounding=ROUND_HALF_UP)


def _component(db: Session, tenant_id: UUID, component_id: UUID) -> PayComponent:
    row = db.scalar(
        select(PayComponent).where(
            PayComponent.tenant_id == tenant_id, PayComponent.id == component_id
        )
    )
    if row is None:
        raise PayrollNotFound("pay component not found")
    return row


def _structure(db: Session, tenant_id: UUID, structure_id: UUID) -> PayStructure:
    row = db.scalar(
        select(PayStructure).where(
            PayStructure.tenant_id == tenant_id, PayStructure.id == structure_id
        )
    )
    if row is None:
        raise PayrollNotFound("pay structure not found")
    return row


def create_pay_component(
    db: Session, *, tenant_id: UUID, command: PayComponentInput
) -> PayComponent:
    if command.kind not in {
        "earning",
        "deduction",
        "employer_liability",
        "information",
    }:
        raise PayrollRuleViolation("unknown pay component kind")
    if command.kind == "earning" and not command.expense_account_ref:
        raise PayrollRuleViolation(
            "earning component requires an expense account reference"
        )
    if command.kind in {"deduction", "employer_liability"}:
        if not command.liability_account_ref or not command.liability_destination_ref:
            raise PayrollRuleViolation(
                "liability component requires account and destination references"
            )
    code = _clean(command.component_code, "component code")
    existing = db.scalar(
        select(PayComponent).where(
            PayComponent.tenant_id == tenant_id,
            PayComponent.component_code == code,
        )
    )
    if existing is not None:
        raise PayrollConflict(f"pay component {code} already exists")
    row = PayComponent(
        tenant_id=tenant_id,
        component_code=code,
        name=_clean(command.name, "component name"),
        description=command.description.strip() if command.description else None,
        kind=command.kind,
        expense_account_ref=(
            command.expense_account_ref.strip() if command.expense_account_ref else None
        ),
        liability_account_ref=(
            command.liability_account_ref.strip()
            if command.liability_account_ref
            else None
        ),
        liability_destination_ref=(
            command.liability_destination_ref.strip()
            if command.liability_destination_ref
            else None
        ),
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def update_pay_component(
    db: Session,
    *,
    tenant_id: UUID,
    component_id: UUID,
    name: str,
    expense_account_ref: str | None,
) -> PayComponent:
    row = _component(db, tenant_id, component_id)
    if row.status != "active":
        raise PayrollConflict("retired pay component cannot be changed")
    row.name = _clean(name, "component name")
    row.expense_account_ref = (
        expense_account_ref.strip() if expense_account_ref else None
    )
    if row.kind == "earning" and row.expense_account_ref is None:
        raise PayrollRuleViolation(
            "earning component requires an expense account reference"
        )
    db.flush()
    return row


def retire_pay_component(
    db: Session, *, tenant_id: UUID, component_id: UUID
) -> PayComponent:
    row = _component(db, tenant_id, component_id)
    row.status = "retired"
    db.flush()
    return row


def create_pay_structure(
    db: Session,
    *,
    tenant_id: UUID,
    code: str,
    name: str,
    currency: Currency,
) -> PayStructure:
    row = PayStructure(
        tenant_id=tenant_id,
        code=_clean(code, "pay structure code"),
        name=_clean(name, "pay structure name"),
        currency_code=currency.code,
        minor_units=currency.minor_units,
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def publish_pay_structure_revision(
    db: Session,
    *,
    tenant_id: UUID,
    structure_id: UUID,
    version: int,
    effective_from: date,
    effective_to: date | None,
    net_payable_account_ref: str,
    rules: tuple[PayRuleInput, ...],
    published_by_id: UUID,
    published_at: datetime,
) -> PayStructureRevision:
    structure = _structure(db, tenant_id, structure_id)
    if structure.status != "active":
        raise PayrollConflict("pay structure is retired")
    if version <= 0:
        raise PayrollRuleViolation("pay structure version must be positive")
    if effective_to is not None and effective_to < effective_from:
        raise PayrollRuleViolation("pay structure effective end precedes its start")
    if not rules:
        raise PayrollRuleViolation("pay structure revision requires rules")
    sequences = [rule.sequence for rule in rules]
    component_ids = [rule.component_id for rule in rules]
    if len(set(sequences)) != len(sequences) or len(set(component_ids)) != len(
        component_ids
    ):
        raise PayrollRuleViolation(
            "pay structure rule sequences and components must be unique"
        )
    if sorted(sequences) != list(range(1, len(sequences) + 1)):
        raise PayrollRuleViolation("pay structure rule sequences must be contiguous")
    components = {
        item.id: item
        for item in db.scalars(
            select(PayComponent).where(
                PayComponent.tenant_id == tenant_id,
                PayComponent.id.in_(component_ids),
            )
        ).all()
    }
    if set(components) != set(component_ids):
        raise PayrollNotFound("one or more pay components were not found")
    if any(item.status != "active" for item in components.values()):
        raise PayrollConflict("retired pay component cannot enter a new revision")
    sequence_by_component = {rule.component_id: rule.sequence for rule in rules}
    for rule in rules:
        if rule.calculation_method not in {"input", "fixed", "percentage"}:
            raise PayrollRuleViolation("unknown payroll calculation method")
        if rule.settlement_mode not in SETTLEMENT_MODES:
            raise PayrollRuleViolation("unknown deduction settlement mode")
        if rule.settlement_mode == "internal_recovery":
            rule_component = components[rule.component_id]
            if rule_component.kind != "deduction":
                raise PayrollRuleViolation(
                    "internal recovery applies only to deduction components"
                )
            if not rule_component.liability_account_ref:
                raise PayrollRuleViolation(
                    "internal recovery requires an asset account reference"
                )
        if rule.calculation_method == "input":
            if (
                rule.fixed_amount is not None
                or rule.rate is not None
                or rule.basis_component_ids
            ):
                raise PayrollRuleViolation("input rule cannot declare a formula")
        elif rule.calculation_method == "fixed":
            if (
                rule.fixed_amount is None
                or rule.rate is not None
                or rule.basis_component_ids
            ):
                raise PayrollRuleViolation("fixed rule requires only a fixed amount")
            if (
                rule.fixed_amount.currency.code != structure.currency_code
                or rule.fixed_amount.currency.minor_units != structure.minor_units
            ):
                raise PayrollRuleViolation(
                    "fixed rule uses the wrong structure currency"
                )
        else:
            if rule.rate is None or rule.rate < 0 or rule.fixed_amount is not None:
                raise PayrollRuleViolation(
                    "percentage rule requires only a non-negative rate"
                )
            if not rule.basis_component_ids:
                raise PayrollRuleViolation("percentage rule requires basis components")
            if any(
                basis not in sequence_by_component
                or sequence_by_component[basis] >= rule.sequence
                for basis in rule.basis_component_ids
            ):
                raise PayrollRuleViolation(
                    "percentage basis components must appear earlier in the revision"
                )
    existing = db.scalar(
        select(PayStructureRevision).where(
            PayStructureRevision.tenant_id == tenant_id,
            PayStructureRevision.structure_id == structure.id,
            PayStructureRevision.version == version,
        )
    )
    if existing is not None:
        raise PayrollConflict("pay structure revision already exists")
    revision = PayStructureRevision(
        tenant_id=tenant_id,
        structure_id=structure.id,
        version=version,
        effective_from=effective_from,
        effective_to=effective_to,
        net_payable_account_ref=_clean(
            net_payable_account_ref, "net payable account reference"
        ),
        published_by_id=published_by_id,
        published_at=published_at,
    )
    for command in sorted(rules, key=lambda item: item.sequence):
        component = components[command.component_id]
        stored_rule = PayStructureRule(
            tenant_id=tenant_id,
            component_id=component.id,
            sequence=command.sequence,
            component_code=component.component_code,
            component_name=component.name,
            component_kind=component.kind,
            expense_account_ref=component.expense_account_ref,
            liability_account_ref=component.liability_account_ref,
            liability_destination_ref=component.liability_destination_ref,
            calculation_method=command.calculation_method,
            fixed_amount=(
                command.fixed_amount.amount if command.fixed_amount else None
            ),
            rate=command.rate,
            prorates=command.prorates,
            settlement_mode=command.settlement_mode,
        )
        stored_rule.bases.extend(
            PayRuleBasis(tenant_id=tenant_id, component_id=component_id)
            for component_id in command.basis_component_ids
        )
        revision.rules.append(stored_rule)
    db.add(revision)
    db.flush()
    return revision


def assign_employee_pay_structure(
    db: Session,
    *,
    tenant_id: UUID,
    employee_ref: str,
    revision_id: UUID,
    effective_from: date,
    effective_to: date | None,
    source_ref: str,
    source_version: str,
) -> EmployeePayAssignment:
    revision = db.scalar(
        select(PayStructureRevision).where(
            PayStructureRevision.tenant_id == tenant_id,
            PayStructureRevision.id == revision_id,
        )
    )
    if revision is None:
        raise PayrollNotFound("pay structure revision not found")
    if effective_to is not None and effective_to < effective_from:
        raise PayrollRuleViolation("assignment effective end precedes its start")
    cleaned_ref = _clean(employee_ref, "employee reference")
    overlap = db.scalar(
        select(EmployeePayAssignment).where(
            EmployeePayAssignment.tenant_id == tenant_id,
            EmployeePayAssignment.employee_ref == cleaned_ref,
            EmployeePayAssignment.status == "active",
            EmployeePayAssignment.effective_from <= (effective_to or date.max),
            (EmployeePayAssignment.effective_to.is_(None))
            | (EmployeePayAssignment.effective_to >= effective_from),
        )
    )
    if overlap is not None:
        raise PayrollConflict("employee pay assignment overlaps an active assignment")
    row = EmployeePayAssignment(
        tenant_id=tenant_id,
        employee_ref=cleaned_ref,
        revision_id=revision.id,
        effective_from=effective_from,
        effective_to=effective_to,
        source_ref=_clean(source_ref, "assignment source reference"),
        source_version=_clean(source_version, "assignment source version"),
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def create_payroll_run(
    db: Session,
    *,
    tenant_id: UUID,
    run_ref: str,
    period_start: date,
    period_end: date,
    currency: Currency,
    created_by_id: UUID,
    created_at: datetime,
) -> PayrollRun:
    if period_end < period_start:
        raise PayrollRuleViolation("payroll period end precedes its start")
    row = PayrollRun(
        tenant_id=tenant_id,
        run_ref=_clean(run_ref, "payroll run reference"),
        period_start=period_start,
        period_end=period_end,
        currency_code=currency.code,
        minor_units=currency.minor_units,
        status="draft",
        created_by_id=created_by_id,
        created_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def _run(db: Session, tenant_id: UUID, run_id: UUID) -> PayrollRun:
    row = db.scalar(
        select(PayrollRun)
        .where(PayrollRun.tenant_id == tenant_id, PayrollRun.id == run_id)
        .with_for_update()
    )
    if row is None:
        raise PayrollNotFound("payroll run not found")
    return row


def _active_assignment(
    db: Session, tenant_id: UUID, employee_ref: str, as_of: date
) -> EmployeePayAssignment:
    rows = db.scalars(
        select(EmployeePayAssignment).where(
            EmployeePayAssignment.tenant_id == tenant_id,
            EmployeePayAssignment.employee_ref == employee_ref,
            EmployeePayAssignment.status == "active",
            EmployeePayAssignment.effective_from <= as_of,
            (EmployeePayAssignment.effective_to.is_(None))
            | (EmployeePayAssignment.effective_to >= as_of),
        )
    ).all()
    if not rows:
        raise PayrollNotFound("employee has no active pay assignment")
    if len(rows) > 1:
        raise PayrollConflict("employee has ambiguous active pay assignments")
    return rows[0]


def _assignment_for_period(
    db: Session, tenant_id: UUID, employee_ref: str, run: PayrollRun
) -> tuple[EmployeePayAssignment, date]:
    """Assignment and its effective date for the run period.

    An assignment active at the period end is used exactly as before. A
    mid-month leaver has none at the period end, so the lookup date becomes
    min(period_end, assignment end date) of the latest assignment that ends
    inside the period.
    """
    try:
        return (
            _active_assignment(db, tenant_id, employee_ref, run.period_end),
            run.period_end,
        )
    except PayrollNotFound:
        pass
    candidate_ends = db.scalars(
        select(EmployeePayAssignment.effective_to).where(
            EmployeePayAssignment.tenant_id == tenant_id,
            EmployeePayAssignment.employee_ref == employee_ref,
            EmployeePayAssignment.status == "active",
            EmployeePayAssignment.effective_from <= run.period_end,
            EmployeePayAssignment.effective_to.is_not(None),
            EmployeePayAssignment.effective_to >= run.period_start,
        )
    ).all()
    ends = [end for end in candidate_ends if end is not None]
    if not ends:
        raise PayrollNotFound("employee has no active pay assignment")
    as_of = min(run.period_end, max(ends))
    return _active_assignment(db, tenant_id, employee_ref, as_of), as_of


def calculate_employee_payroll(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    employee_ref: str,
    proration_factor: Decimal,
    inputs: tuple[EmployeeComponentInput, ...],
    calculated_at: datetime,
) -> PayrollCalculation:
    run = _run(db, tenant_id, run_id)
    if run.status != "draft":
        raise PayrollConflict("only a draft payroll run can be calculated")
    if not 0 <= proration_factor <= 1:
        raise PayrollRuleViolation("proration factor must be between zero and one")
    cleaned_employee = _clean(employee_ref, "employee reference")
    existing = db.scalar(
        select(PayrollCalculation).where(
            PayrollCalculation.tenant_id == tenant_id,
            PayrollCalculation.run_id == run.id,
            PayrollCalculation.employee_ref == cleaned_employee,
        )
    )
    if existing is not None:
        raise PayrollConflict("employee already has a calculation in this run")
    assignment, as_of = _assignment_for_period(db, tenant_id, cleaned_employee, run)
    revision = db.scalar(
        select(PayStructureRevision)
        .options(
            selectinload(PayStructureRevision.rules).selectinload(
                PayStructureRule.bases
            )
        )
        .where(
            PayStructureRevision.tenant_id == tenant_id,
            PayStructureRevision.id == assignment.revision_id,
        )
    )
    if revision is None:
        raise PayrollNotFound("assigned pay structure revision not found")
    structure = _structure(db, tenant_id, revision.structure_id)
    if (
        structure.currency_code != run.currency_code
        or structure.minor_units != run.minor_units
    ):
        raise PayrollRuleViolation("pay structure currency does not match payroll run")
    if not revision.effective_from <= as_of or (
        revision.effective_to is not None and revision.effective_to < as_of
    ):
        raise PayrollRuleViolation("assigned pay structure revision is not effective")
    input_by_component: dict[UUID, EmployeeComponentInput] = {}
    for item in inputs:
        if item.component_id in input_by_component:
            raise PayrollRuleViolation("employee component inputs must be unique")
        if (
            item.amount.currency.code != run.currency_code
            or item.amount.currency.minor_units != run.minor_units
        ):
            raise PayrollRuleViolation(
                "employee component input uses the wrong currency"
            )
        if item.amount.amount < 0:
            raise PayrollRuleViolation("employee component input must be non-negative")
        input_by_component[item.component_id] = item
    raw_values: dict[UUID, Decimal] = {}
    values: dict[UUID, Decimal] = {}
    line_values: list[tuple[PayStructureRule, Decimal, str]] = []
    for rule in revision.rules:
        if rule.calculation_method == "input":
            supplied = input_by_component.get(rule.component_id)
            if supplied is None:
                raise PayrollRuleViolation(
                    f"missing employee input for component {rule.component_code}"
                )
            raw = supplied.amount.amount
            evidence_ref = _clean(supplied.evidence_ref, "component evidence reference")
        elif rule.calculation_method == "fixed":
            if rule.fixed_amount is None:
                raise PayrollRuleViolation("fixed payroll rule has no amount")
            raw = rule.fixed_amount
            evidence_ref = f"pay-structure-revision:{revision.id}"
        else:
            if rule.rate is None or not rule.bases:
                raise PayrollRuleViolation("percentage payroll rule is incomplete")
            try:
                basis = sum(
                    (raw_values[item.component_id] for item in rule.bases), Decimal(0)
                )
            except KeyError as exc:
                raise PayrollRuleViolation(
                    "percentage payroll rule references an unevaluated component"
                ) from exc
            raw = basis * rule.rate
            evidence_ref = f"pay-structure-revision:{revision.id}"
        amount = raw * proration_factor if rule.prorates else raw
        raw_values[rule.component_id] = _round(raw, run.minor_units)
        values[rule.component_id] = _round(amount, run.minor_units)
        line_values.append((rule, values[rule.component_id], evidence_ref))
    gross = sum(
        (amount for rule, amount, _ in line_values if rule.component_kind == "earning"),
        Decimal(0),
    )
    deductions = sum(
        (
            amount
            for rule, amount, _ in line_values
            if rule.component_kind == "deduction"
        ),
        Decimal(0),
    )
    employer_liability = sum(
        (
            amount
            for rule, amount, _ in line_values
            if rule.component_kind == "employer_liability"
        ),
        Decimal(0),
    )
    if deductions > gross:
        raise PayrollRuleViolation("payroll deductions exceed gross pay")
    calculation = PayrollCalculation(
        tenant_id=tenant_id,
        run_id=run.id,
        employee_ref=cleaned_employee,
        assignment_id=assignment.id,
        revision_id=revision.id,
        revision_version=revision.version,
        proration_factor=proration_factor,
        gross_amount=gross,
        deduction_amount=deductions,
        net_amount=gross - deductions,
        employer_liability_amount=employer_liability,
        calculated_at=calculated_at,
    )
    db.add(calculation)
    db.flush()
    for rule, amount, evidence_ref in line_values:
        db.add(
            PayrollCalculationLine(
                tenant_id=tenant_id,
                calculation_id=calculation.id,
                component_id=rule.component_id,
                sequence=rule.sequence,
                component_code=rule.component_code,
                component_name=rule.component_name,
                component_kind=rule.component_kind,
                calculation_method=rule.calculation_method,
                amount=amount,
                evidence_ref=evidence_ref,
                expense_account_ref=rule.expense_account_ref,
                liability_account_ref=rule.liability_account_ref,
                liability_destination_ref=rule.liability_destination_ref,
                settlement_mode=rule.settlement_mode,
            )
        )
    db.flush()
    return calculation


def read_payroll_run_totals(
    db: Session, *, tenant_id: UUID, run_id: UUID
) -> PayrollRunTotals:
    """Read persisted scalar facts and delegate their totals to the pure owner.

    Flush before reading so pending calculations are included even when the
    assembly disables autoflush. This creates no stored total or shadow run.
    """
    db.flush()
    run_currency = db.execute(
        select(PayrollRun.currency_code, PayrollRun.minor_units).where(
            PayrollRun.tenant_id == tenant_id, PayrollRun.id == run_id
        )
    ).one_or_none()
    if run_currency is None:
        raise PayrollNotFound("payroll run not found")
    currency = Currency(run_currency.currency_code, run_currency.minor_units)
    rows = db.execute(
        select(
            PayrollCalculation.id,
            PayrollCalculation.gross_amount,
            PayrollCalculation.deduction_amount,
            PayrollCalculation.net_amount,
        ).where(
            PayrollCalculation.tenant_id == tenant_id,
            PayrollCalculation.run_id == run_id,
        )
    )
    return aggregate_payroll_run_totals(
        currency=currency,
        snapshots=(
            PayrollAmountSnapshot(
                str(row.id),
                currency,
                row.gross_amount,
                row.deduction_amount,
                row.net_amount,
            )
            for row in rows
        ),
    )


def approve_payroll_run(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    approved_by_id: UUID,
    approved_at: datetime,
) -> PayrollRun:
    run = _run(db, tenant_id, run_id)
    if run.status != "draft":
        raise PayrollConflict("only a draft payroll run can be approved")
    if run.created_by_id == approved_by_id:
        raise PayrollRuleViolation("payroll run creator cannot approve")
    totals = read_payroll_run_totals(db, tenant_id=tenant_id, run_id=run.id)
    if not totals.calculation_count:
        raise PayrollRuleViolation("payroll run has no calculations")
    run.status = "approved"
    run.approved_by_id = approved_by_id
    run.approved_at = approved_at
    db.flush()
    return run


def finalize_payroll_run(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    finalized_by_id: UUID,
    finalized_at: datetime,
) -> PayrollRun:
    run = _run(db, tenant_id, run_id)
    if run.status != "approved":
        raise PayrollConflict("only an approved payroll run can be finalized")
    if run.approved_by_id == finalized_by_id:
        raise PayrollRuleViolation("payroll approver cannot finalize the run")
    calculations = db.scalars(
        select(PayrollCalculation).where(
            PayrollCalculation.tenant_id == tenant_id,
            PayrollCalculation.run_id == run.id,
        )
    ).all()
    for calculation in calculations:
        revision = db.scalar(
            select(PayStructureRevision).where(
                PayStructureRevision.tenant_id == tenant_id,
                PayStructureRevision.id == calculation.revision_id,
            )
        )
        if revision is None:
            raise PayrollNotFound("calculation pay structure revision not found")
        db.add(
            PayrollLiability(
                tenant_id=tenant_id,
                run_id=run.id,
                calculation_id=calculation.id,
                liability_key="employee-net-pay",
                beneficiary_type="employee",
                beneficiary_ref=calculation.employee_ref,
                component_id=None,
                liability_account_ref=revision.net_payable_account_ref,
                amount=calculation.net_amount,
                amount_settled=Decimal(0),
                currency_code=run.currency_code,
                minor_units=run.minor_units,
                status="outstanding",
            )
        )
        lines = db.scalars(
            select(PayrollCalculationLine).where(
                PayrollCalculationLine.tenant_id == tenant_id,
                PayrollCalculationLine.calculation_id == calculation.id,
                PayrollCalculationLine.component_kind.in_(
                    ("deduction", "employer_liability")
                ),
                PayrollCalculationLine.amount > 0,
            )
        ).all()
        for line in lines:
            if line.settlement_mode == "internal_recovery":
                # Already recovered against the employee and the asset account
                # (e.g. a staff loan); never an outstanding external payable.
                if line.component_kind != "deduction":
                    raise PayrollRuleViolation(
                        "internal recovery applies only to deduction components"
                    )
                if not line.liability_account_ref:
                    raise PayrollRuleViolation(
                        f"internal recovery {line.component_code} has no asset account"
                    )
                db.add(
                    PayrollLiability(
                        tenant_id=tenant_id,
                        run_id=run.id,
                        calculation_id=calculation.id,
                        liability_key=f"component:{line.component_id}",
                        beneficiary_type="employee",
                        beneficiary_ref=calculation.employee_ref,
                        component_id=line.component_id,
                        liability_account_ref=line.liability_account_ref,
                        amount=line.amount,
                        amount_settled=line.amount,
                        currency_code=run.currency_code,
                        minor_units=run.minor_units,
                        status="settled",
                        settlement_mode="internal_recovery",
                    )
                )
                continue
            if not line.liability_account_ref or not line.liability_destination_ref:
                raise PayrollRuleViolation(
                    f"liability component {line.component_code} has no destination"
                )
            db.add(
                PayrollLiability(
                    tenant_id=tenant_id,
                    run_id=run.id,
                    calculation_id=calculation.id,
                    liability_key=f"component:{line.component_id}",
                    beneficiary_type="external",
                    beneficiary_ref=line.liability_destination_ref,
                    component_id=line.component_id,
                    liability_account_ref=line.liability_account_ref,
                    amount=line.amount,
                    amount_settled=Decimal(0),
                    currency_code=run.currency_code,
                    minor_units=run.minor_units,
                    status="outstanding",
                )
            )
    run.status = "finalized"
    run.finalized_by_id = finalized_by_id
    run.finalized_at = finalized_at
    db.flush()
    return run


def cancel_payroll_run(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    cancelled_by_id: UUID,
    reason: str,
    cancelled_at: datetime,
) -> PayrollRun:
    """Owner command: draft or approved -> cancelled, with actor and reason.

    Refused once finalized. The actor, instant and reason are stored on the run
    as the audit record. Idempotent: repeating the same actor and reason returns
    the already-cancelled run unchanged; a different actor or reason conflicts.
    Calculations are kept as evidence. The row is locked; flush-only.
    """
    cleaned_reason = _clean(reason, "cancel reason")
    run = _run(db, tenant_id, run_id)
    if run.status == "cancelled":
        if (
            run.cancelled_by_id != cancelled_by_id
            or run.cancel_reason != cleaned_reason
        ):
            raise PayrollConflict("payroll run was already cancelled differently")
        return run
    if run.status not in ("draft", "approved"):
        raise PayrollConflict("only a draft or approved payroll run can be cancelled")
    run.status = "cancelled"
    run.cancelled_by_id = cancelled_by_id
    run.cancelled_at = cancelled_at
    run.cancel_reason = cleaned_reason
    db.flush()
    return run


def effective_liability_amount(liability: PayrollLiability) -> Decimal:
    """The amount owed: the original ``amount`` plus the signed adjustments.

    ``liability.amount`` is bound to the calculation and never changes; payout
    and settlement compare against this value instead.
    """
    return liability.amount + (liability.amount_adjusted or Decimal(0))


def _liability_status(liability: PayrollLiability) -> str:
    if liability.amount_settled == effective_liability_amount(liability):
        return "settled"
    if liability.amount_settled == 0:
        return "outstanding"
    return "partially_settled"


def record_liability_settlement(
    db: Session,
    *,
    tenant_id: UUID,
    liability_id: UUID,
    amount: Money,
    settlement_ref: str,
    evidence_ref: str,
    recorded_by_id: UUID,
    recorded_at: datetime,
) -> PayrollLiabilitySettlement:
    liability = db.scalar(
        select(PayrollLiability)
        .where(
            PayrollLiability.tenant_id == tenant_id,
            PayrollLiability.id == liability_id,
        )
        .with_for_update()
    )
    if liability is None:
        raise PayrollNotFound("payroll liability not found")
    if liability.settlement_mode == "internal_recovery":
        raise PayrollRuleViolation(
            "internal recovery is recovered at finalize and cannot be settled"
        )
    if (
        amount.currency.code != liability.currency_code
        or amount.currency.minor_units != liability.minor_units
    ):
        raise PayrollRuleViolation("settlement uses the wrong liability currency")
    if amount.amount <= 0:
        raise PayrollRuleViolation("settlement amount must be positive")
    cleaned_settlement_ref = _clean(settlement_ref, "settlement reference")
    cleaned_evidence_ref = _clean(evidence_ref, "settlement evidence reference")
    existing = db.scalar(
        select(PayrollLiabilitySettlement).where(
            PayrollLiabilitySettlement.tenant_id == tenant_id,
            PayrollLiabilitySettlement.liability_id == liability.id,
            PayrollLiabilitySettlement.settlement_ref == cleaned_settlement_ref,
        )
    )
    if existing is not None:
        if (
            existing.amount != amount.amount
            or existing.evidence_ref != cleaned_evidence_ref
        ):
            raise PayrollConflict(
                "settlement reference was reused with different evidence"
            )
        if db.scalar(
            select(PayrollSettlementReversal.id).where(
                PayrollSettlementReversal.tenant_id == tenant_id,
                PayrollSettlementReversal.settlement_id == existing.id,
            )
        ):
            raise PayrollConflict(
                "settlement was reversed; record re-payment under a new reference"
            )
        return existing
    if liability.amount_settled + amount.amount > effective_liability_amount(liability):
        raise PayrollRuleViolation("settlement exceeds the outstanding liability")
    row = PayrollLiabilitySettlement(
        tenant_id=tenant_id,
        liability_id=liability.id,
        amount=amount.amount,
        settlement_ref=cleaned_settlement_ref,
        evidence_ref=cleaned_evidence_ref,
        recorded_by_id=recorded_by_id,
        recorded_at=recorded_at,
    )
    liability.amount_settled += amount.amount
    liability.status = _liability_status(liability)
    db.add(row)
    db.flush()
    return row


def reverse_liability_settlement(
    db: Session,
    *,
    tenant_id: UUID,
    settlement_id: UUID,
    return_ref: str,
    reason: str,
    reversed_by_id: UUID,
    reversed_at: datetime,
) -> PayrollSettlementReversal:
    """Reverse one whole recorded settlement whose bank credit was returned.

    The money never reached the payee, so the liability is reopened for
    re-payment. The settlement row is never updated: an append-only reversal
    row is inserted and the liability's settled amount is reduced by exactly
    the settlement amount (status back to ``outstanding``) in the caller's
    transaction. Refuses the settlement's own recorder (a second person must
    attest the bank return), internal recoveries, and a liability this
    settlement does not fully settle. An identical replay (same return reference,
    reason and reverser) returns the existing reversal; anything else is a
    conflict. ``reversed_at`` must be timezone-aware and not before the
    settlement's ``recorded_at``.

    Caller duty (the ERP pin; this package cannot prove it): the bank account
    passed to ``build_settlement_reversal_consequence`` must be the cash GL of
    the settlement's ORIGINAL posted journal, not the current account mapping.
    Refuse a mismatch, route a now-retired cash GL to the manual exception
    queue, and post on a date not earlier than the settlement's posting date.
    """
    ref = _clean(return_ref, "bank return reference")
    why = _clean(reason, "reversal reason")
    settlement = db.scalar(
        select(PayrollLiabilitySettlement).where(
            PayrollLiabilitySettlement.tenant_id == tenant_id,
            PayrollLiabilitySettlement.id == settlement_id,
        )
    )
    if settlement is None:
        raise PayrollNotFound("payroll settlement not found")
    liability = db.scalar(
        select(PayrollLiability)
        .where(
            PayrollLiability.tenant_id == tenant_id,
            PayrollLiability.id == settlement.liability_id,
        )
        .with_for_update()
    )
    if liability is None:
        raise PayrollNotFound("payroll liability not found")
    existing = db.scalar(
        select(PayrollSettlementReversal).where(
            PayrollSettlementReversal.tenant_id == tenant_id,
            PayrollSettlementReversal.settlement_id == settlement.id,
        )
    )
    if existing is not None:
        if (
            existing.return_ref != ref
            or existing.reason != why
            or existing.reversed_by_id != reversed_by_id
        ):
            raise PayrollConflict(
                "settlement was already reversed with different evidence"
            )
        return existing
    if reversed_at.tzinfo is None or reversed_at.utcoffset() is None:
        raise PayrollRuleViolation("reversed_at must be timezone-aware")
    recorded_at = settlement.recorded_at
    if recorded_at.tzinfo is None:
        recorded_at = recorded_at.replace(tzinfo=UTC)
    if reversed_at < recorded_at:
        raise PayrollRuleViolation(
            "settlement cannot be reversed before it was recorded"
        )
    if liability.settlement_mode == "internal_recovery":
        raise PayrollRuleViolation(
            "internal recovery is recovered at finalize and cannot be reversed"
        )
    if reversed_by_id == settlement.recorded_by_id:
        raise PayrollRuleViolation(
            "settlement reversal requires a different person than the recorder"
        )
    if (
        settlement.amount != effective_liability_amount(liability)
        or liability.amount_settled != settlement.amount
        or liability.status != "settled"
    ):
        raise PayrollRuleViolation(
            "settlement does not fully settle the liability and cannot be reversed"
        )
    row = PayrollSettlementReversal(
        tenant_id=tenant_id,
        settlement_id=settlement.id,
        liability_id=liability.id,
        amount=settlement.amount,
        return_ref=ref,
        reason=why,
        reversed_by_id=reversed_by_id,
        reversed_at=reversed_at,
    )
    liability.amount_settled -= settlement.amount
    liability.status = _liability_status(liability)
    db.add(row)
    db.flush()
    return row


def adjust_liability(
    db: Session,
    *,
    tenant_id: UUID,
    liability_id: UUID,
    amount: Money,
    adjustment_ref: str,
    reason: str,
    evidence_ref: str,
    offset_account_ref: str,
    adjusted_by_id: UUID,
    adjusted_at: datetime,
) -> PayrollLiabilityAdjustment:
    """Apply one signed, audited adjustment to a liability (a6).

    A negative amount writes the liability down (for example unclaimed salary);
    a positive amount increases it. ``liability.amount`` is never changed (the
    run consequence compares it with the calculation); the signed sum lives in
    ``amount_adjusted`` and the audited rows. Corrections are made forward with
    an opposite adjustment. No second person is required. An identical replay
    returns the existing row; any other reuse of the reference is a conflict.
    Flush-only; the caller owns the transaction.
    """
    liability = db.scalar(
        select(PayrollLiability)
        .where(
            PayrollLiability.tenant_id == tenant_id,
            PayrollLiability.id == liability_id,
        )
        .with_for_update()
    )
    if liability is None:
        raise PayrollNotFound("payroll liability not found")
    if liability.settlement_mode == "internal_recovery":
        raise PayrollRuleViolation(
            "internal recovery cannot be adjusted (internal_recovery_not_adjustable)"
        )
    if (
        amount.currency.code != liability.currency_code
        or amount.currency.minor_units != liability.minor_units
    ):
        raise PayrollRuleViolation("adjustment uses the wrong liability currency")
    quantum = Decimal(1).scaleb(-liability.minor_units)
    if not amount.amount.is_finite() or amount.amount != amount.amount.quantize(
        quantum
    ):
        raise PayrollRuleViolation("adjustment amount is not at the currency scale")
    if amount.amount == 0:
        raise PayrollRuleViolation("adjustment amount must not be zero")
    ref = _clean(adjustment_ref, "adjustment reference")
    why = _clean(reason, "adjustment reason")
    evidence = _clean(evidence_ref, "adjustment evidence reference")
    offset = _clean(offset_account_ref, "offset account reference")
    if offset == liability.liability_account_ref.strip():
        raise PayrollRuleViolation(
            "offset account must differ from the liability account "
            "(offset_is_liability_account)"
        )
    existing = db.scalar(
        select(PayrollLiabilityAdjustment).where(
            PayrollLiabilityAdjustment.tenant_id == tenant_id,
            PayrollLiabilityAdjustment.liability_id == liability.id,
            PayrollLiabilityAdjustment.adjustment_ref == ref,
        )
    )
    if existing is not None:
        if (
            existing.amount != amount.amount
            or existing.reason != why
            or existing.evidence_ref != evidence
            or existing.offset_account_ref != offset
            or existing.adjusted_by_id != adjusted_by_id
        ):
            raise PayrollConflict(
                "adjustment reference was reused with different evidence"
            )
        return existing
    if adjusted_at.tzinfo is None or adjusted_at.utcoffset() is None:
        raise PayrollRuleViolation("adjusted_at must be timezone-aware")
    created_at = liability.created_at
    if created_at is not None:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if adjusted_at < created_at:
            raise PayrollRuleViolation(
                "liability cannot be adjusted before it was created"
            )
    if amount.amount < 0 and -amount.amount > (
        effective_liability_amount(liability) - liability.amount_settled
    ):
        raise PayrollRuleViolation(
            "write-down exceeds the outstanding liability "
            "(adjustment_exceeds_outstanding)"
        )
    row = PayrollLiabilityAdjustment(
        tenant_id=tenant_id,
        liability_id=liability.id,
        amount=amount.amount,
        adjustment_ref=ref,
        reason=why,
        evidence_ref=evidence,
        offset_account_ref=offset,
        adjusted_by_id=adjusted_by_id,
        adjusted_at=adjusted_at,
    )
    liability.amount_adjusted = (
        liability.amount_adjusted or Decimal(0)
    ) + amount.amount
    liability.status = _liability_status(liability)
    db.add(row)
    db.flush()
    return row


__all__ = [
    "PayrollConflict",
    "PayrollNotFound",
    "PayrollRuleViolation",
    "adjust_liability",
    "approve_payroll_run",
    "assign_employee_pay_structure",
    "calculate_employee_payroll",
    "cancel_payroll_run",
    "create_pay_component",
    "create_pay_structure",
    "create_payroll_run",
    "effective_liability_amount",
    "finalize_payroll_run",
    "publish_pay_structure_revision",
    "read_payroll_run_totals",
    "record_liability_settlement",
    "retire_pay_component",
    "reverse_liability_settlement",
    "update_pay_component",
]
