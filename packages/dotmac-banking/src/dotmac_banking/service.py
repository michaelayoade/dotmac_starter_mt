"""Flush-only owner for banking masters, observations, matching and reconciliation."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from dotmac_kernel.money import Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_banking.contracts import (
    BankAccountInput,
    BankInstitutionInput,
    BankStatementInput,
    CashObservationInput,
    MatchPolicyInput,
    MatchSuggestion,
)
from dotmac_banking.models import (
    BankAccount,
    BankInstitution,
    BankStatement,
    BankStatementLine,
    CashAccountObservation,
    MatchAllocation,
    MatchDecision,
    MatchPolicy,
    Reconciliation,
)


class BankingNotFound(LookupError):
    """A tenant-local banking record does not exist."""


class BankingConflict(ValueError):
    """A banking identity or immutable observation conflicts with existing data."""


class MatchRuleViolation(ValueError):
    """A statement, match or reconciliation violates its configured policy."""


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise MatchRuleViolation(f"{label} must not be blank")
    return cleaned


def _institution(db: Session, tenant_id: UUID, institution_id: UUID) -> BankInstitution:
    row = db.scalar(
        select(BankInstitution).where(
            BankInstitution.tenant_id == tenant_id,
            BankInstitution.id == institution_id,
        )
    )
    if row is None:
        raise BankingNotFound("bank institution not found")
    return row


def _account(
    db: Session, tenant_id: UUID, account_id: UUID, *, lock: bool = False
) -> BankAccount:
    statement = select(BankAccount).where(
        BankAccount.tenant_id == tenant_id, BankAccount.id == account_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise BankingNotFound("bank account not found")
    return row


def _money_matches_account(
    account: BankAccount, currency_code: str, minor_units: int
) -> None:
    if account.currency_code != currency_code or account.minor_units != minor_units:
        raise MatchRuleViolation("amount does not match the bank account currency")


def _quantum(minor_units: int) -> Decimal:
    return Decimal(1).scaleb(-minor_units)


def create_bank_institution(
    db: Session, *, tenant_id: UUID, command: BankInstitutionInput
) -> BankInstitution:
    code = _clean(command.code, "institution code")
    existing = db.scalar(
        select(BankInstitution).where(
            BankInstitution.tenant_id == tenant_id, BankInstitution.code == code
        )
    )
    if existing is not None:
        raise BankingConflict(f"bank institution {code} already exists")
    country = command.country_code.strip().upper()
    if len(country) != 2 or not country.isalpha() or not country.isascii():
        raise MatchRuleViolation("country code must be a two-letter code")
    row = BankInstitution(
        tenant_id=tenant_id,
        code=code,
        name=_clean(command.name, "institution name"),
        country_code=country,
        clearing_code=(
            command.clearing_code.strip() if command.clearing_code else None
        ),
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def update_bank_institution(
    db: Session,
    *,
    tenant_id: UUID,
    institution_id: UUID,
    name: str,
    clearing_code: str | None,
) -> BankInstitution:
    row = _institution(db, tenant_id, institution_id)
    if row.status != "active":
        raise BankingConflict("retired bank institution cannot be changed")
    row.name = _clean(name, "institution name")
    row.clearing_code = clearing_code.strip() if clearing_code else None
    db.flush()
    return row


def retire_bank_institution(
    db: Session, *, tenant_id: UUID, institution_id: UUID
) -> BankInstitution:
    row = _institution(db, tenant_id, institution_id)
    active_accounts = db.scalar(
        select(func.count())
        .select_from(BankAccount)
        .where(
            BankAccount.tenant_id == tenant_id,
            BankAccount.institution_id == institution_id,
            BankAccount.status != "closed",
        )
    )
    if active_accounts:
        raise BankingConflict("bank institution still has non-closed accounts")
    row.status = "retired"
    db.flush()
    return row


def create_bank_account(
    db: Session, *, tenant_id: UUID, command: BankAccountInput
) -> BankAccount:
    institution = _institution(db, tenant_id, command.institution_id)
    if institution.status != "active":
        raise BankingConflict("bank institution is retired")
    code = _clean(command.account_code, "account code")
    identifier = _clean(command.account_identifier, "account identifier")
    existing = db.scalar(
        select(BankAccount).where(
            BankAccount.tenant_id == tenant_id,
            (
                (BankAccount.account_code == code)
                | (
                    (BankAccount.institution_id == command.institution_id)
                    & (BankAccount.account_identifier == identifier)
                )
            ),
        )
    )
    if existing is not None:
        raise BankingConflict("bank account code or identifier already exists")
    row = BankAccount(
        tenant_id=tenant_id,
        institution_id=command.institution_id,
        account_code=code,
        account_name=_clean(command.account_name, "account name"),
        account_identifier=identifier,
        account_type_code=_clean(command.account_type_code, "account type code"),
        currency_code=command.currency.code,
        minor_units=command.currency.minor_units,
        cash_account_ref=_clean(command.cash_account_ref, "cash account reference"),
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def update_bank_account(
    db: Session,
    *,
    tenant_id: UUID,
    account_id: UUID,
    account_name: str,
    cash_account_ref: str,
) -> BankAccount:
    row = _account(db, tenant_id, account_id, lock=True)
    if row.status == "closed":
        raise BankingConflict("closed bank account cannot be changed")
    row.account_name = _clean(account_name, "account name")
    row.cash_account_ref = _clean(cash_account_ref, "cash account reference")
    db.flush()
    return row


def close_bank_account(
    db: Session, *, tenant_id: UUID, account_id: UUID
) -> BankAccount:
    row = _account(db, tenant_id, account_id, lock=True)
    open_statements = db.scalar(
        select(func.count())
        .select_from(BankStatement)
        .where(
            BankStatement.tenant_id == tenant_id,
            BankStatement.account_id == account_id,
            BankStatement.status != "closed",
        )
    )
    if open_statements:
        raise BankingConflict("bank account still has open statements")
    row.status = "closed"
    db.flush()
    return row


def list_bank_accounts(db: Session, *, tenant_id: UUID) -> tuple[BankAccount, ...]:
    return tuple(
        db.scalars(
            select(BankAccount)
            .where(BankAccount.tenant_id == tenant_id)
            .order_by(BankAccount.account_code)
        ).all()
    )


def import_bank_statement(
    db: Session,
    *,
    tenant_id: UUID,
    command: BankStatementInput,
    imported_at: datetime,
    imported_by_id: UUID,
) -> BankStatement:
    account = _account(db, tenant_id, command.account_id, lock=True)
    if account.status != "active":
        raise BankingConflict("bank account is not active")
    if command.period_end < command.period_start:
        raise MatchRuleViolation("statement period end precedes start")
    for value in (command.opening_balance, command.closing_balance):
        if value is not None:
            _money_matches_account(
                account, value.currency.code, value.currency.minor_units
            )
    line_numbers: set[int] = set()
    total_credits = Decimal(0)
    total_debits = Decimal(0)
    rows: list[BankStatementLine] = []
    for item in command.lines:
        if item.line_number <= 0 or item.line_number in line_numbers:
            raise MatchRuleViolation(
                "statement line numbers must be positive and unique"
            )
        line_numbers.add(item.line_number)
        if not command.period_start <= item.transaction_date <= command.period_end:
            raise MatchRuleViolation("statement line date is outside its period")
        _money_matches_account(
            account, item.amount.currency.code, item.amount.currency.minor_units
        )
        if item.amount.amount <= 0:
            raise MatchRuleViolation("statement line amount must be positive")
        if item.direction.value == "credit":
            total_credits += item.amount.amount
        else:
            total_debits += item.amount.amount
        rows.append(
            BankStatementLine(
                tenant_id=tenant_id,
                line_number=item.line_number,
                transaction_date=item.transaction_date,
                value_date=item.value_date,
                direction=item.direction.value,
                amount=item.amount.amount,
                external_ref=_clean(
                    item.external_ref, "external transaction reference"
                ),
                description=_clean(item.description, "statement description"),
                reference=item.reference.strip() if item.reference else None,
                counterparty=item.counterparty.strip() if item.counterparty else None,
                bank_transaction_code=(
                    item.bank_transaction_code.strip()
                    if item.bank_transaction_code
                    else None
                ),
                is_matched=False,
            )
        )
    if command.opening_balance is not None and command.closing_balance is not None:
        calculated = (
            command.opening_balance.amount + total_credits - total_debits
        ).quantize(_quantum(account.minor_units), rounding=ROUND_HALF_UP)
        if calculated != command.closing_balance.amount:
            raise MatchRuleViolation(
                "bank statement does not balance to its closing balance"
            )
    existing = db.scalar(
        select(BankStatement).where(
            BankStatement.tenant_id == tenant_id,
            (
                (
                    (BankStatement.account_id == command.account_id)
                    & (BankStatement.statement_ref == command.statement_ref.strip())
                )
                | (
                    (BankStatement.source_ref == command.source_ref.strip())
                    & (BankStatement.source_version == command.source_version.strip())
                )
            ),
        )
    )
    if existing is not None:
        raise BankingConflict("bank statement identity already exists")
    statement = BankStatement(
        tenant_id=tenant_id,
        account_id=account.id,
        statement_ref=_clean(command.statement_ref, "statement reference"),
        period_start=command.period_start,
        period_end=command.period_end,
        opening_balance=(
            command.opening_balance.amount if command.opening_balance else None
        ),
        closing_balance=(
            command.closing_balance.amount if command.closing_balance else None
        ),
        total_credits=total_credits,
        total_debits=total_debits,
        currency_code=account.currency_code,
        minor_units=account.minor_units,
        status="imported",
        total_lines=len(rows),
        matched_lines=0,
        source_ref=_clean(command.source_ref, "source reference"),
        source_version=_clean(command.source_version, "source version"),
        evidence_ref=_clean(command.evidence_ref, "evidence reference"),
        imported_by_id=imported_by_id,
        imported_at=imported_at,
    )
    statement.lines.extend(rows)
    db.add(statement)
    db.flush()
    return statement


def record_cash_observation(
    db: Session,
    *,
    tenant_id: UUID,
    command: CashObservationInput,
    observed_at: datetime,
) -> CashAccountObservation:
    account = _account(db, tenant_id, command.account_id)
    _money_matches_account(
        account, command.amount.currency.code, command.amount.currency.minor_units
    )
    if command.amount.amount <= 0:
        raise MatchRuleViolation("cash observation amount must be positive")
    existing = db.scalar(
        select(CashAccountObservation).where(
            CashAccountObservation.tenant_id == tenant_id,
            CashAccountObservation.source_ref == command.source_ref.strip(),
            CashAccountObservation.source_version == command.source_version.strip(),
        )
    )
    if existing is not None:
        raise BankingConflict("cash observation source version already exists")
    row = CashAccountObservation(
        tenant_id=tenant_id,
        account_id=account.id,
        effective_on=command.effective_on,
        direction=command.direction.value,
        amount=command.amount.amount,
        currency_code=account.currency_code,
        minor_units=account.minor_units,
        source_ref=_clean(command.source_ref, "source reference"),
        source_version=_clean(command.source_version, "source version"),
        evidence_ref=_clean(command.evidence_ref, "evidence reference"),
        description=_clean(command.description, "observation description"),
        reference=command.reference.strip() if command.reference else None,
        counterparty_ref=(
            command.counterparty_ref.strip() if command.counterparty_ref else None
        ),
        observed_at=observed_at,
    )
    db.add(row)
    db.flush()
    return row


def create_match_policy(
    db: Session, *, tenant_id: UUID, command: MatchPolicyInput
) -> MatchPolicy:
    if command.amount_tolerance < 0 or command.date_window_days < 0:
        raise MatchRuleViolation("match tolerance and date window must be non-negative")
    if command.reference_match_mode not in {"none", "contains", "exact"}:
        raise MatchRuleViolation("unknown reference match mode")
    if command.amount_weight + command.date_weight + command.reference_weight != 100:
        raise MatchRuleViolation("match-policy weights must total 100")
    if min(command.amount_weight, command.date_weight, command.reference_weight) < 0:
        raise MatchRuleViolation("match-policy weights must be non-negative")
    if not 0 <= command.minimum_confidence <= 100:
        raise MatchRuleViolation("minimum confidence must be between 0 and 100")
    row = MatchPolicy(
        tenant_id=tenant_id,
        code=_clean(command.code, "match policy code"),
        name=_clean(command.name, "match policy name"),
        amount_tolerance=command.amount_tolerance,
        date_window_days=command.date_window_days,
        reference_match_mode=command.reference_match_mode,
        amount_weight=command.amount_weight,
        date_weight=command.date_weight,
        reference_weight=command.reference_weight,
        minimum_confidence=command.minimum_confidence,
        direction=command.direction.value if command.direction else None,
        is_active=True,
    )
    db.add(row)
    db.flush()
    return row


def _line_and_statement(
    db: Session, tenant_id: UUID, line_id: UUID, *, lock: bool = False
) -> tuple[BankStatementLine, BankStatement]:
    statement = select(BankStatementLine).where(
        BankStatementLine.tenant_id == tenant_id, BankStatementLine.id == line_id
    )
    if lock:
        statement = statement.with_for_update()
    line = db.scalar(statement)
    if line is None:
        raise BankingNotFound("bank statement line not found")
    header = db.scalar(
        select(BankStatement).where(
            BankStatement.tenant_id == tenant_id,
            BankStatement.id == line.statement_id,
        )
    )
    if header is None:
        raise BankingNotFound("bank statement not found")
    return line, header


def _reference_matches(mode: str, left: str | None, right: str | None) -> bool:
    if mode == "none":
        return True
    left_clean = (left or "").strip().casefold()
    right_clean = (right or "").strip().casefold()
    if not left_clean or not right_clean:
        return False
    if mode == "exact":
        return left_clean == right_clean
    return left_clean in right_clean or right_clean in left_clean


def suggest_matches(
    db: Session,
    *,
    tenant_id: UUID,
    statement_line_id: UUID,
    policy_id: UUID,
) -> tuple[MatchSuggestion, ...]:
    line, header = _line_and_statement(db, tenant_id, statement_line_id)
    policy = db.scalar(
        select(MatchPolicy).where(
            MatchPolicy.tenant_id == tenant_id,
            MatchPolicy.id == policy_id,
            MatchPolicy.is_active.is_(True),
        )
    )
    if policy is None:
        raise BankingNotFound("active match policy not found")
    if policy.direction is not None and policy.direction != line.direction:
        return ()
    candidates = list(
        db.scalars(
            select(CashAccountObservation).where(
                CashAccountObservation.tenant_id == tenant_id,
                CashAccountObservation.account_id == header.account_id,
                CashAccountObservation.direction == line.direction,
                CashAccountObservation.effective_on
                >= line.transaction_date - timedelta(days=policy.date_window_days),
                CashAccountObservation.effective_on
                <= line.transaction_date + timedelta(days=policy.date_window_days),
            )
        ).all()
    )
    reference_candidates = [
        item
        for item in candidates
        if _reference_matches(
            policy.reference_match_mode, line.reference, item.reference
        )
    ]
    group_total = sum((item.amount for item in reference_candidates), Decimal(0))
    group_matches = abs(group_total - line.amount) <= policy.amount_tolerance
    suggestions: list[MatchSuggestion] = []
    for item in reference_candidates:
        difference = abs(item.amount - line.amount)
        if group_matches:
            amount_score = policy.amount_weight
        elif difference <= policy.amount_tolerance:
            amount_score = policy.amount_weight
        else:
            amount_score = 0
        days = abs((item.effective_on - line.transaction_date).days)
        if policy.date_window_days == 0:
            date_score = policy.date_weight if days == 0 else 0
        else:
            date_score = round(
                policy.date_weight
                * (policy.date_window_days - days)
                / policy.date_window_days
            )
        reference_score = (
            policy.reference_weight
            if _reference_matches(
                policy.reference_match_mode, line.reference, item.reference
            )
            else 0
        )
        confidence = amount_score + date_score + reference_score
        if confidence >= policy.minimum_confidence:
            suggestions.append(
                MatchSuggestion(
                    observation_id=item.id,
                    confidence=confidence,
                    amount_score=amount_score,
                    date_score=date_score,
                    reference_score=reference_score,
                )
            )
    return tuple(
        sorted(
            suggestions, key=lambda item: (-item.confidence, str(item.observation_id))
        )
    )


def accept_match(
    db: Session,
    *,
    tenant_id: UUID,
    statement_line_id: UUID,
    allocations: tuple[tuple[UUID, Money], ...],
    decided_by_id: UUID,
    decided_at: datetime,
    policy_id: UUID | None = None,
) -> MatchDecision:
    line, header = _line_and_statement(db, tenant_id, statement_line_id, lock=True)
    if line.is_matched:
        raise BankingConflict("bank statement line is already matched")
    if not allocations:
        raise MatchRuleViolation("a match requires at least one allocation")
    if policy_id is not None:
        policy = db.scalar(
            select(MatchPolicy).where(
                MatchPolicy.tenant_id == tenant_id, MatchPolicy.id == policy_id
            )
        )
        if policy is None:
            raise BankingNotFound("match policy not found")
    total = Decimal(0)
    prepared: list[tuple[CashAccountObservation, Decimal]] = []
    seen: set[UUID] = set()
    for observation_id, amount_value in allocations:
        if observation_id in seen:
            raise MatchRuleViolation("an observation may appear only once in a match")
        seen.add(observation_id)
        if (
            amount_value.currency.code != header.currency_code
            or amount_value.currency.minor_units != header.minor_units
        ):
            raise MatchRuleViolation("allocation currency does not match statement")
        amount = amount_value.amount
        if amount <= 0:
            raise MatchRuleViolation("allocation amount must be positive")
        observation = db.scalar(
            select(CashAccountObservation)
            .where(
                CashAccountObservation.tenant_id == tenant_id,
                CashAccountObservation.id == observation_id,
            )
            .with_for_update()
        )
        if observation is None:
            raise BankingNotFound("cash observation not found")
        if (
            observation.account_id != header.account_id
            or observation.direction != line.direction
        ):
            raise MatchRuleViolation(
                "cash observation does not match account and direction"
            )
        already = db.scalar(
            select(func.coalesce(func.sum(MatchAllocation.amount), 0))
            .join(MatchDecision, MatchDecision.id == MatchAllocation.decision_id)
            .where(
                MatchAllocation.tenant_id == tenant_id,
                MatchAllocation.observation_id == observation.id,
                MatchDecision.status == "accepted",
            )
        )
        if Decimal(already or 0) + amount > observation.amount:
            raise MatchRuleViolation(
                "allocation exceeds the observation's remaining amount"
            )
        total += amount
        prepared.append((observation, amount))
    if total != line.amount:
        raise MatchRuleViolation(
            "match allocations must equal the statement line amount"
        )
    decision = MatchDecision(
        tenant_id=tenant_id,
        statement_line_id=line.id,
        policy_id=policy_id,
        status="accepted",
        decided_by_id=decided_by_id,
        decided_at=decided_at,
    )
    db.add(decision)
    db.flush()
    for observation, amount in prepared:
        db.add(
            MatchAllocation(
                tenant_id=tenant_id,
                decision_id=decision.id,
                observation_id=observation.id,
                amount=amount,
            )
        )
    line.is_matched = True
    line.matched_at = decided_at
    header.matched_lines += 1
    db.flush()
    return decision


def prepare_reconciliation(
    db: Session,
    *,
    tenant_id: UUID,
    statement_id: UUID,
    cash_opening_balance: Money,
    prepared_by_id: UUID,
    prepared_at: datetime,
) -> Reconciliation:
    statement = db.scalar(
        select(BankStatement)
        .where(BankStatement.tenant_id == tenant_id, BankStatement.id == statement_id)
        .with_for_update()
    )
    if statement is None:
        raise BankingNotFound("bank statement not found")
    if statement.closing_balance is None:
        raise MatchRuleViolation("reconciliation requires a statement closing balance")
    if (
        cash_opening_balance.currency.code != statement.currency_code
        or cash_opening_balance.currency.minor_units != statement.minor_units
    ):
        raise MatchRuleViolation(
            "cash opening balance currency does not match statement"
        )
    existing = db.scalar(
        select(Reconciliation).where(
            Reconciliation.tenant_id == tenant_id,
            Reconciliation.statement_id == statement_id,
        )
    )
    if existing is not None:
        raise BankingConflict("statement already has a reconciliation")
    observations = db.scalars(
        select(CashAccountObservation).where(
            CashAccountObservation.tenant_id == tenant_id,
            CashAccountObservation.account_id == statement.account_id,
            CashAccountObservation.effective_on >= statement.period_start,
            CashAccountObservation.effective_on <= statement.period_end,
        )
    ).all()
    cash_movement = sum(
        (
            item.amount if item.direction == "credit" else -item.amount
            for item in observations
        ),
        Decimal(0),
    )
    cash_closing = cash_opening_balance.amount + cash_movement
    difference = statement.closing_balance - cash_closing
    row = Reconciliation(
        tenant_id=tenant_id,
        statement_id=statement.id,
        status="prepared",
        cash_opening_balance=cash_opening_balance.amount,
        cash_closing_balance=cash_closing,
        statement_closing_balance=statement.closing_balance,
        difference=difference,
        total_lines=statement.total_lines,
        matched_lines=statement.matched_lines,
        snapshot_ref=(
            f"statement:{statement.id}:source:{statement.source_version}:"
            f"matched:{statement.matched_lines}"
        ),
        prepared_by_id=prepared_by_id,
        prepared_at=prepared_at,
    )
    db.add(row)
    db.flush()
    return row


def approve_reconciliation(
    db: Session,
    *,
    tenant_id: UUID,
    reconciliation_id: UUID,
    approved_by_id: UUID,
    approved_at: datetime,
) -> Reconciliation:
    row = db.scalar(
        select(Reconciliation)
        .where(
            Reconciliation.tenant_id == tenant_id,
            Reconciliation.id == reconciliation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise BankingNotFound("reconciliation not found")
    if row.status != "prepared":
        raise BankingConflict("only a prepared reconciliation can be approved")
    if row.prepared_by_id == approved_by_id:
        raise MatchRuleViolation("reconciliation preparer cannot approve")
    if row.difference != 0:
        raise MatchRuleViolation("reconciliation difference must be zero")
    if row.matched_lines != row.total_lines:
        raise MatchRuleViolation("all statement lines must be matched")
    row.status = "approved"
    row.approved_by_id = approved_by_id
    row.approved_at = approved_at
    statement = db.scalar(
        select(BankStatement).where(
            BankStatement.tenant_id == tenant_id,
            BankStatement.id == row.statement_id,
        )
    )
    if statement is None:
        raise BankingNotFound("bank statement not found")
    statement.status = "reconciled"
    db.flush()
    return row


__all__ = [
    "BankingConflict",
    "BankingNotFound",
    "MatchRuleViolation",
    "accept_match",
    "approve_reconciliation",
    "close_bank_account",
    "create_bank_account",
    "create_bank_institution",
    "create_match_policy",
    "import_bank_statement",
    "list_bank_accounts",
    "prepare_reconciliation",
    "record_cash_observation",
    "retire_bank_institution",
    "suggest_matches",
    "update_bank_account",
    "update_bank_institution",
]
