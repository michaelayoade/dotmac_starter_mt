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
    PayrollLiabilityAdjustment,
    PayrollLiabilitySettlement,
    PayrollRun,
    PayrollSettlementReversal,
)
from dotmac_payroll.service import PayrollNotFound, PayrollRuleViolation

CONSEQUENCE_OWNER = "payroll"
RUN_DOCUMENT_KIND = "payroll_run"
SETTLEMENT_DOCUMENT_KIND = "payroll_liability_settlement"
SETTLEMENT_REVERSAL_DOCUMENT_KIND = "payroll_settlement_reversal"
LIABILITY_ADJUSTMENT_DOCUMENT_KIND = "payroll_liability_adjustment"
CONSEQUENCE_SOURCE_VERSION = "1"
_DEBIT = "debit"
_CREDIT = "credit"


@dataclass(frozen=True, slots=True)
class PayrollConsequenceSource:
    """Source identity; field names mirror payables ``ConsequenceSource``."""

    owner: str
    document_kind: str
    document_id: str
    version: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class PayrollAccountingEntry:
    """One GL entry; field names mirror payables ``AccountingEntry``."""

    account_ref: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str | None = None
    dimension_refs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class PayrollAccountingConsequence:
    """One balanced, idempotent GL consequence fact.

    Mirrors payables ``AccountingConsequence`` (source, posting_date,
    description, currency_code, entries, fingerprint) without
    ``exchange_rate``: payroll stores none, so adopters must refuse a
    non-functional currency. ``minor_units`` and ``idempotency_key`` are extras.
    """

    source: PayrollConsequenceSource
    posting_date: date
    description: str
    currency_code: str
    minor_units: int
    entries: tuple[PayrollAccountingEntry, ...]
    fingerprint: str
    idempotency_key: str


def _clean_ref(value: str | None, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise PayrollRuleViolation(f"{label} must be configured")
    return cleaned


def _seal(
    *,
    tenant_id: UUID,
    document_kind: str,
    document_id: str,
    description: str,
    posting_date: date,
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
    entries = tuple(
        PayrollAccountingEntry(
            account_ref,
            debit=amount.quantize(quantum) if side == _DEBIT else Decimal(0),
            credit=amount.quantize(quantum) if side == _CREDIT else Decimal(0),
        )
        for (account_ref, side), amount in ordered
    )
    if not entries:
        raise PayrollRuleViolation("payroll consequence has no non-zero lines")
    debit = sum((x.debit for x in entries), Decimal(0))
    credit = sum((x.credit for x in entries), Decimal(0))
    if debit != credit:
        raise PayrollRuleViolation("payroll consequence does not balance")
    canonical = "|".join(
        [
            str(tenant_id),
            CONSEQUENCE_OWNER,
            document_kind,
            document_id,
            CONSEQUENCE_SOURCE_VERSION,
            description,
            posting_date.isoformat(),
            currency_code,
            str(minor_units),
            *(f"{x.account_ref}:{x.debit}:{x.credit}" for x in entries),
        ]
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return PayrollAccountingConsequence(
        source=PayrollConsequenceSource(
            CONSEQUENCE_OWNER,
            document_kind,
            document_id,
            CONSEQUENCE_SOURCE_VERSION,
            fingerprint,
        ),
        posting_date=posting_date,
        description=description,
        currency_code=currency_code,
        minor_units=minor_units,
        entries=entries,
        fingerprint=fingerprint,
        idempotency_key=f"{CONSEQUENCE_OWNER}:{document_kind}:{tenant_id}:{document_id}",
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
    Component liability rows must match the deduction/employer lines per
    calculation, so later settlements clear exactly what the fact credited.
    The builder calls ``db.flush()`` on the caller's session and takes no row
    lock: a finalized run is terminal and immutable.
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
        component_liabilities = db.scalars(
            select(PayrollLiability).where(
                PayrollLiability.tenant_id == tenant_id,
                PayrollLiability.run_id == run.id,
                PayrollLiability.calculation_id == calculation.id,
                PayrollLiability.liability_key != "employee-net-pay",
            )
        ).all()
        expected = sorted(
            (str(x.component_id), x.liability_account_ref or "", x.amount)
            for x in lines
            if x.component_kind in ("deduction", "employer_liability") and x.amount > 0
        )
        actual = sorted(
            (str(x.component_id), x.liability_account_ref, x.amount)
            for x in component_liabilities
        )
        if expected != actual:
            raise PayrollRuleViolation(
                "component liabilities drift from the calculation lines"
            )
        # Internal recoveries must be booked as already recovered (settled,
        # employee-side) by finalize; the credit stays on the asset account.
        internal_components = {
            str(x.component_id)
            for x in lines
            if x.settlement_mode == "internal_recovery" and x.amount > 0
        }
        if internal_components != {
            str(x.component_id)
            for x in component_liabilities
            if x.settlement_mode == "internal_recovery"
        } or any(
            x.settlement_mode == "internal_recovery"
            and (x.beneficiary_type != "employee" or x.amount_settled != x.amount)
            for x in component_liabilities
        ):
            raise PayrollRuleViolation(
                "internal recovery liabilities drift from the calculation lines"
            )
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
        document_kind=RUN_DOCUMENT_KIND,
        document_id=str(run.id),
        description=run.run_ref,
        posting_date=run.period_end,
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
    posting_date: date,
) -> PayrollAccountingConsequence:
    """Fact for a recorded settlement: Dr liability account / Cr bank GL.

    Covers employee net pay (the payable account, e.g. 2040) and statutory
    remittances (PAYE, pension) alike; settlements are records of money a human
    already moved, never an instruction to move it. The bank GL ref is supplied
    by the caller because payroll stores no bank account. ``posting_date`` is
    the bank value date, supplied by the caller (who validates it against the
    open period) and covered by the fingerprint. Calls ``db.flush()`` on the
    caller's session; takes no lock.
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
        document_kind=SETTLEMENT_DOCUMENT_KIND,
        document_id=str(settlement.id),
        description=settlement.settlement_ref,
        posting_date=posting_date,
        currency_code=liability.currency_code,
        minor_units=liability.minor_units,
        raw=[
            (liability_account, _DEBIT, settlement.amount),
            (bank, _CREDIT, settlement.amount),
        ],
    )


def build_settlement_reversal_consequence(
    db: Session,
    *,
    tenant_id: UUID,
    reversal_id: UUID,
    bank_account_ref: str,
    posting_date: date,
) -> PayrollAccountingConsequence:
    """Fact for a reversed settlement: Dr bank cash GL / Cr liability account.

    The bank returned the salary credit, so cash comes back and the employee
    payable (e.g. 2040) is owed again. Keyed on the reversal id; the amount is
    the persisted reversal amount. ``posting_date`` is the bank return value
    date supplied by the caller and covered by the fingerprint. Calls
    ``db.flush()``; takes no lock.

    Caller duty (the ERP pin; this package stores no bank account and cannot
    prove it): ``bank_account_ref`` must be the cash GL of the settlement's
    ORIGINAL posted journal, not the current account mapping. Refuse a
    mismatch, route a now-retired cash GL to the manual exception queue, and
    use a ``posting_date`` not earlier than the settlement's posting date.
    """
    db.flush()
    bank = _clean_ref(bank_account_ref, "bank account reference")
    row = db.execute(
        select(PayrollSettlementReversal, PayrollLiability)
        .join(
            PayrollLiability,
            (PayrollLiability.tenant_id == PayrollSettlementReversal.tenant_id)
            & (PayrollLiability.id == PayrollSettlementReversal.liability_id),
        )
        .where(
            PayrollSettlementReversal.tenant_id == tenant_id,
            PayrollSettlementReversal.id == reversal_id,
        )
    ).one_or_none()
    if row is None:
        raise PayrollNotFound("payroll settlement reversal not found")
    reversal, liability = row
    liability_account = _clean_ref(liability.liability_account_ref, "liability account")
    if bank == liability_account:
        raise PayrollRuleViolation(
            "bank account must differ from the liability account"
        )
    if reversal.amount <= 0:
        raise PayrollRuleViolation("reversal amount must be positive")
    return _seal(
        tenant_id=tenant_id,
        document_kind=SETTLEMENT_REVERSAL_DOCUMENT_KIND,
        document_id=str(reversal.id),
        description=reversal.return_ref,
        posting_date=posting_date,
        currency_code=liability.currency_code,
        minor_units=liability.minor_units,
        raw=[
            (bank, _DEBIT, reversal.amount),
            (liability_account, _CREDIT, reversal.amount),
        ],
    )


def build_liability_adjustment_consequence(
    db: Session,
    *,
    tenant_id: UUID,
    adjustment_id: UUID,
    posting_date: date,
) -> PayrollAccountingConsequence:
    """Fact for a liability adjustment, from the persisted adjustment row.

    A write-down (negative amount) is Dr liability account / Cr offset account;
    an increase is Dr offset account / Cr liability account. The offset account
    is the one frozen on the adjustment row. ``posting_date`` is supplied by the
    caller (who validates it against the open period) and covered by the
    fingerprint. Calls ``db.flush()``; takes no lock.
    """
    db.flush()
    row = db.execute(
        select(PayrollLiabilityAdjustment, PayrollLiability)
        .join(
            PayrollLiability,
            (PayrollLiability.tenant_id == PayrollLiabilityAdjustment.tenant_id)
            & (PayrollLiability.id == PayrollLiabilityAdjustment.liability_id),
        )
        .where(
            PayrollLiabilityAdjustment.tenant_id == tenant_id,
            PayrollLiabilityAdjustment.id == adjustment_id,
        )
    ).one_or_none()
    if row is None:
        raise PayrollNotFound("payroll liability adjustment not found")
    adjustment, liability = row
    liability_account = _clean_ref(liability.liability_account_ref, "liability account")
    offset = _clean_ref(adjustment.offset_account_ref, "offset account")
    if offset == liability_account:
        raise PayrollRuleViolation(
            "offset account must differ from the liability account"
        )
    if adjustment.amount == 0:
        raise PayrollRuleViolation("adjustment amount must not be zero")
    size = abs(adjustment.amount)
    raw = (
        [(liability_account, _DEBIT, size), (offset, _CREDIT, size)]
        if adjustment.amount < 0
        else [(offset, _DEBIT, size), (liability_account, _CREDIT, size)]
    )
    return _seal(
        tenant_id=tenant_id,
        document_kind=LIABILITY_ADJUSTMENT_DOCUMENT_KIND,
        document_id=str(adjustment.id),
        description=adjustment.adjustment_ref,
        posting_date=posting_date,
        currency_code=liability.currency_code,
        minor_units=liability.minor_units,
        raw=raw,
    )


__all__ = [
    "CONSEQUENCE_OWNER",
    "CONSEQUENCE_SOURCE_VERSION",
    "LIABILITY_ADJUSTMENT_DOCUMENT_KIND",
    "RUN_DOCUMENT_KIND",
    "SETTLEMENT_DOCUMENT_KIND",
    "SETTLEMENT_REVERSAL_DOCUMENT_KIND",
    "PayrollAccountingConsequence",
    "PayrollAccountingEntry",
    "PayrollConsequenceSource",
    "build_liability_adjustment_consequence",
    "build_liability_settlement_consequence",
    "build_run_accounting_consequence",
    "build_settlement_reversal_consequence",
]
