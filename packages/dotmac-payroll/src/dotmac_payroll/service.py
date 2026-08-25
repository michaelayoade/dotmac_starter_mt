"""Flush-only owner for payroll configuration, calculations and liabilities."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from dotmac_kernel.money import Currency, Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from dotmac_payroll.contracts import (
    EmployeeComponentInput,
    PayComponentInput,
    PayRuleInput,
)
from dotmac_payroll.models import (
    EmployeePayAssignment,
    PayComponent,
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollLiability,
    PayrollLiabilitySettlement,
    PayrollRun,
    PayRuleBasis,
    PayStructure,
    PayStructureRevision,
    PayStructureRule,
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
    assignment = _active_assignment(db, tenant_id, cleaned_employee, run.period_end)
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
    if not revision.effective_from <= run.period_end or (
        revision.effective_to is not None and revision.effective_to < run.period_end
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
            )
        )
    db.flush()
    return calculation


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
    count = db.scalar(
        select(func.count())
        .select_from(PayrollCalculation)
        .where(
            PayrollCalculation.tenant_id == tenant_id,
            PayrollCalculation.run_id == run.id,
        )
    )
    if not count:
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
        return existing
    if liability.amount_settled + amount.amount > liability.amount:
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
    liability.status = (
        "settled"
        if liability.amount_settled == liability.amount
        else "partially_settled"
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "PayrollConflict",
    "PayrollNotFound",
    "PayrollRuleViolation",
    "approve_payroll_run",
    "assign_employee_pay_structure",
    "calculate_employee_payroll",
    "create_pay_component",
    "create_pay_structure",
    "create_payroll_run",
    "finalize_payroll_run",
    "publish_pay_structure_revision",
    "record_liability_settlement",
    "retire_pay_component",
    "update_pay_component",
]
