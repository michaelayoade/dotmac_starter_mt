"""Read-only GL consequence facts for finalized payroll runs and settlements.

Payroll never writes the general ledger. These builders read persisted,
tenant-scoped facts and return frozen, balanced, content-hashed facts that an
assembly adapter (for example the ERP posting adapter) may post under its own
idempotency, period and authority rules. Account references are the opaque,
tenant-configured refs already stored on components and structure revisions;
nothing here hard-codes a chart of accounts, moves money or calls a provider.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_payroll.models import (
    PayrollCalculation,
    PayrollCalculationLine,
    PayrollLiability,
    PayrollLiabilitySettlement,
    PayrollRun,
)
from dotmac_payroll.service import PayrollNotFound, PayrollRuleViolation

RUN_FINALIZED_SOURCE = "payroll.run.finalized"
SETTLEMENT_SOURCE = "payroll.liability.settled"
_DEBIT = "debit"
_CREDIT = "credit"


@dataclass(frozen=True, slots=True)
class PayrollGlLine:
    account_ref: str
    side: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class PayrollAccountingConsequence:
    """One balanced, idempotent GL consequence fact."""

    tenant_id: UUID
    source_type: str
    source_ref: str
    idempotency_key: str
    effective_date: date
    currency_code: str
    minor_units: int
    lines: tuple[PayrollGlLine, ...]
    total_debit: Decimal
    total_credit: Decimal
    content_sha256: str


def _clean_ref(value: str | None, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise PayrollRuleViolation(f"{label} must be configured")
    return cleaned


def _seal(
    *,
    tenant_id: UUID,
    source_type: str,
    source_ref: str,
    effective_date: date,
    currency_code: str,
    minor_units: int,
    raw: list[tuple[str, str, Decimal]],
) -> PayrollAccountingConsequence:
    quantum = Decimal(1).scaleb(-minor_units)
    merged: dict[tuple[str, str], Decimal] = {}
    for account_ref, side, amount in raw:
        if not amount.is_finite() or amount != amount.quantize(quantum):
            raise PayrollRuleViolation("payroll amount is not at the currency scale")
        if amount < 0:
            raise PayrollRuleViolation("payroll GL line amount must not be negative")
        if amount == 0:
            continue
        key = (account_ref, side)
        merged[key] = merged.get(key, Decimal(0)) + amount
    # Deterministic order: debits first, then by account reference.
    ordered = sorted(
        merged.items(), key=lambda item: (item[0][1] != _DEBIT, item[0][0])
    )
    lines = tuple(
        PayrollGlLine(account_ref, side, amount.quantize(quantum))
        for (account_ref, side), amount in ordered
    )
    if not lines:
        raise PayrollRuleViolation("payroll consequence has no non-zero lines")
    debit = sum((x.amount for x in lines if x.side == _DEBIT), Decimal(0))
    credit = sum((x.amount for x in lines if x.side == _CREDIT), Decimal(0))
    if debit != credit:
        raise PayrollRuleViolation("payroll consequence does not balance")
    canonical = "|".join(
        [
            str(tenant_id),
            source_type,
            source_ref,
            effective_date.isoformat(),
            currency_code,
            str(minor_units),
            *(f"{x.side}:{x.account_ref}:{x.amount}" for x in lines),
        ]
    )
    return PayrollAccountingConsequence(
        tenant_id=tenant_id,
        source_type=source_type,
        source_ref=source_ref,
        idempotency_key=f"{source_type}:{tenant_id}:{source_ref}",
        effective_date=effective_date,
        currency_code=currency_code,
        minor_units=minor_units,
        lines=lines,
        total_debit=debit,
        total_credit=credit,
        content_sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    )


def _sum_kind(lines: list[PayrollCalculationLine], kind: str) -> Decimal:
    return sum((x.amount for x in lines if x.component_kind == kind), Decimal(0))


def build_run_accounting_consequence(
    db: Session, *, tenant_id: UUID, run_id: UUID
) -> PayrollAccountingConsequence:
    """Fact for a finalized run.

    Dr earning expense (gross) and employer-liability expense; Cr deduction and
    employer liability accounts; Cr the revision net-payable account (the
    configured payroll payable, e.g. ref 2040) for total net pay. Refuses a run
    that is not finalized, has no calculations, lacks a configured account, or
    whose persisted liabilities/calculation snapshots drift from its lines.
    """
    db.flush()
    run = db.scalar(
        select(PayrollRun).where(
            PayrollRun.tenant_id == tenant_id, PayrollRun.id == run_id
        )
    )
    if run is None:
        raise PayrollNotFound("payroll run not found")
    if run.status != "finalized":
        raise PayrollRuleViolation("only a finalized payroll run has a GL consequence")
    calculations = db.scalars(
        select(PayrollCalculation).where(
            PayrollCalculation.tenant_id == tenant_id,
            PayrollCalculation.run_id == run.id,
        )
    ).all()
    if not calculations:
        raise PayrollRuleViolation("payroll run has no calculations")
    raw: list[tuple[str, str, Decimal]] = []
    for calculation in calculations:
        lines = list(
            db.scalars(
                select(PayrollCalculationLine).where(
                    PayrollCalculationLine.tenant_id == tenant_id,
                    PayrollCalculationLine.calculation_id == calculation.id,
                )
            ).all()
        )
        gross = _sum_kind(lines, "earning")
        deductions = _sum_kind(lines, "deduction")
        if (
            gross != calculation.gross_amount
            or deductions != calculation.deduction_amount
            or _sum_kind(lines, "employer_liability")
            != calculation.employer_liability_amount
            or gross - deductions != calculation.net_amount
        ):
            raise PayrollRuleViolation("calculation lines drift from their snapshot")
        for line in lines:
            if line.amount == 0:
                continue
            code = line.component_code
            if line.component_kind == "earning":
                expense = _clean_ref(
                    line.expense_account_ref, f"expense account for {code}"
                )
                raw.append((expense, _DEBIT, line.amount))
            elif line.component_kind == "deduction":
                payable = _clean_ref(
                    line.liability_account_ref, f"liability account for {code}"
                )
                raw.append((payable, _CREDIT, line.amount))
            elif line.component_kind == "employer_liability":
                expense = _clean_ref(
                    line.expense_account_ref, f"expense account for {code}"
                )
                payable = _clean_ref(
                    line.liability_account_ref, f"liability account for {code}"
                )
                raw.append((expense, _DEBIT, line.amount))
                raw.append((payable, _CREDIT, line.amount))
            else:
                raise PayrollRuleViolation("unknown payroll component kind")
        net_liabilities = db.scalars(
            select(PayrollLiability).where(
                PayrollLiability.tenant_id == tenant_id,
                PayrollLiability.run_id == run.id,
                PayrollLiability.calculation_id == calculation.id,
                PayrollLiability.liability_key == "employee-net-pay",
            )
        ).all()
        if len(net_liabilities) != 1:
            raise PayrollRuleViolation("calculation has no single net-pay liability")
        liability = net_liabilities[0]
        if liability.amount != calculation.net_amount:
            raise PayrollRuleViolation("net-pay liability drifts from calculation")
        if calculation.net_amount:
            raw.append(
                (
                    _clean_ref(liability.liability_account_ref, "net payable account"),
                    _CREDIT,
                    liability.amount,
                )
            )
    return _seal(
        tenant_id=tenant_id,
        source_type=RUN_FINALIZED_SOURCE,
        source_ref=str(run.id),
        effective_date=run.period_end,
        currency_code=run.currency_code,
        minor_units=run.minor_units,
        raw=raw,
    )


def build_liability_settlement_consequence(
    db: Session,
    *,
    tenant_id: UUID,
    settlement_id: UUID,
    bank_account_ref: str,
) -> PayrollAccountingConsequence:
    """Fact for a recorded settlement: Dr liability account / Cr bank GL.

    Covers employee net pay (the payable account, e.g. 2040) and statutory
    remittances (PAYE, pension) alike; settlements are records of money a human
    already moved, never an instruction to move it. The bank GL ref is supplied
    by the caller because payroll stores no bank account.
    """
    db.flush()
    bank = _clean_ref(bank_account_ref, "bank account reference")
    row = db.execute(
        select(PayrollLiabilitySettlement, PayrollLiability)
        .join(
            PayrollLiability,
            (PayrollLiability.tenant_id == PayrollLiabilitySettlement.tenant_id)
            & (PayrollLiability.id == PayrollLiabilitySettlement.liability_id),
        )
        .where(
            PayrollLiabilitySettlement.tenant_id == tenant_id,
            PayrollLiabilitySettlement.id == settlement_id,
        )
    ).one_or_none()
    if row is None:
        raise PayrollNotFound("payroll settlement not found")
    settlement, liability = row
    liability_account = _clean_ref(liability.liability_account_ref, "liability account")
    if bank == liability_account:
        raise PayrollRuleViolation(
            "bank account must differ from the liability account"
        )
    if settlement.amount <= 0:
        raise PayrollRuleViolation("settlement amount must be positive")
    return _seal(
        tenant_id=tenant_id,
        source_type=SETTLEMENT_SOURCE,
        source_ref=str(settlement.id),
        effective_date=settlement.recorded_at.date(),
        currency_code=liability.currency_code,
        minor_units=liability.minor_units,
        raw=[
            (liability_account, _DEBIT, settlement.amount),
            (bank, _CREDIT, settlement.amount),
        ],
    )


__all__ = [
    "RUN_FINALIZED_SOURCE",
    "SETTLEMENT_SOURCE",
    "PayrollAccountingConsequence",
    "PayrollGlLine",
    "build_liability_settlement_consequence",
    "build_run_accounting_consequence",
]
