"""The tenant accounting decision engine in the caller's transaction.

Every query carries tenant scope even though PostgreSQL RLS enforces it again.
Services add/mutate/flush only: they never construct a session, commit or roll
back. Kernel idempotency owns replay; immutable ledger/period evidence is the
domain result, not a competing replay mechanism.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import execute_once, fingerprint_of
from dotmac_kernel.models import Tenant
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_accounting.contracts import (
    AccountKind,
    Conflict,
    CreateAccount,
    CreateAccountCategory,
    CreateDimension,
    CreateDimensionValue,
    CreateFiscalPeriod,
    CreateFiscalYear,
    CreateJournal,
    InvalidAccount,
    InvalidDimension,
    InvalidJournal,
    InvalidPeriod,
    InvalidTransition,
    JournalKind,
    JournalLineInput,
    JournalStatus,
    NotFound,
    PeriodCloseEvidence,
    PeriodStatus,
    ReverseJournal,
)
from dotmac_accounting.models import (
    Account,
    AccountCategory,
    AccountingDimension,
    AccountingDimensionValue,
    FiscalPeriod,
    FiscalYear,
    JournalEntry,
    JournalLine,
    JournalLineDimension,
    PeriodEvent,
    PostedLedgerDimension,
    PostedLedgerLine,
)

MONEY_QUANTUM = Decimal("0.000001")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _required(value: str, label: str, maximum: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    if len(normalized) > maximum:
        raise ValueError(f"{label} must be at most {maximum} characters")
    return normalized


def _code(value: str, label: str, maximum: int) -> str:
    return _required(value, label, maximum).upper()


def _currency(value: str | None) -> str | None:
    if value is None:
        return None
    result = _code(value, "currency code", 3)
    if len(result) != 3:
        raise InvalidAccount("currency code must contain exactly three characters")
    return result


def _tenant_lock(db: Session, tenant_id: UUID) -> None:
    if (
        db.scalar(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())
        is None
    ):
        raise NotFound("tenant was not found")


def _flush_unique(db: Session, row: object, *, label: str) -> None:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(f"{label} already exists") from exc


def _category(db: Session, tenant_id: UUID, category_id: UUID) -> AccountCategory:
    row = db.scalar(
        select(AccountCategory).where(
            AccountCategory.tenant_id == tenant_id, AccountCategory.id == category_id
        )
    )
    if row is None:
        raise NotFound("account category was not found")
    return row


def _account(
    db: Session, tenant_id: UUID, account_id: UUID, *, lock: bool = False
) -> Account:
    stmt = select(Account).where(
        Account.tenant_id == tenant_id, Account.id == account_id
    )
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("account was not found")
    return row


def _year(
    db: Session, tenant_id: UUID, year_id: UUID, *, lock: bool = False
) -> FiscalYear:
    stmt = select(FiscalYear).where(
        FiscalYear.tenant_id == tenant_id, FiscalYear.id == year_id
    )
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("fiscal year was not found")
    return row


def _period(
    db: Session, tenant_id: UUID, period_id: UUID, *, lock: bool = False
) -> FiscalPeriod:
    stmt = select(FiscalPeriod).where(
        FiscalPeriod.tenant_id == tenant_id, FiscalPeriod.id == period_id
    )
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("fiscal period was not found")
    return row


def _period_for_date(
    db: Session, tenant_id: UUID, posting_date: date, *, lock: bool = False
) -> FiscalPeriod:
    stmt = select(FiscalPeriod).where(
        FiscalPeriod.tenant_id == tenant_id,
        FiscalPeriod.start_date <= posting_date,
        FiscalPeriod.end_date >= posting_date,
    )
    if lock:
        stmt = stmt.with_for_update()
    rows = list(db.scalars(stmt))
    if not rows:
        raise InvalidPeriod("no fiscal period contains the posting date")
    if len(rows) != 1:
        raise InvalidPeriod("more than one fiscal period contains the posting date")
    return rows[0]


def _journal(
    db: Session, tenant_id: UUID, journal_id: UUID, *, lock: bool = False
) -> JournalEntry:
    stmt = select(JournalEntry).where(
        JournalEntry.tenant_id == tenant_id, JournalEntry.id == journal_id
    )
    if lock:
        stmt = stmt.with_for_update()
    row = db.scalar(stmt)
    if row is None:
        raise NotFound("journal was not found")
    return row


def create_account_category(
    db: Session, *, scope: TenantScope, command: CreateAccountCategory
) -> AccountCategory:
    tenant_id = scope.tenant_id
    code = _code(command.code, "category code", 30)
    name = _required(command.name, "category name", 160)
    if command.parent_id is not None:
        parent = _category(db, tenant_id, command.parent_id)
        if parent.account_class != command.account_class:
            raise InvalidAccount(
                "a child category must share its parent's account class"
            )
    row = AccountCategory(
        tenant_id=tenant_id,
        code=code,
        name=name,
        account_class=command.account_class,
        description=command.description,
        parent_id=command.parent_id,
    )
    _flush_unique(db, row, label="account category code")
    return row


def create_account(
    db: Session, *, scope: TenantScope, command: CreateAccount
) -> Account:
    tenant_id = scope.tenant_id
    category = _category(db, tenant_id, command.category_id)
    if not category.is_active:
        raise InvalidAccount("account category is inactive")
    if command.parent_id is not None:
        parent = _account(db, tenant_id, command.parent_id)
        if parent.kind == AccountKind.POSTING:
            raise InvalidAccount("a posting account cannot be a parent account")
    posting_allowed = command.posting_allowed
    if command.kind != AccountKind.POSTING and posting_allowed:
        raise InvalidAccount("only a posting account can allow journal posting")
    row = Account(
        tenant_id=tenant_id,
        category_id=category.id,
        parent_id=command.parent_id,
        code=_code(command.code, "account code", 40),
        name=_required(command.name, "account name", 200),
        description=command.description,
        kind=command.kind,
        normal_balance=command.normal_balance,
        currency_code=_currency(command.currency_code),
        posting_allowed=posting_allowed,
    )
    _flush_unique(db, row, label="account code")
    return row


def create_fiscal_year(
    db: Session, *, scope: TenantScope, command: CreateFiscalYear
) -> FiscalYear:
    if command.start_date > command.end_date:
        raise InvalidPeriod("fiscal year start date must not follow its end date")
    tenant_id = scope.tenant_id
    _tenant_lock(db, tenant_id)
    overlap = db.scalar(
        select(FiscalYear.id)
        .where(
            FiscalYear.tenant_id == tenant_id,
            FiscalYear.start_date <= command.end_date,
            FiscalYear.end_date >= command.start_date,
        )
        .limit(1)
    )
    if overlap is not None:
        raise InvalidPeriod("fiscal years cannot overlap")
    row = FiscalYear(
        tenant_id=tenant_id,
        code=_code(command.code, "fiscal year code", 20),
        name=_required(command.name, "fiscal year name", 80),
        start_date=command.start_date,
        end_date=command.end_date,
    )
    _flush_unique(db, row, label="fiscal year")
    return row


def create_fiscal_period(
    db: Session, *, scope: TenantScope, command: CreateFiscalPeriod
) -> FiscalPeriod:
    if command.period_number <= 0:
        raise InvalidPeriod("period number must be positive")
    if command.start_date > command.end_date:
        raise InvalidPeriod("period start date must not follow its end date")
    tenant_id = scope.tenant_id
    year = _year(db, tenant_id, command.fiscal_year_id, lock=True)
    if command.start_date < year.start_date or command.end_date > year.end_date:
        raise InvalidPeriod("a fiscal period must be contained by its fiscal year")
    overlap = db.scalar(
        select(FiscalPeriod.id)
        .where(
            FiscalPeriod.tenant_id == tenant_id,
            FiscalPeriod.fiscal_year_id == year.id,
            FiscalPeriod.start_date <= command.end_date,
            FiscalPeriod.end_date >= command.start_date,
        )
        .limit(1)
    )
    if overlap is not None:
        raise InvalidPeriod("fiscal periods cannot overlap")
    row = FiscalPeriod(
        tenant_id=tenant_id,
        fiscal_year_id=year.id,
        period_number=command.period_number,
        name=_required(command.name, "period name", 80),
        start_date=command.start_date,
        end_date=command.end_date,
        is_adjustment=command.is_adjustment,
        status=PeriodStatus.FUTURE,
    )
    _flush_unique(db, row, label="fiscal period")
    return row


def create_dimension(
    db: Session, *, scope: TenantScope, command: CreateDimension
) -> AccountingDimension:
    row = AccountingDimension(
        tenant_id=scope.tenant_id,
        code=_code(command.code, "dimension code", 40),
        name=_required(command.name, "dimension name", 160),
        description=command.description,
    )
    _flush_unique(db, row, label="accounting dimension code")
    return row


def create_dimension_value(
    db: Session, *, scope: TenantScope, command: CreateDimensionValue
) -> AccountingDimensionValue:
    tenant_id = scope.tenant_id
    dimension = db.scalar(
        select(AccountingDimension).where(
            AccountingDimension.tenant_id == tenant_id,
            AccountingDimension.id == command.dimension_id,
        )
    )
    if dimension is None:
        raise NotFound("accounting dimension was not found")
    if not dimension.is_active:
        raise InvalidDimension("accounting dimension is inactive")
    if command.parent_id is not None:
        parent = db.scalar(
            select(AccountingDimensionValue).where(
                AccountingDimensionValue.tenant_id == tenant_id,
                AccountingDimensionValue.id == command.parent_id,
            )
        )
        if parent is None or parent.dimension_id != dimension.id:
            raise InvalidDimension(
                "dimension value parent belongs to another dimension"
            )
    row = AccountingDimensionValue(
        tenant_id=tenant_id,
        dimension_id=dimension.id,
        parent_id=command.parent_id,
        code=_code(command.code, "dimension value code", 80),
        name=_required(command.name, "dimension value name", 200),
    )
    _flush_unique(db, row, label="dimension value code")
    return row


def _event(
    db: Session,
    *,
    tenant_id: UUID,
    period: FiscalPeriod,
    kind: str,
    before: PeriodStatus,
    after: PeriodStatus,
    actor_ref: str,
    approval_reference: str | None,
    evidence: dict[str, object],
    occurred_at: datetime,
) -> None:
    db.add(
        PeriodEvent(
            tenant_id=tenant_id,
            period_id=period.id,
            event_kind=kind,
            from_status=before.value,
            to_status=after.value,
            actor_ref=_required(actor_ref, "actor reference", 255),
            approval_reference=approval_reference,
            evidence=evidence,
            occurred_at=occurred_at,
        )
    )
    db.flush()


def open_period(
    db: Session,
    *,
    scope: TenantScope,
    period_id: UUID,
    actor_ref: str,
    occurred_at: datetime,
) -> FiscalPeriod:
    period = _period(db, scope.tenant_id, period_id, lock=True)
    if period.status != PeriodStatus.FUTURE:
        raise InvalidPeriod("only a future period can be opened")
    before = period.status
    period.status = PeriodStatus.OPEN
    _event(
        db,
        tenant_id=scope.tenant_id,
        period=period,
        kind="open",
        before=before,
        after=period.status,
        actor_ref=actor_ref,
        approval_reference=None,
        evidence={},
        occurred_at=occurred_at,
    )
    return period


def soft_close_period(
    db: Session,
    *,
    scope: TenantScope,
    period_id: UUID,
    evidence: PeriodCloseEvidence,
    actor_ref: str,
    approval_reference: str,
    occurred_at: datetime,
) -> FiscalPeriod:
    period = _period(db, scope.tenant_id, period_id, lock=True)
    if period.status not in {PeriodStatus.OPEN, PeriodStatus.REOPENED}:
        raise InvalidPeriod("only an open or reopened period can be soft-closed")
    approval = _required(approval_reference, "approval reference", 255)
    payload = evidence.payload()
    before = period.status
    period.status = PeriodStatus.SOFT_CLOSED
    period.reopen_token = None
    _event(
        db,
        tenant_id=scope.tenant_id,
        period=period,
        kind="soft_close",
        before=before,
        after=period.status,
        actor_ref=actor_ref,
        approval_reference=approval,
        evidence=payload,
        occurred_at=occurred_at,
    )
    return period


def reopen_period(
    db: Session,
    *,
    scope: TenantScope,
    period_id: UUID,
    actor_ref: str,
    approval_reference: str,
    reason: str,
    occurred_at: datetime,
) -> FiscalPeriod:
    period = _period(db, scope.tenant_id, period_id, lock=True)
    if period.status == PeriodStatus.LOCKED:
        raise InvalidPeriod("a locked period can never be reopened")
    if period.status != PeriodStatus.SOFT_CLOSED:
        raise InvalidPeriod("only a soft-closed period can be reopened")
    approval = _required(approval_reference, "approval reference", 255)
    reason_value = _required(reason, "reopen reason", 2000)
    before = period.status
    period.status = PeriodStatus.REOPENED
    period.reopen_token = uuid.uuid4()
    _event(
        db,
        tenant_id=scope.tenant_id,
        period=period,
        kind="reopen",
        before=before,
        after=period.status,
        actor_ref=actor_ref,
        approval_reference=approval,
        evidence={"reason": reason_value, "reopen_token": str(period.reopen_token)},
        occurred_at=occurred_at,
    )
    return period


def lock_period(
    db: Session,
    *,
    scope: TenantScope,
    period_id: UUID,
    actor_ref: str,
    approval_reference: str,
    occurred_at: datetime,
) -> FiscalPeriod:
    period = _period(db, scope.tenant_id, period_id, lock=True)
    if period.status != PeriodStatus.SOFT_CLOSED:
        raise InvalidPeriod("only a soft-closed period can be locked")
    approval = _required(approval_reference, "approval reference", 255)
    before = period.status
    period.status = PeriodStatus.LOCKED
    _event(
        db,
        tenant_id=scope.tenant_id,
        period=period,
        kind="lock",
        before=before,
        after=period.status,
        actor_ref=actor_ref,
        approval_reference=approval,
        evidence={},
        occurred_at=occurred_at,
    )
    return period


def _validated_lines(
    db: Session,
    *,
    tenant_id: UUID,
    command: CreateJournal,
    currency_code: str,
) -> tuple[
    list[tuple[JournalLineInput, Account, list[AccountingDimensionValue]]],
    Decimal,
    Decimal,
    Decimal,
    Decimal,
]:
    if len(command.lines) < 2:
        raise InvalidJournal("a journal must contain at least two lines")
    if command.exchange_rate <= 0:
        raise InvalidJournal("exchange rate must be greater than zero")
    total_debit = Decimal("0")
    total_credit = Decimal("0")
    total_debit_functional = Decimal("0")
    total_credit_functional = Decimal("0")
    resolved: list[
        tuple[JournalLineInput, Account, list[AccountingDimensionValue]]
    ] = []
    for line in command.lines:
        line.validate()
        account = _account(db, tenant_id, line.account_id)
        if (
            not account.is_active
            or not account.posting_allowed
            or account.kind != AccountKind.POSTING
        ):
            raise InvalidAccount(f"account {account.code} is not open for posting")
        if account.currency_code is not None and account.currency_code != currency_code:
            raise InvalidAccount(
                f"account {account.code} accepts only {account.currency_code} journals"
            )
        values: list[AccountingDimensionValue] = []
        dimension_ids: set[UUID] = set()
        for value_id in line.dimension_value_ids:
            value = db.scalar(
                select(AccountingDimensionValue).where(
                    AccountingDimensionValue.tenant_id == tenant_id,
                    AccountingDimensionValue.id == value_id,
                )
            )
            if value is None or not value.is_active:
                raise InvalidDimension("dimension value was not found or is inactive")
            dimension = db.scalar(
                select(AccountingDimension).where(
                    AccountingDimension.tenant_id == tenant_id,
                    AccountingDimension.id == value.dimension_id,
                )
            )
            if dimension is None or not dimension.is_active:
                raise InvalidDimension("accounting dimension is inactive")
            if value.dimension_id in dimension_ids:
                raise InvalidDimension(
                    "a journal line may select one value per dimension"
                )
            dimension_ids.add(value.dimension_id)
            values.append(value)
        debit, credit = _money(line.debit), _money(line.credit)
        debit_functional = _money(debit * command.exchange_rate)
        credit_functional = _money(credit * command.exchange_rate)
        total_debit += debit
        total_credit += credit
        total_debit_functional += debit_functional
        total_credit_functional += credit_functional
        resolved.append((line, account, values))
    if total_debit <= 0 or total_debit != total_credit:
        raise InvalidJournal(
            "journal debit and credit totals must balance and be positive"
        )
    if total_debit_functional != total_credit_functional:
        raise InvalidJournal("journal functional debit and credit totals must balance")
    return (
        resolved,
        _money(total_debit),
        _money(total_credit),
        _money(total_debit_functional),
        _money(total_credit_functional),
    )


def create_journal(
    db: Session,
    *,
    scope: TenantScope,
    command: CreateJournal,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    recorded_at: datetime,
) -> JournalEntry:
    tenant_id = scope.tenant_id
    payload = command.fingerprint_payload()
    request_fingerprint = fingerprint_of(payload)
    source = command.source.normalized()

    def operation(session: Session) -> Mapping[str, object]:
        existing = session.scalar(
            select(JournalEntry).where(
                JournalEntry.tenant_id == tenant_id,
                JournalEntry.source_owner == source.owner,
                JournalEntry.source_document_kind == source.document_kind,
                JournalEntry.source_document_id == source.document_id,
                JournalEntry.source_version == source.version,
            )
        )
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise Conflict(
                    "source identity already names a different journal request"
                )
            return {"journal_id": str(existing.id)}
        period = _period_for_date(session, tenant_id, command.posting_date)
        (
            resolved,
            total_debit,
            total_credit,
            functional_debit,
            functional_credit,
        ) = _validated_lines(
            session,
            tenant_id=tenant_id,
            command=command,
            currency_code=str(payload["currency_code"]),
        )
        journal = JournalEntry(
            tenant_id=tenant_id,
            fiscal_period_id=period.id,
            number=str(payload["number"]),
            kind=command.kind,
            status=JournalStatus.DRAFT,
            entry_date=command.entry_date,
            posting_date=command.posting_date,
            description=str(payload["description"]),
            reference=str(payload["reference"])
            if payload["reference"] is not None
            else None,
            currency_code=str(payload["currency_code"]),
            exchange_rate=command.exchange_rate,
            total_debit=total_debit,
            total_credit=total_credit,
            total_debit_functional=functional_debit,
            total_credit_functional=functional_credit,
            source_owner=source.owner,
            source_document_kind=source.document_kind,
            source_document_id=source.document_id,
            source_version=source.version,
            source_fingerprint=source.fingerprint,
            request_fingerprint=request_fingerprint,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        session.add(journal)
        session.flush()
        for number, (line, _account_row, values) in enumerate(resolved, start=1):
            row = JournalLine(
                tenant_id=tenant_id,
                journal_id=journal.id,
                line_number=number,
                account_id=line.account_id,
                description=line.description,
                debit=_money(line.debit),
                credit=_money(line.credit),
                debit_functional=_money(line.debit * command.exchange_rate),
                credit_functional=_money(line.credit * command.exchange_rate),
                created_at=recorded_at,
                updated_at=recorded_at,
            )
            session.add(row)
            session.flush()
            for value in values:
                session.add(
                    JournalLineDimension(
                        tenant_id=tenant_id,
                        journal_line_id=row.id,
                        dimension_id=value.dimension_id,
                        dimension_value_id=value.id,
                        created_at=recorded_at,
                    )
                )
        session.flush()
        return {"journal_id": str(journal.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="accounting.create_journal",
        key=idempotency_key,
        operation=operation,
        fingerprint=request_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _journal(db, tenant_id, UUID(str(outcome.result["journal_id"])))


def _require_postable_period(
    period: FiscalPeriod, *, kind: JournalKind, reopen_token: UUID | None
) -> None:
    if period.status == PeriodStatus.REOPENED:
        if reopen_token is None or reopen_token != period.reopen_token:
            raise InvalidPeriod(
                "posting to a reopened period requires its current reopen token"
            )
    elif period.status != PeriodStatus.OPEN:
        raise InvalidPeriod(
            f"period is {period.status.value.lower()} and rejects posting"
        )
    if period.is_adjustment and kind not in {
        JournalKind.ADJUSTMENT,
        JournalKind.CLOSING,
    }:
        raise InvalidPeriod(
            "an adjustment period accepts adjustment or closing journals only"
        )


def _journal_lines(db: Session, tenant_id: UUID, journal_id: UUID) -> list[JournalLine]:
    return list(
        db.scalars(
            select(JournalLine)
            .where(
                JournalLine.tenant_id == tenant_id, JournalLine.journal_id == journal_id
            )
            .order_by(JournalLine.line_number)
        )
    )


def _require_active_posting_references(
    db: Session, *, tenant_id: UUID, journal: JournalEntry, lines: list[JournalLine]
) -> None:
    for line in lines:
        account = _account(db, tenant_id, line.account_id, lock=True)
        if (
            not account.is_active
            or not account.posting_allowed
            or account.kind != AccountKind.POSTING
        ):
            raise InvalidAccount(f"account {account.code} is not open for posting")
        if (
            account.currency_code is not None
            and account.currency_code != journal.currency_code
        ):
            raise InvalidAccount(
                f"account {account.code} accepts only {account.currency_code} journals"
            )
        assignments = list(
            db.scalars(
                select(JournalLineDimension).where(
                    JournalLineDimension.tenant_id == tenant_id,
                    JournalLineDimension.journal_line_id == line.id,
                )
            )
        )
        for assignment in assignments:
            dimension = db.scalar(
                select(AccountingDimension)
                .where(
                    AccountingDimension.tenant_id == tenant_id,
                    AccountingDimension.id == assignment.dimension_id,
                )
                .with_for_update()
            )
            value = db.scalar(
                select(AccountingDimensionValue)
                .where(
                    AccountingDimensionValue.tenant_id == tenant_id,
                    AccountingDimensionValue.id == assignment.dimension_value_id,
                )
                .with_for_update()
            )
            if (
                dimension is None
                or value is None
                or not dimension.is_active
                or not value.is_active
            ):
                raise InvalidDimension(
                    "journal dimension assignment is inactive or incomplete"
                )


def _append_ledger(
    db: Session,
    *,
    tenant_id: UUID,
    journal: JournalEntry,
    posted_by: str,
    posted_at: datetime,
) -> None:
    for line in _journal_lines(db, tenant_id, journal.id):
        account = _account(db, tenant_id, line.account_id)
        ledger = PostedLedgerLine(
            tenant_id=tenant_id,
            journal_id=journal.id,
            journal_line_id=line.id,
            fiscal_period_id=journal.fiscal_period_id,
            account_id=account.id,
            account_code=account.code,
            journal_number=journal.number,
            entry_date=journal.entry_date,
            posting_date=journal.posting_date,
            description=line.description,
            currency_code=journal.currency_code,
            debit=line.debit_functional,
            credit=line.credit_functional,
            original_debit=line.debit,
            original_credit=line.credit,
            exchange_rate=journal.exchange_rate,
            source_owner=journal.source_owner,
            source_document_kind=journal.source_document_kind,
            source_document_id=journal.source_document_id,
            source_version=journal.source_version,
            source_fingerprint=journal.source_fingerprint,
            posted_by=posted_by,
            posted_at=posted_at,
        )
        db.add(ledger)
        db.flush()
        assignments = list(
            db.scalars(
                select(JournalLineDimension).where(
                    JournalLineDimension.tenant_id == tenant_id,
                    JournalLineDimension.journal_line_id == line.id,
                )
            )
        )
        for assignment in assignments:
            dimension = db.scalar(
                select(AccountingDimension).where(
                    AccountingDimension.tenant_id == tenant_id,
                    AccountingDimension.id == assignment.dimension_id,
                )
            )
            value = db.scalar(
                select(AccountingDimensionValue).where(
                    AccountingDimensionValue.tenant_id == tenant_id,
                    AccountingDimensionValue.id == assignment.dimension_value_id,
                )
            )
            if dimension is None or value is None:
                raise InvalidDimension("journal dimension assignment is incomplete")
            db.add(
                PostedLedgerDimension(
                    tenant_id=tenant_id,
                    ledger_line_id=ledger.id,
                    dimension_id=dimension.id,
                    dimension_code=dimension.code,
                    dimension_value_id=value.id,
                    value_code=value.code,
                    recorded_at=posted_at,
                )
            )
    db.flush()


def post_journal(
    db: Session,
    *,
    scope: TenantScope,
    journal_id: UUID,
    approval_reference: str,
    posted_by: str,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    posted_at: datetime,
    reopen_token: UUID | None = None,
) -> JournalEntry:
    tenant_id = scope.tenant_id
    approval = _required(approval_reference, "approval reference", 255)
    actor = _required(posted_by, "posted by", 255)
    payload = {
        "journal_id": str(journal_id),
        "approval_reference": approval,
        "posted_by": actor,
        "posted_at": posted_at.isoformat(),
        "reopen_token": str(reopen_token) if reopen_token else None,
    }

    def operation(session: Session) -> Mapping[str, object]:
        journal = _journal(session, tenant_id, journal_id, lock=True)
        if journal.status != JournalStatus.DRAFT:
            raise InvalidTransition("only a draft journal can be posted")
        period = _period(session, tenant_id, journal.fiscal_period_id, lock=True)
        _require_postable_period(period, kind=journal.kind, reopen_token=reopen_token)
        if not period.start_date <= journal.posting_date <= period.end_date:
            raise InvalidPeriod("journal posting date is outside its fiscal period")
        lines = _journal_lines(session, tenant_id, journal.id)
        _require_active_posting_references(
            session, tenant_id=tenant_id, journal=journal, lines=lines
        )
        original_debit = _money(sum((line.debit for line in lines), Decimal("0")))
        original_credit = _money(sum((line.credit for line in lines), Decimal("0")))
        debit = _money(sum((line.debit_functional for line in lines), Decimal("0")))
        credit = _money(sum((line.credit_functional for line in lines), Decimal("0")))
        if (
            original_debit <= 0
            or original_debit != original_credit
            or original_debit != journal.total_debit
            or original_credit != journal.total_credit
            or debit <= 0
            or debit != credit
            or debit != journal.total_debit_functional
            or credit != journal.total_credit_functional
        ):
            raise InvalidJournal("persisted journal lines are not balanced")
        _append_ledger(
            session,
            tenant_id=tenant_id,
            journal=journal,
            posted_by=actor,
            posted_at=posted_at,
        )
        journal.status = JournalStatus.POSTED
        journal.approval_reference = approval
        journal.posted_by = actor
        journal.posted_at = posted_at
        journal.updated_at = posted_at
        session.flush()
        return {"journal_id": str(journal.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="accounting.post_journal",
        key=idempotency_key,
        operation=operation,
        fingerprint=fingerprint_of(payload),
        expires_at=idempotency_expires_at,
    )
    return _journal(db, tenant_id, UUID(str(outcome.result["journal_id"])))


def reverse_journal(
    db: Session,
    *,
    scope: TenantScope,
    command: ReverseJournal,
    idempotency_key: str,
    idempotency_expires_at: datetime | None,
    reversed_at: datetime,
) -> JournalEntry:
    tenant_id = scope.tenant_id
    payload = command.fingerprint_payload()
    request_fingerprint = fingerprint_of(payload)

    def operation(session: Session) -> Mapping[str, object]:
        original = _journal(session, tenant_id, command.journal_id, lock=True)
        if (
            original.status != JournalStatus.POSTED
            or original.reversal_journal_id is not None
        ):
            raise InvalidTransition("only an unreversed posted journal can be reversed")
        period = _period_for_date(session, tenant_id, command.posting_date, lock=True)
        _require_postable_period(
            period, kind=JournalKind.REVERSAL, reopen_token=command.reopen_token
        )
        reversal = JournalEntry(
            tenant_id=tenant_id,
            fiscal_period_id=period.id,
            number=str(payload["number"]),
            kind=JournalKind.REVERSAL,
            # Lines and dimension assignments are assembled while the journal is
            # draft. The database trigger closes that content only after the
            # evidence append succeeds in this same transaction.
            status=JournalStatus.DRAFT,
            entry_date=command.posting_date,
            posting_date=command.posting_date,
            description=f"Reversal of {original.number}: {payload['reason']}",
            reference=original.number,
            currency_code=original.currency_code,
            exchange_rate=original.exchange_rate,
            total_debit=original.total_credit,
            total_credit=original.total_debit,
            total_debit_functional=original.total_credit_functional,
            total_credit_functional=original.total_debit_functional,
            source_owner="accounting.reversal",
            source_document_kind="journal",
            source_document_id=str(original.id),
            source_version="1",
            source_fingerprint=request_fingerprint,
            request_fingerprint=request_fingerprint,
            approval_reference=str(payload["approval_reference"]),
            posted_by=str(payload["reversed_by"]),
            posted_at=reversed_at,
            reverses_journal_id=original.id,
            created_at=reversed_at,
            updated_at=reversed_at,
        )
        session.add(reversal)
        session.flush()
        for source_line in _journal_lines(session, tenant_id, original.id):
            line = JournalLine(
                tenant_id=tenant_id,
                journal_id=reversal.id,
                line_number=source_line.line_number,
                account_id=source_line.account_id,
                description=(
                    f"Reversal: {source_line.description or original.description}"
                ),
                debit=source_line.credit,
                credit=source_line.debit,
                debit_functional=source_line.credit_functional,
                credit_functional=source_line.debit_functional,
                created_at=reversed_at,
                updated_at=reversed_at,
            )
            session.add(line)
            session.flush()
            assignments = list(
                session.scalars(
                    select(JournalLineDimension).where(
                        JournalLineDimension.tenant_id == tenant_id,
                        JournalLineDimension.journal_line_id == source_line.id,
                    )
                )
            )
            for assignment in assignments:
                session.add(
                    JournalLineDimension(
                        tenant_id=tenant_id,
                        journal_line_id=line.id,
                        dimension_id=assignment.dimension_id,
                        dimension_value_id=assignment.dimension_value_id,
                        created_at=reversed_at,
                    )
                )
        session.flush()
        _append_ledger(
            session,
            tenant_id=tenant_id,
            journal=reversal,
            posted_by=str(payload["reversed_by"]),
            posted_at=reversed_at,
        )
        reversal.status = JournalStatus.POSTED
        # The original-row guard verifies that its linked reversal is already
        # posted. Flush that state first; both writes remain in this transaction.
        session.flush()
        original.status = JournalStatus.REVERSED
        original.reversal_journal_id = reversal.id
        original.updated_at = reversed_at
        session.flush()
        return {"journal_id": str(reversal.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="accounting.reverse_journal",
        key=idempotency_key,
        operation=operation,
        fingerprint=request_fingerprint,
        expires_at=idempotency_expires_at,
    )
    return _journal(db, tenant_id, UUID(str(outcome.result["journal_id"])))


__all__ = [
    "create_account",
    "create_account_category",
    "create_dimension",
    "create_dimension_value",
    "create_fiscal_period",
    "create_fiscal_year",
    "create_journal",
    "lock_period",
    "open_period",
    "post_journal",
    "reopen_period",
    "reverse_journal",
    "soft_close_period",
]
