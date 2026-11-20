"""Pure GL consequence facts for claim liabilities, built from frozen rows.

Expenses never writes the general ledger. These builders read persisted,
tenant-scoped rows and return frozen, balanced, content-hashed facts that an
assembly adapter may post under its own idempotency, period and authority
rules. Account references are the opaque refs frozen on the liability and its
lines at acceptance; nothing here hard-codes a chart of accounts.

A historical liability (admitted with no GL: the legacy ledger already holds
it) has no acceptance consequence; its settlements, returns and adjustments
build normally.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_expenses.contracts import (
    InvalidCommand,
    InvalidLifecycle,
    LiabilityOrigin,
    NotFound,
    RefusalCode,
    refuse,
)
from dotmac_expenses.models import (
    ClaimLiability,
    ClaimLiabilityAdjustment,
    ClaimLiabilityLine,
    ClaimLiabilityReversal,
    ClaimLiabilitySettlement,
    ClaimSettlementReversal,
)

CONSEQUENCE_OWNER = "expenses"
LIABILITY_DOCUMENT_KIND = "expense_claim_liability"
LIABILITY_REVERSAL_DOCUMENT_KIND = "expense_claim_liability_reversal"
SETTLEMENT_DOCUMENT_KIND = "expense_claim_settlement"
SETTLEMENT_REVERSAL_DOCUMENT_KIND = "expense_claim_settlement_reversal"
LIABILITY_ADJUSTMENT_DOCUMENT_KIND = "expense_claim_liability_adjustment"
CONSEQUENCE_DOCUMENT_KINDS = (
    LIABILITY_DOCUMENT_KIND,
    LIABILITY_REVERSAL_DOCUMENT_KIND,
    SETTLEMENT_DOCUMENT_KIND,
    SETTLEMENT_REVERSAL_DOCUMENT_KIND,
    LIABILITY_ADJUSTMENT_DOCUMENT_KIND,
)
CONSEQUENCE_SOURCE_VERSION = "1"
_DEBIT = "debit"
_CREDIT = "credit"

# One raw line: (account_ref, side, amount, dimension_refs).
_Raw = tuple[str, str, Decimal, tuple[tuple[str, str], ...]]


@dataclass(frozen=True, slots=True)
class ExpenseConsequenceSource:
    owner: str
    document_kind: str
    document_id: str
    version: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ExpenseAccountingEntry:
    account_ref: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    description: str | None = None
    dimension_refs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ExpenseAccountingConsequence:
    """One balanced, idempotent GL consequence fact (no exchange rate: the
    functional currency is the claim currency, so adopters refuse any other)."""

    source: ExpenseConsequenceSource
    posting_date: date
    description: str
    currency_code: str
    minor_units: int
    entries: tuple[ExpenseAccountingEntry, ...]
    fingerprint: str
    idempotency_key: str


def _clean_ref(value: str | None, label: str) -> str:
    cleaned = (value or "").strip()
    if not cleaned:
        raise InvalidCommand(f"{label} must be configured")
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
    raw: list[_Raw],
) -> ExpenseAccountingConsequence:
    quantum = Decimal(1).scaleb(-minor_units)
    # Merge by (account, side, dimensions) so two lines of one account and one
    # dimension set become one entry; zero entries are skipped.
    merged: dict[tuple[str, str, tuple[tuple[str, str], ...]], Decimal] = {}
    for account_ref, side, amount, dimensions in raw:
        if not amount.is_finite() or amount != amount.quantize(quantum):
            raise InvalidCommand("expense amount is not at the currency scale")
        if amount < 0:
            raise InvalidCommand("expense GL line amount must not be negative")
        if amount == 0:
            continue
        key = (account_ref, side, dimensions)
        merged[key] = merged.get(key, Decimal(0)) + amount
    # Deterministic order: debits first, then by account reference.
    ordered = sorted(
        merged.items(),
        key=lambda item: (item[0][1] != _DEBIT, item[0][0], item[0][2]),
    )
    entries = tuple(
        ExpenseAccountingEntry(
            account_ref,
            debit=amount if side == _DEBIT else Decimal(0),
            credit=amount if side == _CREDIT else Decimal(0),
            dimension_refs=dimensions,
        )
        for (account_ref, side, dimensions), amount in ordered
    )
    if not entries:
        raise InvalidCommand("expense consequence has no non-zero lines")
    debit = sum((x.debit for x in entries), Decimal(0))
    credit = sum((x.credit for x in entries), Decimal(0))
    if debit != credit:
        raise InvalidCommand("expense consequence does not balance")
    fingerprint = fingerprint_of(
        {
            "tenant_id": str(tenant_id),
            "owner": CONSEQUENCE_OWNER,
            "document_kind": document_kind,
            "document_id": document_id,
            "version": CONSEQUENCE_SOURCE_VERSION,
            "description": description,
            "posting_date": posting_date.isoformat(),
            "currency_code": currency_code,
            "minor_units": minor_units,
            "entries": [
                {
                    "account_ref": entry.account_ref,
                    "debit": str(entry.debit),
                    "credit": str(entry.credit),
                    "dimension_refs": [list(pair) for pair in entry.dimension_refs],
                }
                for entry in entries
            ],
        }
    )
    return ExpenseAccountingConsequence(
        source=ExpenseConsequenceSource(
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


def _utc_date(value: datetime) -> date:
    """The UTC calendar date, so the posting date never depends on the session
    time zone the row was read in (SQLite returns naive values: taken as UTC)."""
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(UTC).date()


def _liability(db: Session, tenant_id: UUID, liability_id: UUID) -> ClaimLiability:
    row = db.scalar(
        select(ClaimLiability).where(
            ClaimLiability.tenant_id == tenant_id, ClaimLiability.id == liability_id
        )
    )
    if row is None:
        raise NotFound("claim liability not found")
    return row


def _dimensions(raw: object) -> tuple[tuple[str, str], ...]:
    return tuple((str(pair[0]), str(pair[1])) for pair in raw)  # type: ignore[attr-defined]


def _acceptance_raw(
    db: Session, liability: ClaimLiability, *, mirror: bool
) -> list[_Raw]:
    """Dr each line's expense (net) and tax account (tax); Cr the liability
    account for the amount. ``mirror`` swaps the sides for the reversal."""
    lines = list(
        db.scalars(
            select(ClaimLiabilityLine)
            .where(
                ClaimLiabilityLine.tenant_id == liability.tenant_id,
                ClaimLiabilityLine.liability_id == liability.id,
            )
            .order_by(ClaimLiabilityLine.line_number)
        )
    )
    if not lines:
        raise InvalidCommand("claim liability has no accepted lines")
    debit, credit = (_CREDIT, _DEBIT) if mirror else (_DEBIT, _CREDIT)
    raw: list[_Raw] = []
    for line in lines:
        raw.append(
            (
                _clean_ref(line.expense_account_ref, "expense account"),
                debit,
                line.net_amount,
                _dimensions(line.dimension_refs),
            )
        )
        if line.tax_amount > 0:
            raw.append(
                (
                    _clean_ref(line.tax_account_ref, "tax account"),
                    debit,
                    line.tax_amount,
                    (),
                )
            )
    raw.append(
        (
            _clean_ref(liability.liability_account_ref, "liability account"),
            credit,
            liability.amount,
            (),
        )
    )
    return raw


def _acceptance_description(liability: ClaimLiability) -> str:
    return _clean_ref(liability.acceptance_reference, "acceptance reference")


def build_claim_liability_consequence(
    db: Session, *, tenant_id: UUID, liability_id: UUID
) -> ExpenseAccountingConsequence:
    """Acceptance fact: Dr expense lines (net) and tax (when > 0) / Cr claims
    payable. Posting date is the acceptance date (the caller validates the
    period). Refuses a historical liability: the legacy ledger holds it.
    Calls ``db.flush()``; takes no lock."""
    db.flush()
    liability = _liability(db, tenant_id, liability_id)
    if liability.origin == LiabilityOrigin.HISTORICAL.value:
        raise refuse(
            InvalidLifecycle,
            "a historical liability has no acceptance consequence",
            RefusalCode.HISTORICAL_LIABILITY_HAS_NO_CONSEQUENCE,
        )
    return _seal(
        tenant_id=tenant_id,
        document_kind=LIABILITY_DOCUMENT_KIND,
        document_id=str(liability.id),
        description=_acceptance_description(liability),
        posting_date=_utc_date(liability.accepted_at),
        currency_code=liability.currency_code,
        minor_units=liability.minor_units,
        raw=_acceptance_raw(db, liability, mirror=False),
    )


def build_claim_liability_reversal_consequence(
    db: Session,
    *,
    tenant_id: UUID,
    reversal_id: UUID,
    posting_date: date | None = None,
) -> ExpenseAccountingConsequence:
    """Exact mirror of the acceptance fact, keyed on the reversal row.
    ``posting_date`` defaults to the reversal date. Calls ``db.flush()``."""
    db.flush()
    reversal = db.scalar(
        select(ClaimLiabilityReversal).where(
            ClaimLiabilityReversal.tenant_id == tenant_id,
            ClaimLiabilityReversal.id == reversal_id,
        )
    )
    if reversal is None:
        raise NotFound("claim liability reversal not found")
    liability = _liability(db, tenant_id, reversal.liability_id)
    return _seal(
        tenant_id=tenant_id,
        document_kind=LIABILITY_REVERSAL_DOCUMENT_KIND,
        document_id=str(reversal.id),
        description=_acceptance_description(liability),
        posting_date=posting_date or _utc_date(reversal.reversed_at),
        currency_code=liability.currency_code,
        minor_units=liability.minor_units,
        raw=_acceptance_raw(db, liability, mirror=True),
    )


def build_claim_settlement_consequence(
    db: Session, *, tenant_id: UUID, settlement_id: UUID, bank_account_ref: str
) -> ExpenseAccountingConsequence:
    """Settlement fact: Dr liability account / Cr the caller-supplied cash GL,
    posted on the settlement's value date. Calls ``db.flush()``."""
    db.flush()
    bank = _clean_ref(bank_account_ref, "bank account reference")
    settlement = db.scalar(
        select(ClaimLiabilitySettlement).where(
            ClaimLiabilitySettlement.tenant_id == tenant_id,
            ClaimLiabilitySettlement.id == settlement_id,
        )
    )
    if settlement is None:
        raise NotFound("claim settlement not found")
    liability = _liability(db, tenant_id, settlement.liability_id)
    account = _clean_ref(liability.liability_account_ref, "liability account")
    if bank == account:
        raise InvalidCommand("bank account must differ from the liability account")
    return _seal(
        tenant_id=tenant_id,
        document_kind=SETTLEMENT_DOCUMENT_KIND,
        document_id=str(settlement.id),
        description=settlement.settlement_ref,
        posting_date=settlement.value_date,
        currency_code=liability.currency_code,
        minor_units=liability.minor_units,
        raw=[
            (account, _DEBIT, settlement.amount, ()),
            (bank, _CREDIT, settlement.amount, ()),
        ],
    )


def build_claim_settlement_reversal_consequence(
    db: Session,
    *,
    tenant_id: UUID,
    reversal_id: UUID,
    bank_account_ref: str,
    posting_date: date,
) -> ExpenseAccountingConsequence:
    """Bank-return fact: Dr cash GL / Cr liability account.

    Caller duty (the ERP pin; this package stores no bank account and cannot
    prove it): ``bank_account_ref`` must be the cash GL of the settlement's
    ORIGINAL posted journal, not the current account mapping. Refuse a
    mismatch, route a retired cash GL to the manual exception queue and use a
    ``posting_date`` not earlier than the settlement's posting date.
    Calls ``db.flush()``."""
    db.flush()
    bank = _clean_ref(bank_account_ref, "bank account reference")
    reversal = db.scalar(
        select(ClaimSettlementReversal).where(
            ClaimSettlementReversal.tenant_id == tenant_id,
            ClaimSettlementReversal.id == reversal_id,
        )
    )
    if reversal is None:
        raise NotFound("claim settlement reversal not found")
    liability = _liability(db, tenant_id, reversal.liability_id)
    account = _clean_ref(liability.liability_account_ref, "liability account")
    if bank == account:
        raise InvalidCommand("bank account must differ from the liability account")
    return _seal(
        tenant_id=tenant_id,
        document_kind=SETTLEMENT_REVERSAL_DOCUMENT_KIND,
        document_id=str(reversal.id),
        description=reversal.return_ref,
        posting_date=posting_date,
        currency_code=liability.currency_code,
        minor_units=liability.minor_units,
        raw=[
            (bank, _DEBIT, reversal.amount, ()),
            (account, _CREDIT, reversal.amount, ()),
        ],
    )


def build_claim_liability_adjustment_consequence(
    db: Session, *, tenant_id: UUID, adjustment_id: UUID, posting_date: date
) -> ExpenseAccountingConsequence:
    """Adjustment fact. A write-down (negative amount) is Dr liability account
    / Cr offset; an increase is Dr offset / Cr liability account. Calls
    ``db.flush()``."""
    db.flush()
    adjustment = db.scalar(
        select(ClaimLiabilityAdjustment).where(
            ClaimLiabilityAdjustment.tenant_id == tenant_id,
            ClaimLiabilityAdjustment.id == adjustment_id,
        )
    )
    if adjustment is None:
        raise NotFound("claim liability adjustment not found")
    liability = _liability(db, tenant_id, adjustment.liability_id)
    account = _clean_ref(liability.liability_account_ref, "liability account")
    offset = _clean_ref(adjustment.offset_account_ref, "offset account")
    if offset == account:
        raise refuse(
            InvalidCommand,
            "offset account must differ from the liability account",
            RefusalCode.OFFSET_IS_LIABILITY_ACCOUNT,
        )
    amount = abs(adjustment.amount)
    if adjustment.amount < 0:
        raw: list[_Raw] = [(account, _DEBIT, amount, ()), (offset, _CREDIT, amount, ())]
    else:
        raw = [(offset, _DEBIT, amount, ()), (account, _CREDIT, amount, ())]
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
    "CONSEQUENCE_DOCUMENT_KINDS",
    "CONSEQUENCE_OWNER",
    "CONSEQUENCE_SOURCE_VERSION",
    "LIABILITY_ADJUSTMENT_DOCUMENT_KIND",
    "LIABILITY_DOCUMENT_KIND",
    "LIABILITY_REVERSAL_DOCUMENT_KIND",
    "SETTLEMENT_DOCUMENT_KIND",
    "SETTLEMENT_REVERSAL_DOCUMENT_KIND",
    "ExpenseAccountingConsequence",
    "ExpenseAccountingEntry",
    "ExpenseConsequenceSource",
    "build_claim_liability_adjustment_consequence",
    "build_claim_liability_consequence",
    "build_claim_liability_reversal_consequence",
    "build_claim_settlement_consequence",
    "build_claim_settlement_reversal_consequence",
]
