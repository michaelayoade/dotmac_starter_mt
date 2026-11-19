"""Flush-only owner for the staff claim liability lifecycle (a2).

An approved claim is accepted for reimbursement into one ``ClaimLiability``:
Dr expense / Cr claims-payable is the GL consequence the assembly posts (see
``consequences``). The liability then moves OPEN -> PARTIALLY_SETTLED ->
SETTLED through settlements, bank returns and signed adjustments, or OPEN ->
REVERSED when the acceptance is reversed. Status is never set directly: it is
derived from the amounts after every transition and held by database CHECKs.

Every command runs in ``execute_once`` under a permanent unique row, takes the
row locks it needs, flushes and returns; the caller owns the transaction.
Nothing here posts to a ledger, moves money or reads a bank.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict, execute_once, fingerprint_of
from dotmac_kernel.models import Party
from dotmac_kernel.money import Money, currency
from dotmac_kernel.semantic_encoding import canonical_instant
from sqlalchemy import ColumnElement, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, scoped_session

from dotmac_expenses.consequences import (
    LIABILITY_ADJUSTMENT_DOCUMENT_KIND,
    LIABILITY_DOCUMENT_KIND,
    LIABILITY_REVERSAL_DOCUMENT_KIND,
    SETTLEMENT_DOCUMENT_KIND,
    SETTLEMENT_REVERSAL_DOCUMENT_KIND,
    ExpenseAccountingConsequence,
    build_claim_liability_adjustment_consequence,
    build_claim_liability_consequence,
    build_claim_liability_reversal_consequence,
    build_claim_settlement_consequence,
    build_claim_settlement_reversal_consequence,
)
from dotmac_expenses.contracts import (
    INPUT_VAT_IMMEDIATE_ACCOUNT_REF,
    AcceptClaimForReimbursement,
    ClaimAccountingReceiptInput,
    ClaimInputVat,
    ClaimLiabilityStatus,
    ClaimStatus,
    Conflict,
    HistoricalClaimLiabilityCommand,
    HistoricalClaimLiabilityEvidence,
    HistoricalClaimLiabilityVerifier,
    InvalidCommand,
    InvalidLifecycle,
    LiabilityOrigin,
    NotFound,
    RefusalCode,
    refuse,
)
from dotmac_expenses.models import (
    ClaimAccountingReceipt,
    ClaimLiability,
    ClaimLiabilityAdjustment,
    ClaimLiabilityLine,
    ClaimLiabilityReversal,
    ClaimLiabilitySettlement,
    ClaimSettlementReversal,
    ExpenseClaim,
    HistoricalClaimReceipt,
)
from dotmac_expenses.service import _claim, _event, reimbursement_eligibility

_SHA256_HEX = frozenset("0123456789abcdef")


# --- small shared helpers ------------------------------------------------------------


def _clean(value: str, label: str, maximum: int = 240) -> str:
    if not isinstance(value, str):
        raise InvalidCommand(f"{label} is required")
    cleaned = value.strip()
    if not cleaned:
        raise InvalidCommand(f"{label} is required")
    if len(cleaned) > maximum:
        raise InvalidCommand(f"{label} is too long")
    return cleaned


def _aware(value: datetime, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise InvalidCommand(f"{label} must be timezone-aware")
    return value


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _scaled(value: Decimal, minor_units: int, label: str) -> Decimal:
    quantum = Decimal(1).scaleb(-minor_units)
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value != value.quantize(quantum)
    ):
        raise InvalidCommand(f"{label} is not at the currency scale")
    return value


def _digest(value: str, label: str) -> str:
    """A canonical ``cv1:`` + 64-hex historical evidence fingerprint."""
    if not isinstance(value, str) or not value.startswith("cv1:"):
        raise InvalidCommand(f"{label} requires a canonical cv1 fingerprint")
    _sha256(value[4:], label)
    return value


def _sha256(value: str, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or not set(value) <= _SHA256_HEX:
        raise InvalidCommand(f"{label} must be a 64-character lowercase SHA-256")
    return value


def _once(
    db: Session,
    *,
    tenant_id: UUID,
    scope_name: str,
    key: str,
    expires_at: datetime | None,
    fingerprint: str,
    operation: Callable[[Session], Mapping[str, object]],
) -> Mapping[str, object]:
    try:
        outcome = execute_once(
            db,
            tenant_id=tenant_id,
            scope=scope_name,
            key=key,
            operation=operation,
            fingerprint=fingerprint,
            expires_at=expires_at,
        )
    except IdempotencyConflict as exc:
        raise refuse(
            Conflict,
            "idempotency key was reused with a different request",
            RefusalCode.IDEMPOTENCY_KEY_REUSED,
        ) from exc
    except IntegrityError as exc:
        raise Conflict("a permanent identity of this command already exists") from exc
    return outcome.result


def effective_claim_liability_amount(liability: ClaimLiability) -> Decimal:
    """The amount owed: the acceptance-bound ``amount`` plus signed adjustments."""
    return liability.amount + (liability.amount_adjusted or Decimal(0))


def derive_claim_liability_status(liability: ClaimLiability) -> ClaimLiabilityStatus:
    """The one status rule (payroll a6): settled when settled == effective (a
    full write-down is settled with settled 0), open when nothing is settled,
    otherwise partially settled. REVERSED is never derived."""
    if liability.amount_settled == effective_claim_liability_amount(liability):
        return ClaimLiabilityStatus.SETTLED
    if liability.amount_settled == 0:
        return ClaimLiabilityStatus.OPEN
    return ClaimLiabilityStatus.PARTIALLY_SETTLED


def claim_liability_outstanding(liability: ClaimLiability) -> Decimal:
    return effective_claim_liability_amount(liability) - liability.amount_settled


def _liability(
    db: Session, tenant_id: UUID, liability_id: UUID, *, lock: bool = False
) -> ClaimLiability:
    query = select(ClaimLiability).where(
        ClaimLiability.tenant_id == tenant_id, ClaimLiability.id == liability_id
    )
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None:
        raise NotFound("claim liability not found")
    return row


def _liability_for_claim(
    db: Session, tenant_id: UUID, claim_id: UUID
) -> ClaimLiability | None:
    return db.scalar(
        select(ClaimLiability).where(
            ClaimLiability.tenant_id == tenant_id, ClaimLiability.claim_id == claim_id
        )
    )


def _exists(
    db: Session, model: Any, tenant_id: UUID, *predicates: ColumnElement[bool]
) -> bool:
    return (
        db.scalar(
            select(model.id).where(model.tenant_id == tenant_id, *predicates).limit(1)
        )
        is not None
    )


def _require_unreversed(liability: ClaimLiability) -> None:
    if liability.status == ClaimLiabilityStatus.REVERSED.value:
        raise refuse(
            InvalidLifecycle,
            "the claim liability was reversed",
            RefusalCode.LIABILITY_REVERSED,
        )


def _dimension_pairs(raw: object) -> list[list[str]]:
    if not isinstance(raw, tuple | list):
        raise InvalidCommand("dimension refs must be a sequence of pairs")
    pairs: list[list[str]] = []
    for item in raw:
        if not isinstance(item, tuple | list) or len(item) != 2:
            raise InvalidCommand("a dimension ref must be a (name, value) pair")
        pairs.append(
            [_clean(item[0], "dimension name", 80), _clean(item[1], "dimension", 240)]
        )
    return pairs


# --- accept / reverse acceptance -----------------------------------------------------


def accept_claim_for_reimbursement(
    db: Session,
    *,
    scope: TenantScope,
    command: AcceptClaimForReimbursement,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    accepted_at: datetime,
) -> ClaimLiability:
    """Accept one APPROVED, eligible claim: write its liability and lines.

    The claim's own status stays APPROVED. Every account ref is an explicit
    command field frozen in rows (no metadata); the VAT mode (the
    ``expenses.claim_input_vat`` setting, read by the assembly) is frozen too,
    so a later setting change never alters an accepted claim. A replay with the
    same facts returns the existing liability; any difference is a conflict.
    """
    tenant_id = scope.tenant_id
    liability_account = _clean(command.liability_account_ref, "liability account")
    reference = _clean(command.acceptance_reference, "acceptance reference")
    actor = _clean(command.accepted_by, "accepted by", 255)
    _aware(accepted_at, "accepted_at")
    try:
        mode = ClaimInputVat(command.input_vat_mode)
    except ValueError as exc:
        raise InvalidCommand("input VAT mode must be 'none' or 'immediate'") from exc
    if not isinstance(command.lines, tuple) or not command.lines:
        raise refuse(
            InvalidCommand,
            "acceptance must name every approved claim line",
            RefusalCode.ACCEPTANCE_LINES_MISMATCH,
        )
    by_line: dict[UUID, dict[str, object]] = {}
    for item in command.lines:
        if item.claim_line_id in by_line:
            raise refuse(
                InvalidCommand,
                "a claim line is named more than once",
                RefusalCode.ACCEPTANCE_LINES_MISMATCH,
            )
        expense = _clean(item.expense_account_ref, "expense account")
        tax_ref = (
            None
            if item.tax_account_ref is None
            else _clean(item.tax_account_ref, "tax account")
        )
        if not isinstance(item.tax_amount, Decimal) or not item.tax_amount.is_finite():
            raise InvalidCommand("tax amount must be a finite decimal")
        if item.tax_amount < 0:
            raise InvalidCommand("tax amount must not be negative")
        if item.tax_amount > 0 and tax_ref is None:
            raise InvalidCommand("a tax amount needs a tax account")
        for ref in (expense, tax_ref):
            if ref is not None and ref == liability_account:
                raise refuse(
                    InvalidCommand,
                    "an account ref equals the liability account",
                    RefusalCode.ACCEPTANCE_ACCOUNT_CONFLICT,
                )
        if tax_ref is not None and tax_ref == expense:
            raise refuse(
                InvalidCommand,
                "the tax account equals the expense account",
                RefusalCode.ACCEPTANCE_ACCOUNT_CONFLICT,
            )
        if item.tax_amount > 0:
            if mode is ClaimInputVat.NONE:
                raise refuse(
                    InvalidCommand,
                    "input VAT is not recoverable on staff claims "
                    "(expenses.claim_input_vat is 'none')",
                    RefusalCode.CLAIM_VAT_DISABLED,
                )
            if tax_ref != INPUT_VAT_IMMEDIATE_ACCOUNT_REF:
                raise refuse(
                    InvalidCommand,
                    "input VAT must go to the immediate input-VAT account",
                    RefusalCode.CLAIM_VAT_ACCOUNT_NOT_IMMEDIATE,
                )
        by_line[item.claim_line_id] = {
            "expense_account_ref": expense,
            "dimension_refs": _dimension_pairs(item.dimension_refs),
            "tax_account_ref": tax_ref,
            "tax_amount": item.tax_amount,
        }
    payload = {
        "claim_id": str(command.claim_id),
        "liability_account_ref": liability_account,
        "acceptance_reference": reference,
        "accepted_by": actor,
        "input_vat_mode": mode.value,
        "lines": [
            {
                "claim_line_id": str(line_id),
                "expense_account_ref": facts["expense_account_ref"],
                "dimension_refs": facts["dimension_refs"],
                "tax_account_ref": facts["tax_account_ref"],
                "tax_amount": str(facts["tax_amount"]),
            }
            for line_id, facts in sorted(by_line.items(), key=lambda kv: str(kv[0]))
        ],
    }

    def replay_matches(session: Session, existing: ClaimLiability) -> bool:
        if (
            existing.origin != LiabilityOrigin.ACCEPTED.value
            or existing.liability_account_ref != liability_account
            or existing.acceptance_reference != reference
            or existing.accepted_by != actor
            or existing.input_vat_mode != mode.value
        ):
            return False
        rows = list(
            session.scalars(
                select(ClaimLiabilityLine).where(
                    ClaimLiabilityLine.tenant_id == tenant_id,
                    ClaimLiabilityLine.liability_id == existing.id,
                )
            )
        )
        stored = {
            row.claim_line_id: {
                "expense_account_ref": row.expense_account_ref,
                "dimension_refs": [list(pair) for pair in row.dimension_refs],
                "tax_account_ref": row.tax_account_ref,
                "tax_amount": row.tax_amount,
            }
            for row in rows
        }
        return stored == by_line

    def operation(session: Session) -> Mapping[str, object]:
        claim = _claim(
            session, tenant_id=tenant_id, claim_id=command.claim_id, lock=True
        )
        existing = _liability_for_claim(session, tenant_id, claim.id)
        if existing is not None:
            if replay_matches(session, existing):
                return {"liability_id": str(existing.id)}
            raise refuse(
                Conflict,
                "the claim already has a liability",
                RefusalCode.CLAIM_ALREADY_ACCEPTED,
            )
        eligibility = reimbursement_eligibility(session, scope=scope, claim_id=claim.id)
        if not eligibility.eligible:
            raise refuse(
                InvalidLifecycle,
                "the claim is not eligible for reimbursement: "
                + ", ".join(eligibility.reasons),
                RefusalCode.CLAIM_NOT_ELIGIBLE,
            )
        if claim.status != ClaimStatus.APPROVED:
            raise refuse(
                InvalidLifecycle,
                "only an approved claim can be accepted",
                RefusalCode.CLAIM_NOT_ELIGIBLE,
            )
        minor_units = currency(claim.currency_code).minor_units
        approved_lines = [
            line
            for line in sorted(claim.lines, key=lambda x: (x.sequence, x.created_at))
            if (line.approved_amount or Decimal(0)) > 0
        ]
        if {line.id for line in approved_lines} != set(by_line):
            raise refuse(
                InvalidCommand,
                "acceptance must name every approved claim line exactly once",
                RefusalCode.ACCEPTANCE_LINES_MISMATCH,
            )
        total = claim.total_approved_amount or Decimal(0)
        if (
            sum(
                (line.approved_amount or Decimal(0) for line in approved_lines),
                Decimal(0),
            )
            != total
        ):
            raise refuse(
                InvalidCommand,
                "approved line amounts do not sum to the approved total",
                RefusalCode.ACCEPTANCE_LINES_MISMATCH,
            )
        _scaled(total, minor_units, "approved total")
        liability = ClaimLiability(
            tenant_id=tenant_id,
            claim_id=claim.id,
            claimant_party_id=claim.claimant_party_id,
            currency_code=claim.currency_code,
            minor_units=minor_units,
            liability_account_ref=liability_account,
            origin=LiabilityOrigin.ACCEPTED.value,
            input_vat_mode=mode.value,
            acceptance_reference=reference,
            accepted_by=actor,
            accepted_at=accepted_at,
            amount=total,
            amount_settled=Decimal(0),
            amount_adjusted=Decimal(0),
            status=ClaimLiabilityStatus.OPEN.value,
        )
        session.add(liability)
        session.flush()
        for number, line in enumerate(approved_lines, start=1):
            facts = by_line[line.id]
            tax = _scaled(facts["tax_amount"], minor_units, "tax amount")  # type: ignore[arg-type]
            approved = _scaled(
                line.approved_amount or Decimal(0), minor_units, "approved amount"
            )
            if tax >= approved:
                raise refuse(
                    InvalidCommand,
                    "a tax amount must be less than the approved amount",
                    RefusalCode.ACCEPTANCE_LINES_MISMATCH,
                )
            session.add(
                ClaimLiabilityLine(
                    tenant_id=tenant_id,
                    liability_id=liability.id,
                    claim_line_id=line.id,
                    line_number=number,
                    expense_account_ref=facts["expense_account_ref"],
                    dimension_refs=facts["dimension_refs"],
                    tax_account_ref=facts["tax_account_ref"],
                    net_amount=approved - tax,
                    tax_amount=tax,
                )
            )
        _event(
            session,
            tenant_id=tenant_id,
            request_id=None,
            claim_id=claim.id,
            from_status=claim.status.value,
            to_status="liability_open",
            actor_reference=actor,
            occurred_at=accepted_at,
            decision_reference=reference,
        )
        session.flush()
        return {"liability_id": str(liability.id)}

    result = _once(
        db,
        tenant_id=tenant_id,
        scope_name="expenses.accept_claim_for_reimbursement",
        key=idempotency_key,
        expires_at=idempotency_expires_at,
        fingerprint=fingerprint_of(payload),
        operation=operation,
    )
    return _liability(db, tenant_id, UUID(str(result["liability_id"])))


def reverse_claim_acceptance(
    db: Session,
    *,
    scope: TenantScope,
    liability_id: UUID,
    reason: str,
    evidence_ref: str,
    reversed_by: str,
    reversed_at: datetime,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
) -> ClaimLiabilityReversal:
    """Reverse an acceptance: only from OPEN, origin accepted, with no
    settlement row and no adjustment row. Terminal; afterwards the claim's
    approval can be withdrawn."""
    tenant_id = scope.tenant_id
    why = _clean(reason, "reversal reason", 4000)
    evidence = _clean(evidence_ref, "reversal evidence reference")
    actor = _clean(reversed_by, "reversed by", 255)
    _aware(reversed_at, "reversed_at")
    payload = {
        "liability_id": str(liability_id),
        "reason": why,
        "evidence_ref": evidence,
        "reversed_by": actor,
    }

    def operation(session: Session) -> Mapping[str, object]:
        liability = _liability(session, tenant_id, liability_id, lock=True)
        existing = session.scalar(
            select(ClaimLiabilityReversal).where(
                ClaimLiabilityReversal.tenant_id == tenant_id,
                ClaimLiabilityReversal.liability_id == liability.id,
            )
        )
        if existing is not None:
            if (
                existing.reason != why
                or existing.evidence_ref != evidence
                or existing.reversed_by != actor
            ):
                raise Conflict("the acceptance was already reversed differently")
            return {"reversal_id": str(existing.id)}
        if (
            liability.origin != LiabilityOrigin.ACCEPTED.value
            or liability.status != ClaimLiabilityStatus.OPEN.value
            or liability.amount_settled != 0
            or liability.amount_adjusted != 0
            or _exists(
                session,
                ClaimLiabilitySettlement,
                tenant_id,
                ClaimLiabilitySettlement.liability_id == liability.id,
            )
            or _exists(
                session,
                ClaimLiabilityAdjustment,
                tenant_id,
                ClaimLiabilityAdjustment.liability_id == liability.id,
            )
        ):
            raise refuse(
                InvalidLifecycle,
                "only an untouched, accepted, open liability can be reversed",
                RefusalCode.LIABILITY_NOT_OPEN,
            )
        if _as_utc(reversed_at) < _as_utc(liability.accepted_at):
            raise InvalidCommand("acceptance cannot be reversed before it happened")
        row = ClaimLiabilityReversal(
            tenant_id=tenant_id,
            liability_id=liability.id,
            reason=why,
            evidence_ref=evidence,
            reversed_by=actor,
            reversed_at=reversed_at,
        )
        session.add(row)
        session.flush()
        liability.status = ClaimLiabilityStatus.REVERSED.value
        _event(
            session,
            tenant_id=tenant_id,
            request_id=None,
            claim_id=liability.claim_id,
            from_status="liability_open",
            to_status="liability_reversed",
            actor_reference=actor,
            occurred_at=reversed_at,
            decision_reference=evidence,
            reason=why,
        )
        session.flush()
        return {"reversal_id": str(row.id)}

    result = _once(
        db,
        tenant_id=tenant_id,
        scope_name="expenses.reverse_claim_acceptance",
        key=idempotency_key,
        expires_at=idempotency_expires_at,
        fingerprint=fingerprint_of(payload),
        operation=operation,
    )
    row = db.scalar(
        select(ClaimLiabilityReversal).where(
            ClaimLiabilityReversal.tenant_id == tenant_id,
            ClaimLiabilityReversal.id == UUID(str(result["reversal_id"])),
        )
    )
    if row is None:
        raise NotFound("claim liability reversal not found")
    return row


# --- settlement / bank return / adjustment -------------------------------------------


def record_claim_settlement(
    db: Session,
    *,
    scope: TenantScope,
    liability_id: UUID,
    amount: Money,
    settlement_ref: str,
    evidence_ref: str,
    value_date: date,
    recorded_by: str,
    recorded_at: datetime,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
) -> ClaimLiabilitySettlement:
    """Record money a human already paid out against one liability.

    The liability row is locked FOR UPDATE; ``0 < amount <= effective -
    settled``. An identical replay returns the row; a differing one, or a
    replay after the settlement was returned, is a conflict (re-pay under a new
    reference).
    """
    tenant_id = scope.tenant_id
    ref = _clean(settlement_ref, "settlement reference")
    evidence = _clean(evidence_ref, "settlement evidence reference")
    actor = _clean(recorded_by, "recorded by", 255)
    _aware(recorded_at, "recorded_at")
    if not isinstance(value_date, date) or isinstance(value_date, datetime):
        raise InvalidCommand("value date must be a date")
    if not isinstance(amount, Money):
        raise InvalidCommand("settlement amount must be Money")
    payload = {
        "liability_id": str(liability_id),
        "amount": str(amount.amount),
        "currency": amount.currency.code,
        "minor_units": amount.currency.minor_units,
        "settlement_ref": ref,
        "evidence_ref": evidence,
        "value_date": value_date.isoformat(),
        "recorded_by": actor,
    }

    def operation(session: Session) -> Mapping[str, object]:
        liability = _liability(session, tenant_id, liability_id, lock=True)
        existing = session.scalar(
            select(ClaimLiabilitySettlement).where(
                ClaimLiabilitySettlement.tenant_id == tenant_id,
                ClaimLiabilitySettlement.liability_id == liability.id,
                ClaimLiabilitySettlement.settlement_ref == ref,
            )
        )
        if existing is not None:
            if (
                existing.amount != amount.amount
                or existing.evidence_ref != evidence
                or existing.value_date != value_date
                or existing.recorded_by != actor
            ):
                raise Conflict(
                    "settlement reference was reused with different evidence"
                )
            if _exists(
                session,
                ClaimSettlementReversal,
                tenant_id,
                ClaimSettlementReversal.settlement_id == existing.id,
            ):
                raise Conflict(
                    "settlement was returned; record re-payment under a new reference"
                )
            return {"settlement_id": str(existing.id)}
        _require_unreversed(liability)
        if (
            amount.currency.code != liability.currency_code
            or amount.currency.minor_units != liability.minor_units
        ):
            raise InvalidCommand("settlement uses the wrong liability currency")
        if amount.amount <= 0:
            raise InvalidCommand("settlement amount must be positive")
        if amount.amount > claim_liability_outstanding(liability):
            raise refuse(
                InvalidCommand,
                "settlement exceeds the outstanding liability",
                RefusalCode.SETTLEMENT_EXCEEDS_OUTSTANDING,
            )
        row = ClaimLiabilitySettlement(
            tenant_id=tenant_id,
            liability_id=liability.id,
            amount=amount.amount,
            settlement_ref=ref,
            evidence_ref=evidence,
            value_date=value_date,
            recorded_by=actor,
            recorded_at=recorded_at,
        )
        session.add(row)
        liability.amount_settled += amount.amount
        liability.status = derive_claim_liability_status(liability).value
        session.flush()
        return {"settlement_id": str(row.id)}

    result = _once(
        db,
        tenant_id=tenant_id,
        scope_name="expenses.record_claim_settlement",
        key=idempotency_key,
        expires_at=idempotency_expires_at,
        fingerprint=fingerprint_of(payload),
        operation=operation,
    )
    row = db.scalar(
        select(ClaimLiabilitySettlement).where(
            ClaimLiabilitySettlement.tenant_id == tenant_id,
            ClaimLiabilitySettlement.id == UUID(str(result["settlement_id"])),
        )
    )
    if row is None:
        raise NotFound("claim settlement not found")
    return row


def _receipt_exists(
    db: Session, tenant_id: UUID, document_kind: str, document_id: UUID
) -> bool:
    return _exists(
        db,
        ClaimAccountingReceipt,
        tenant_id,
        ClaimAccountingReceipt.document_kind == document_kind,
        ClaimAccountingReceipt.document_id == document_id,
    )


def reverse_claim_settlement(
    db: Session,
    *,
    scope: TenantScope,
    settlement_id: UUID,
    return_ref: str,
    reason: str,
    reversed_by: str,
    reversed_at: datetime,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    require_distinct_actor: bool = False,
) -> ClaimSettlementReversal:
    """Reverse one whole settlement whose bank credit was returned.

    The liability reopens for re-payment: an append-only reversal row is
    inserted and ``amount_settled`` falls by exactly the settlement amount. The
    settlement must be in the ledger (its consequence has a receipt). The
    reverser must differ from the recorder only when the caller passes
    ``require_distinct_actor`` (the ERP sets it from the row's stamped dual
    control); there is no compulsory second person. Caller duty: the cash GL of
    the reversal consequence is the ORIGINAL settlement journal's.
    """
    tenant_id = scope.tenant_id
    ref = _clean(return_ref, "bank return reference")
    why = _clean(reason, "reversal reason", 4000)
    actor = _clean(reversed_by, "reversed by", 255)
    _aware(reversed_at, "reversed_at")
    payload = {
        "settlement_id": str(settlement_id),
        "return_ref": ref,
        "reason": why,
        "reversed_by": actor,
        "require_distinct_actor": bool(require_distinct_actor),
    }

    def operation(session: Session) -> Mapping[str, object]:
        settlement = session.scalar(
            select(ClaimLiabilitySettlement).where(
                ClaimLiabilitySettlement.tenant_id == tenant_id,
                ClaimLiabilitySettlement.id == settlement_id,
            )
        )
        if settlement is None:
            raise NotFound("claim settlement not found")
        liability = _liability(session, tenant_id, settlement.liability_id, lock=True)
        existing = session.scalar(
            select(ClaimSettlementReversal).where(
                ClaimSettlementReversal.tenant_id == tenant_id,
                ClaimSettlementReversal.settlement_id == settlement.id,
            )
        )
        if existing is not None:
            if (
                existing.return_ref != ref
                or existing.reason != why
                or existing.reversed_by != actor
            ):
                raise Conflict(
                    "settlement was already returned with different evidence"
                )
            return {"reversal_id": str(existing.id)}
        if _as_utc(reversed_at) < _as_utc(settlement.recorded_at):
            raise InvalidCommand("settlement cannot be returned before it was recorded")
        if require_distinct_actor and actor == settlement.recorded_by:
            raise refuse(
                InvalidLifecycle,
                "the bank return must be attested by someone other than the recorder",
                RefusalCode.DISTINCT_ACTOR_REQUIRED,
            )
        if not _receipt_exists(
            session, tenant_id, SETTLEMENT_DOCUMENT_KIND, settlement.id
        ):
            raise refuse(
                InvalidLifecycle,
                "the settlement has no accounting receipt, so it is not in the ledger",
                RefusalCode.SETTLEMENT_NOT_IN_LEDGER,
            )
        row = ClaimSettlementReversal(
            tenant_id=tenant_id,
            settlement_id=settlement.id,
            liability_id=liability.id,
            amount=settlement.amount,
            return_ref=ref,
            reason=why,
            reversed_by=actor,
            reversed_at=reversed_at,
        )
        session.add(row)
        liability.amount_settled -= settlement.amount
        liability.status = derive_claim_liability_status(liability).value
        session.flush()
        return {"reversal_id": str(row.id)}

    result = _once(
        db,
        tenant_id=tenant_id,
        scope_name="expenses.reverse_claim_settlement",
        key=idempotency_key,
        expires_at=idempotency_expires_at,
        fingerprint=fingerprint_of(payload),
        operation=operation,
    )
    row = db.scalar(
        select(ClaimSettlementReversal).where(
            ClaimSettlementReversal.tenant_id == tenant_id,
            ClaimSettlementReversal.id == UUID(str(result["reversal_id"])),
        )
    )
    if row is None:
        raise NotFound("claim settlement reversal not found")
    return row


def adjust_claim_liability(
    db: Session,
    *,
    scope: TenantScope,
    liability_id: UUID,
    amount: Money,
    adjustment_ref: str,
    reason: str,
    evidence_ref: str,
    offset_account_ref: str,
    adjusted_by: str,
    adjusted_at: datetime,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
) -> ClaimLiabilityAdjustment:
    """Apply one signed, audited, append-only adjustment (payroll a6 contract).

    A negative amount writes the liability down (an abandoned claim, a leaver)
    and may not exceed the outstanding balance; a positive amount increases it.
    ``amount`` on the liability never changes; the signed sum lives in
    ``amount_adjusted``. Corrections are made forward with an opposite
    adjustment. No second person is required.
    """
    tenant_id = scope.tenant_id
    ref = _clean(adjustment_ref, "adjustment reference")
    why = _clean(reason, "adjustment reason", 4000)
    evidence = _clean(evidence_ref, "adjustment evidence reference")
    offset = _clean(offset_account_ref, "offset account reference")
    actor = _clean(adjusted_by, "adjusted by", 255)
    _aware(adjusted_at, "adjusted_at")
    if not isinstance(amount, Money):
        raise InvalidCommand("adjustment amount must be Money")
    if amount.amount == 0:
        raise InvalidCommand("adjustment amount must not be zero")
    payload = {
        "liability_id": str(liability_id),
        "amount": str(amount.amount),
        "currency": amount.currency.code,
        "minor_units": amount.currency.minor_units,
        "adjustment_ref": ref,
        "reason": why,
        "evidence_ref": evidence,
        "offset_account_ref": offset,
        "adjusted_by": actor,
    }

    def operation(session: Session) -> Mapping[str, object]:
        liability = _liability(session, tenant_id, liability_id, lock=True)
        existing = session.scalar(
            select(ClaimLiabilityAdjustment).where(
                ClaimLiabilityAdjustment.tenant_id == tenant_id,
                ClaimLiabilityAdjustment.liability_id == liability.id,
                ClaimLiabilityAdjustment.adjustment_ref == ref,
            )
        )
        if existing is not None:
            if (
                existing.amount != amount.amount
                or existing.reason != why
                or existing.evidence_ref != evidence
                or existing.offset_account_ref != offset
                or existing.adjusted_by != actor
            ):
                raise Conflict(
                    "adjustment reference was reused with different evidence"
                )
            return {"adjustment_id": str(existing.id)}
        _require_unreversed(liability)
        if (
            amount.currency.code != liability.currency_code
            or amount.currency.minor_units != liability.minor_units
        ):
            raise InvalidCommand("adjustment uses the wrong liability currency")
        if offset == liability.liability_account_ref.strip():
            raise refuse(
                InvalidCommand,
                "offset account must differ from the liability account",
                RefusalCode.OFFSET_IS_LIABILITY_ACCOUNT,
            )
        if _as_utc(adjusted_at) < _as_utc(liability.accepted_at):
            raise InvalidCommand("liability cannot be adjusted before it existed")
        if amount.amount < 0 and -amount.amount > claim_liability_outstanding(
            liability
        ):
            raise refuse(
                InvalidCommand,
                "write-down exceeds the outstanding liability",
                RefusalCode.ADJUSTMENT_EXCEEDS_OUTSTANDING,
            )
        row = ClaimLiabilityAdjustment(
            tenant_id=tenant_id,
            liability_id=liability.id,
            amount=amount.amount,
            adjustment_ref=ref,
            reason=why,
            evidence_ref=evidence,
            offset_account_ref=offset,
            adjusted_by=actor,
            adjusted_at=adjusted_at,
        )
        session.add(row)
        liability.amount_adjusted = (
            liability.amount_adjusted or Decimal(0)
        ) + amount.amount
        liability.status = derive_claim_liability_status(liability).value
        session.flush()
        return {"adjustment_id": str(row.id)}

    result = _once(
        db,
        tenant_id=tenant_id,
        scope_name="expenses.adjust_claim_liability",
        key=idempotency_key,
        expires_at=idempotency_expires_at,
        fingerprint=fingerprint_of(payload),
        operation=operation,
    )
    row = db.scalar(
        select(ClaimLiabilityAdjustment).where(
            ClaimLiabilityAdjustment.tenant_id == tenant_id,
            ClaimLiabilityAdjustment.id == UUID(str(result["adjustment_id"])),
        )
    )
    if row is None:
        raise NotFound("claim liability adjustment not found")
    return row


# --- accounting receipt --------------------------------------------------------------


def _rebuild_consequence(
    db: Session, *, tenant_id: UUID, command: ClaimAccountingReceiptInput
) -> ExpenseAccountingConsequence:
    kind = command.document_kind.strip()
    document_id = command.document_id
    if kind == LIABILITY_DOCUMENT_KIND:
        return build_claim_liability_consequence(
            db, tenant_id=tenant_id, liability_id=document_id
        )
    if kind == LIABILITY_REVERSAL_DOCUMENT_KIND:
        return build_claim_liability_reversal_consequence(
            db,
            tenant_id=tenant_id,
            reversal_id=document_id,
            posting_date=command.posting_date,
        )
    if kind == SETTLEMENT_DOCUMENT_KIND:
        return build_claim_settlement_consequence(
            db,
            tenant_id=tenant_id,
            settlement_id=document_id,
            bank_account_ref=command.bank_account_ref or "",
        )
    if kind == SETTLEMENT_REVERSAL_DOCUMENT_KIND:
        if command.posting_date is None:
            raise InvalidCommand("a settlement return receipt needs a posting date")
        return build_claim_settlement_reversal_consequence(
            db,
            tenant_id=tenant_id,
            reversal_id=document_id,
            bank_account_ref=command.bank_account_ref or "",
            posting_date=command.posting_date,
        )
    if kind == LIABILITY_ADJUSTMENT_DOCUMENT_KIND:
        if command.posting_date is None:
            raise InvalidCommand("an adjustment receipt needs a posting date")
        return build_claim_liability_adjustment_consequence(
            db,
            tenant_id=tenant_id,
            adjustment_id=document_id,
            posting_date=command.posting_date,
        )
    raise InvalidCommand("unknown accounting receipt document kind")


def record_claim_accounting_receipt(
    db: Session,
    *,
    scope: TenantScope,
    command: ClaimAccountingReceiptInput,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    recorded_at: datetime,
) -> ClaimAccountingReceipt:
    """Record that a consequence was posted, bound to its exact fingerprint.

    The owner rebuilds the consequence and compares; a mismatch is a conflict.
    One permanent receipt per (kind, document). A settlement can be returned
    only once its settlement receipt exists."""
    tenant_id = scope.tenant_id
    consequence = _rebuild_consequence(db, tenant_id=tenant_id, command=command)
    kind = command.document_kind.strip()
    claimed = _sha256(command.consequence_fingerprint, "consequence fingerprint")
    if claimed != consequence.fingerprint:
        raise Conflict("accounting receipt does not match the current consequence")
    reference = _clean(command.accounting_reference, "accounting reference", 255)
    evidence = _sha256(
        command.accounting_evidence_fingerprint, "accounting evidence fingerprint"
    )
    _aware(recorded_at, "recorded_at")
    payload = {
        "document_kind": kind,
        "document_id": str(command.document_id),
        "consequence_fingerprint": claimed,
        "accounting_reference": reference,
        "accounting_evidence_fingerprint": evidence,
    }

    def operation(session: Session) -> Mapping[str, object]:
        existing = session.scalar(
            select(ClaimAccountingReceipt).where(
                ClaimAccountingReceipt.tenant_id == tenant_id,
                ClaimAccountingReceipt.document_kind == kind,
                ClaimAccountingReceipt.document_id == command.document_id,
            )
        )
        if existing is not None:
            if (
                existing.consequence_fingerprint != claimed
                or existing.accounting_reference != reference
                or existing.accounting_evidence_fingerprint != evidence
            ):
                raise Conflict("document already has different accounting evidence")
            return {"receipt_id": str(existing.id)}
        receipt = ClaimAccountingReceipt(
            tenant_id=tenant_id,
            document_kind=kind,
            document_id=command.document_id,
            consequence_fingerprint=claimed,
            accounting_reference=reference,
            accounting_evidence_fingerprint=evidence,
            recorded_at=recorded_at,
        )
        session.add(receipt)
        session.flush()
        return {"receipt_id": str(receipt.id)}

    result = _once(
        db,
        tenant_id=tenant_id,
        scope_name="expenses.record_claim_accounting_receipt",
        key=idempotency_key,
        expires_at=idempotency_expires_at,
        fingerprint=fingerprint_of(payload),
        operation=operation,
    )
    row = db.scalar(
        select(ClaimAccountingReceipt).where(
            ClaimAccountingReceipt.tenant_id == tenant_id,
            ClaimAccountingReceipt.id == UUID(str(result["receipt_id"])),
        )
    )
    if row is None:
        raise NotFound("claim accounting receipt not found")
    return row


# --- reads ---------------------------------------------------------------------------


def _live_liabilities(
    db: Session, tenant_id: UUID, *predicates: ColumnElement[bool]
) -> list[ClaimLiability]:
    return list(
        db.scalars(
            select(ClaimLiability)
            .where(
                ClaimLiability.tenant_id == tenant_id,
                ClaimLiability.status != ClaimLiabilityStatus.REVERSED.value,
                *predicates,
            )
            .order_by(ClaimLiability.accepted_at, ClaimLiability.id)
        )
    )


def claimant_claims_balance(
    db: Session,
    *,
    tenant_id: UUID,
    claimant_party_id: UUID,
    currency_code: str,
) -> Decimal:
    """What the company owes one claimant: the sum of (effective - settled) over
    the claimant's non-reversed liabilities. Every figure comes from rows."""
    rows = _live_liabilities(
        db,
        tenant_id,
        ClaimLiability.claimant_party_id == claimant_party_id,
        ClaimLiability.currency_code == currency_code.strip().upper(),
    )
    return sum((claim_liability_outstanding(row) for row in rows), Decimal(0))


def claims_payable_total(
    db: Session, *, tenant_id: UUID, liability_account_ref: str
) -> Decimal:
    """The subledger side of the control tie: the outstanding total over every
    non-reversed liability posted to ``liability_account_ref``."""
    rows = _live_liabilities(
        db,
        tenant_id,
        ClaimLiability.liability_account_ref == liability_account_ref.strip(),
    )
    return sum((claim_liability_outstanding(row) for row in rows), Decimal(0))


def open_claim_liabilities(
    db: Session,
    *,
    tenant_id: UUID,
    claimant_party_id: UUID | None = None,
    currency_code: str | None = None,
) -> list[ClaimLiability]:
    """Liabilities with something still owed (the ERP payout selector)."""
    predicates: list[ColumnElement[bool]] = []
    if claimant_party_id is not None:
        predicates.append(ClaimLiability.claimant_party_id == claimant_party_id)
    if currency_code is not None:
        predicates.append(ClaimLiability.currency_code == currency_code.strip().upper())
    return [
        row
        for row in _live_liabilities(db, tenant_id, *predicates)
        if claim_liability_outstanding(row) > 0
    ]


def claim_liability_row_totals(
    db: Session, *, tenant_id: UUID, liability_id: UUID
) -> tuple[Decimal, Decimal]:
    """(settled, adjusted) recomputed from the evidence rows: settlements less
    returns, and the adjustment sum. Must equal the projection columns."""
    settled = sum(
        (
            row.amount
            for row in db.scalars(
                select(ClaimLiabilitySettlement).where(
                    ClaimLiabilitySettlement.tenant_id == tenant_id,
                    ClaimLiabilitySettlement.liability_id == liability_id,
                )
            )
        ),
        Decimal(0),
    ) - sum(
        (
            row.amount
            for row in db.scalars(
                select(ClaimSettlementReversal).where(
                    ClaimSettlementReversal.tenant_id == tenant_id,
                    ClaimSettlementReversal.liability_id == liability_id,
                )
            )
        ),
        Decimal(0),
    )
    adjusted = sum(
        (
            row.amount
            for row in db.scalars(
                select(ClaimLiabilityAdjustment).where(
                    ClaimLiabilityAdjustment.tenant_id == tenant_id,
                    ClaimLiabilityAdjustment.liability_id == liability_id,
                )
            )
        ),
        Decimal(0),
    )
    return settled, adjusted


# --- historical admission (0 GL) -----------------------------------------------------


def _historical_money(value: object, label: str) -> None:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value != value.quantize(Decimal("0.01"))
    ):
        raise InvalidCommand(f"{label} must be exact finite two-decimal money")


def _evidence_json(evidence: HistoricalClaimLiabilityEvidence) -> dict[str, object]:
    def convert(value: object) -> object:
        if isinstance(value, datetime):
            return canonical_instant(value)
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, Decimal | UUID):
            return str(value)
        if isinstance(value, tuple | list):
            return [convert(item) for item in value]
        if is_dataclass(value) and not isinstance(value, type):
            return {f.name: convert(getattr(value, f.name)) for f in fields(value)}
        return value

    return {f.name: convert(getattr(evidence, f.name)) for f in fields(evidence)}


class _DiscardVerifierSavepoint(Exception):
    """Private sentinel: always leave the verifier savepoint by rolling it back."""


def _run_verifier(
    db: Session,
    *,
    scope: TenantScope,
    command: HistoricalClaimLiabilityCommand,
    verifier: HistoricalClaimLiabilityVerifier,
) -> tuple[ExpenseClaim, object]:
    """Lock the claim, then run the read-only verifier in a discarded savepoint."""
    locked = _claim(db, tenant_id=scope.tenant_id, claim_id=command.claim_id, lock=True)
    wrote: list[bool] = []

    def _saw_flush(session: Session, _context: object) -> None:
        if session.new or session.dirty or session.deleted:
            wrote.append(True)

    def _refuse_commit(_session: Session) -> None:
        # Refuse before a session-level commit could also commit the caller's
        # outer transaction. Keep the violation even if the verifier catches it.
        wrote.append(True)
        raise InvalidCommand("historical verifier must not write")

    evidence: object = None
    try:
        with db.begin_nested() as savepoint:
            registered: list[tuple[str, Callable[..., object]]] = []
            try:
                event.listen(db, "after_flush", _saw_flush)
                registered.append(("after_flush", _saw_flush))
                event.listen(db, "before_commit", _refuse_commit)
                registered.append(("before_commit", _refuse_commit))
                with db.no_autoflush:
                    evidence = verifier.verify(
                        scope=scope, command=command, destination=db
                    )
                    if db.new or db.dirty or db.deleted:
                        wrote.append(True)
                    if not savepoint.is_active or (
                        db.get_nested_transaction() is not savepoint
                    ):
                        wrote.append(True)
            finally:
                for name, fn in reversed(registered):
                    event.remove(db, name, fn)
            raise _DiscardVerifierSavepoint
    except _DiscardVerifierSavepoint:
        pass
    if wrote:
        raise InvalidCommand("historical verifier must not write")
    return locked, evidence


def _validate_historical_command(command: HistoricalClaimLiabilityCommand) -> None:
    for value, label, maximum in (
        (command.source_owner, "source owner", 120),
        (command.source_claim_id, "source claim id", 255),
        (command.evidence_reference, "evidence reference", 255),
        (command.admitted_by, "admitted by", 255),
    ):
        if _clean(value, label, maximum) != value:
            raise InvalidCommand(f"{label} must be canonical")
    if not isinstance(command.claim_id, UUID):
        raise InvalidCommand("claim id must be UUID")
    _sha256(command.source_snapshot_sha256, "snapshot digest")
    _digest(command.evidence_fingerprint, "evidence fingerprint")


def _check_historical_evidence(
    evidence: HistoricalClaimLiabilityEvidence,
    *,
    tenant_id: UUID,
    command: HistoricalClaimLiabilityCommand,
    admitted_at: datetime,
) -> str:
    if (
        evidence.tenant_id,
        evidence.source_owner,
        evidence.source_claim_id,
        evidence.source_snapshot_sha256,
        evidence.claim_id,
    ) != (
        tenant_id,
        command.source_owner,
        command.source_claim_id,
        command.source_snapshot_sha256,
        command.claim_id,
    ):
        raise Conflict("historical evidence identity differs from command")
    fingerprint = evidence.fingerprint()
    if fingerprint != command.evidence_fingerprint:
        raise Conflict("historical evidence fingerprint differs from command")
    _digest(evidence.accounting_evidence_fingerprint, "accounting evidence fingerprint")
    _digest(evidence.facts_fingerprint, "facts fingerprint")
    for value, label in (
        (evidence.net_payable_amount, "net payable"),
        (evidence.settled_before_admission, "settled before admission"),
        (evidence.residual, "residual"),
        (evidence.carried_control_amount, "carried control amount"),
    ):
        _historical_money(value, label)
    if evidence.claimant_party_id is None:
        raise refuse(
            InvalidCommand,
            "the legacy claim has no employee, so it has no claimant to owe",
            RefusalCode.CLAIM_WITHOUT_EMPLOYEE,
        )
    if currency(evidence.currency_code).minor_units != 2:
        raise InvalidCommand(
            "historical admission supports two-decimal currencies only"
        )
    if not isinstance(evidence.settlements, tuple):
        raise InvalidCommand("historical settlements must be a tuple")
    seen: set[str] = set()
    paid = Decimal(0)
    for item in evidence.settlements:
        _clean(item.evidence_ref, "settlement evidence reference", 255)
        _historical_money(item.amount, "historical settlement amount")
        if item.amount <= 0:
            raise InvalidCommand("a historical settlement amount must be positive")
        _digest(item.evidence_fingerprint, "settlement evidence fingerprint")
        if item.evidence_ref in seen:
            raise Conflict("historical settlement evidence references must be unique")
        seen.add(item.evidence_ref)
        paid += item.amount
    if paid != evidence.settled_before_admission:
        raise InvalidCommand("historical settlements do not sum to the declared total")
    residual = evidence.net_payable_amount - evidence.settled_before_admission
    if residual != evidence.residual or residual <= 0:
        raise refuse(
            InvalidLifecycle,
            "the historical claim has no open residual to admit",
            RefusalCode.HISTORICAL_CLAIM_NOT_OPEN,
        )
    if evidence.carried_control_amount != residual:
        raise refuse(
            InvalidCommand,
            "the legacy ledger does not hold exactly the residual of this claim",
            RefusalCode.LEGACY_GL_DOES_NOT_HOLD_CLAIM,
        )
    for ref, ref_label in (
        (evidence.liability_account_ref, "liability account"),
        (evidence.carried_account_ref, "carried account"),
        (evidence.carried_evidence_ref, "carried evidence reference"),
        (evidence.source_journal_ref, "source journal reference"),
    ):
        _clean(ref, ref_label, 255)
    _aware(evidence.original_posted_at, "original_posted_at")
    if evidence.original_posted_at > admitted_at:
        raise InvalidCommand("historical posting cannot follow admission")
    return fingerprint


def admit_historical_claim_liability(
    db: Session,
    *,
    scope: TenantScope,
    command: HistoricalClaimLiabilityCommand,
    verifier: HistoricalClaimLiabilityVerifier,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    admitted_at: datetime,
) -> HistoricalClaimReceipt:
    """Admit a legacy posted, partly unreimbursed claim as an OPEN liability.

    No GL: the legacy ledger already holds exactly the residual, which the
    trusted verifier proves and the owner re-checks. Mirrors payables
    ``admit_historical_settled_invoice``: a plain Session only, the destination
    claim locked, then the verifier runs in an always-discarded savepoint in
    which any write is refused. The replay identity includes the evidence
    reference and the admitting actor. The claim's a1 status is not touched.
    """
    _validate_historical_command(command)
    if verifier is None or not callable(getattr(verifier, "verify", None)):
        raise InvalidCommand("historical admission requires a trusted verifier")
    _aware(admitted_at, "admitted_at")
    if not isinstance(db, Session) or isinstance(db, scoped_session):
        raise InvalidCommand("historical admission requires a plain Session")
    tenant_id = scope.tenant_id
    _locked, evidence = _run_verifier(
        db, scope=scope, command=command, verifier=verifier
    )
    if not isinstance(evidence, HistoricalClaimLiabilityEvidence):
        raise InvalidCommand("historical verifier returned no typed evidence")
    fingerprint = _check_historical_evidence(
        evidence, tenant_id=tenant_id, command=command, admitted_at=admitted_at
    )
    identity = {
        "historical_evidence_fingerprint": fingerprint,
        "evidence_reference": command.evidence_reference,
        "admitted_by": command.admitted_by,
    }

    def existing_receipt(session: Session) -> HistoricalClaimReceipt | None:
        return session.scalar(
            select(HistoricalClaimReceipt).where(
                HistoricalClaimReceipt.tenant_id == tenant_id,
                HistoricalClaimReceipt.claim_id == command.claim_id,
            )
        )

    def replay_matches(receipt: HistoricalClaimReceipt) -> bool:
        return (
            receipt.evidence_fingerprint == fingerprint
            and receipt.evidence_reference == command.evidence_reference
            and receipt.admitted_by == command.admitted_by
        )

    def operation(session: Session) -> Mapping[str, object]:
        claim = _claim(
            session, tenant_id=tenant_id, claim_id=command.claim_id, lock=True
        )
        existing = existing_receipt(session)
        if existing is not None:
            if not replay_matches(existing):
                raise Conflict("claim has different permanent historical evidence")
            return {"receipt_id": str(existing.id)}
        if _exists(
            session,
            HistoricalClaimReceipt,
            tenant_id,
            HistoricalClaimReceipt.source_owner == command.source_owner,
            HistoricalClaimReceipt.source_claim_id == command.source_claim_id,
        ):
            raise Conflict("source claim already admitted to another claim")
        if (
            claim.claimant_party_id != evidence.claimant_party_id
            or claim.currency_code != evidence.currency_code
        ):
            raise Conflict("historical destination claimant or currency differs")
        if _liability_for_claim(session, tenant_id, claim.id) is not None:
            raise refuse(
                Conflict,
                "the claim already has a liability",
                RefusalCode.CLAIM_ALREADY_ACCEPTED,
            )
        if session.get(Party, evidence.claimant_party_id) is None:
            raise refuse(
                InvalidCommand,
                "the claimant is not a party",
                RefusalCode.CLAIM_WITHOUT_EMPLOYEE,
            )
        liability = ClaimLiability(
            tenant_id=tenant_id,
            claim_id=claim.id,
            claimant_party_id=claim.claimant_party_id,
            currency_code=claim.currency_code,
            minor_units=2,
            liability_account_ref=evidence.liability_account_ref.strip(),
            origin=LiabilityOrigin.HISTORICAL.value,
            input_vat_mode=None,
            acceptance_reference=None,
            accepted_by=command.admitted_by,
            accepted_at=admitted_at,
            amount=evidence.residual,
            amount_settled=Decimal(0),
            amount_adjusted=Decimal(0),
            status=ClaimLiabilityStatus.OPEN.value,
        )
        session.add(liability)
        session.flush()
        receipt = HistoricalClaimReceipt(
            tenant_id=tenant_id,
            claim_id=claim.id,
            liability_id=liability.id,
            source_owner=command.source_owner,
            source_claim_id=command.source_claim_id,
            source_snapshot_sha256=command.source_snapshot_sha256,
            evidence_fingerprint=fingerprint,
            facts_fingerprint=evidence.facts_fingerprint,
            accounting_evidence_fingerprint=evidence.accounting_evidence_fingerprint,
            source_journal_ref=evidence.source_journal_ref.strip(),
            carried_evidence_ref=evidence.carried_evidence_ref.strip(),
            carried_account_ref=evidence.carried_account_ref.strip(),
            evidence_reference=command.evidence_reference,
            admitted_by=command.admitted_by,
            net_payable_amount=evidence.net_payable_amount,
            settled_before_admission=evidence.settled_before_admission,
            residual=evidence.residual,
            carried_control_amount=evidence.carried_control_amount,
            evidence=_evidence_json(evidence),
            original_posted_at=evidence.original_posted_at,
            admitted_at=admitted_at,
        )
        session.add(receipt)
        session.flush()
        return {"receipt_id": str(receipt.id)}

    # Check permanent evidence even when the short-lived kernel key still exists.
    prior = existing_receipt(db)
    if prior is not None and not replay_matches(prior):
        raise Conflict("claim has different permanent historical evidence")
    result = _once(
        db,
        tenant_id=tenant_id,
        scope_name="expenses.admit_historical_claim_liability",
        key=idempotency_key,
        expires_at=idempotency_expires_at,
        fingerprint=fingerprint_of(identity),
        operation=operation,
    )
    row = db.scalar(
        select(HistoricalClaimReceipt).where(
            HistoricalClaimReceipt.tenant_id == tenant_id,
            HistoricalClaimReceipt.id == UUID(str(result["receipt_id"])),
        )
    )
    if row is None:
        raise NotFound("historical claim receipt not found")
    return row


__all__ = [
    "accept_claim_for_reimbursement",
    "adjust_claim_liability",
    "admit_historical_claim_liability",
    "claim_liability_outstanding",
    "claim_liability_row_totals",
    "claimant_claims_balance",
    "claims_payable_total",
    "derive_claim_liability_status",
    "effective_claim_liability_amount",
    "open_claim_liabilities",
    "record_claim_accounting_receipt",
    "record_claim_settlement",
    "reverse_claim_acceptance",
    "reverse_claim_settlement",
]
