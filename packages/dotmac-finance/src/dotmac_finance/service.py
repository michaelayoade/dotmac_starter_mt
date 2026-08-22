"""Flush-only owner for fixed-asset books and accounting consequences."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.money import Money
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_finance.calculation import (
    FinanceRuleViolation,
    calculate_depreciation,
    calculate_disposal,
    calculate_impairment,
    calculate_revaluation,
)
from dotmac_finance.contracts import (
    AccountingModel,
    BookStatus,
    CapitalizeAssetBook,
    DepreciationMethod,
    DisposalCommand,
    ImpairmentCommand,
    RevaluationCommand,
)
from dotmac_finance.models import (
    AccountingConsequence,
    AccountingConsequenceLine,
    AccountingEvent,
    AssetBook,
    DepreciationLine,
    DepreciationRun,
)


class FinanceNotFound(LookupError):
    """The requested tenant-local accounting record does not exist."""


class FinanceConflict(ValueError):
    """The request conflicts with the book's authoritative state."""


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{label} must not be blank")
    return cleaned


def _same_functional_currency(book: AssetBook, *money: Money | None) -> None:
    for value in money:
        if value is None:
            continue
        if (
            value.currency.code != book.currency_code
            or value.currency.minor_units != book.minor_units
        ):
            raise FinanceRuleViolation("amount does not match the book currency")


def _currency_code(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha() or not code.isascii():
        raise FinanceRuleViolation("functional currency must be a three-letter code")
    return code


def _book(db: Session, tenant_id: UUID, book_id: UUID) -> AssetBook:
    book = db.scalar(
        select(AssetBook)
        .where(AssetBook.tenant_id == tenant_id, AssetBook.id == book_id)
        .with_for_update()
    )
    if book is None:
        raise FinanceNotFound("asset book not found")
    return book


def _active_expected(book: AssetBook, expected_version: int) -> None:
    if book.status != BookStatus.ACTIVE.value:
        raise FinanceConflict("asset book is already derecognized")
    if book.version != expected_version:
        raise FinanceConflict(
            f"stale asset book: expected version {expected_version}, "
            f"found {book.version}"
        )


def _approved(requested_by_id: UUID, approved_by_id: UUID, approval_ref: str) -> str:
    if requested_by_id == approved_by_id:
        raise FinanceRuleViolation(
            "separation of duties violation: requester cannot approve"
        )
    return _clean(approval_ref, "approval reference")


def _event(
    db: Session,
    *,
    book: AssetBook,
    event_type: str,
    effective_on: date,
    source_ref: str,
    source_version: str,
    evidence_ref: str,
    approval_ref: str | None,
    actor_id: UUID | None,
    carrying_before: Decimal,
    carrying_after: Decimal,
    event_data: dict[str, object],
    occurred_at: datetime,
) -> AccountingEvent:
    row = AccountingEvent(
        tenant_id=book.tenant_id,
        book_id=book.id,
        sequence=book.version,
        event_type=event_type,
        effective_on=effective_on,
        source_ref=_clean(source_ref, "source reference"),
        source_version=_clean(source_version, "source version"),
        evidence_ref=_clean(evidence_ref, "evidence reference"),
        approval_ref=approval_ref,
        actor_id=actor_id,
        carrying_amount_before=carrying_before,
        carrying_amount_after=carrying_after,
        event_data=event_data,
        occurred_at=occurred_at,
    )
    db.add(row)
    db.flush()
    return row


Line = tuple[str, str, Decimal, str, str | None]


def _consequence(
    db: Session,
    *,
    tenant_id: UUID,
    source_type: str,
    source_id: UUID,
    effective_on: date,
    currency_code: str,
    minor_units: int,
    description: str,
    evidence_ref: str,
    lines: Iterable[Line],
    created_at: datetime,
) -> AccountingConsequence:
    aggregated: dict[tuple[str, str, str, str | None], Decimal] = defaultdict(Decimal)
    for account, side, amount, purpose, cost_center in lines:
        if amount > 0:
            aggregated[(account, side, purpose, cost_center)] += amount
    debit = sum(
        (amount for (_, side, _, _), amount in aggregated.items() if side == "debit"),
        Decimal(0),
    )
    credit = sum(
        (amount for (_, side, _, _), amount in aggregated.items() if side == "credit"),
        Decimal(0),
    )
    if not aggregated or debit != credit:
        raise FinanceRuleViolation(
            f"accounting consequence is not balanced: debit={debit}, credit={credit}"
        )
    group = AccountingConsequence(
        tenant_id=tenant_id,
        source_type=source_type,
        source_id=source_id,
        effective_on=effective_on,
        currency_code=currency_code,
        minor_units=minor_units,
        description=description,
        evidence_ref=_clean(evidence_ref, "evidence reference"),
        created_at=created_at,
    )
    db.add(group)
    db.flush()
    ordered_lines = sorted(
        aggregated.items(),
        key=lambda item: (
            item[0][0],
            item[0][1],
            item[0][2],
            item[0][3] or "",
        ),
    )
    for number, ((account, side, purpose, cost_center), amount) in enumerate(
        ordered_lines, start=1
    ):
        db.add(
            AccountingConsequenceLine(
                tenant_id=tenant_id,
                consequence_id=group.id,
                line_number=number,
                account_ref=account,
                side=side,
                amount=amount,
                purpose=purpose,
                cost_center_ref=cost_center,
            )
        )
    db.flush()
    return group


def capitalize_asset_book(
    db: Session,
    *,
    tenant_id: UUID,
    command: CapitalizeAssetBook,
    recorded_at: datetime,
) -> AssetBook:
    book_code = _clean(command.book_code, "book code")
    source_ref = _clean(command.source_ref, "source reference")
    source_version = _clean(command.source_version, "source version")
    evidence_ref = _clean(command.evidence_ref, "evidence reference")
    if command.useful_life_months <= 0:
        raise FinanceRuleViolation("useful life must be positive")
    if command.functional_cost.currency != command.residual_value.currency:
        raise FinanceRuleViolation("residual value must use the functional currency")
    if command.functional_cost.amount <= 0:
        raise FinanceRuleViolation("functional cost must be positive")
    if not 0 <= command.residual_value.amount <= command.functional_cost.amount:
        raise FinanceRuleViolation("residual value must be between zero and cost")
    if (
        command.accounting_model is AccountingModel.REVALUATION
        and command.accounts.revaluation_reserve is None
    ):
        raise FinanceRuleViolation("revaluation model requires a reserve account")
    existing = db.scalar(
        select(AssetBook).where(
            AssetBook.tenant_id == tenant_id,
            AssetBook.asset_id == command.asset_id,
            AssetBook.book_code == book_code,
        )
    )
    if existing is not None:
        raise FinanceConflict(f"asset already has book {book_code}")
    currency = command.functional_cost.currency
    zero = Decimal(0).quantize(Decimal(1).scaleb(-currency.minor_units))
    book = AssetBook(
        tenant_id=tenant_id,
        asset_id=command.asset_id,
        book_code=book_code,
        status=BookStatus.ACTIVE.value,
        accounting_model=command.accounting_model.value,
        depreciation_method=command.method.value,
        currency_code=currency.code,
        minor_units=currency.minor_units,
        # Book balances use functional currency; the original transaction
        # amount and currency remain in the immutable capitalization event.
        acquisition_cost=command.functional_cost.amount,
        gross_carrying_amount=command.functional_cost.amount,
        accumulated_depreciation=zero,
        accumulated_impairment=zero,
        carrying_amount=command.functional_cost.amount,
        unimpaired_carrying_amount=command.functional_cost.amount,
        residual_value=command.residual_value.amount,
        useful_life_months=command.useful_life_months,
        depreciation_periods_taken=0,
        revaluation_reserve_balance=zero,
        prior_revaluation_loss_balance=zero,
        impairment_loss_balance=zero,
        impairment_reserve_reduction_balance=zero,
        available_for_use_on=command.available_for_use_on,
        asset_account_ref=command.accounts.asset,
        accumulated_depreciation_account_ref=(
            command.accounts.accumulated_depreciation
        ),
        accumulated_impairment_account_ref=command.accounts.accumulated_impairment,
        depreciation_expense_account_ref=command.accounts.depreciation_expense,
        impairment_loss_account_ref=command.accounts.impairment_loss,
        revaluation_reserve_account_ref=command.accounts.revaluation_reserve,
        disposal_gain_loss_account_ref=command.accounts.disposal_gain_loss,
        cost_center_ref=command.accounts.cost_center,
        source_ref=source_ref,
        source_version=source_version,
        evidence_ref=evidence_ref,
        version=1,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(book)
            db.flush()
    except IntegrityError as exc:
        raise FinanceConflict(f"asset already has book {book_code}") from exc
    _event(
        db,
        book=book,
        event_type="capitalized",
        effective_on=command.available_for_use_on,
        source_ref=source_ref,
        source_version=source_version,
        evidence_ref=evidence_ref,
        approval_ref=None,
        actor_id=command.actor_id,
        carrying_before=zero,
        carrying_after=book.carrying_amount,
        event_data={
            "asset_id": str(command.asset_id),
            "book_code": book_code,
            "functional_cost": str(command.functional_cost.amount),
            "acquisition_currency": command.acquisition_cost.currency.code,
            "acquisition_cost": str(command.acquisition_cost.amount),
        },
        occurred_at=recorded_at,
    )
    return book


def _elapsed_months(start: date, through: date) -> int:
    if start > through:
        return 0
    months = (through.year - start.year) * 12 + through.month - start.month
    if through.day >= start.day:
        months += 1
    return max(months, 0)


def calculate_depreciation_run(
    db: Session,
    *,
    tenant_id: UUID,
    run_ref: str,
    period_ref: str,
    through_date: date,
    created_by_id: UUID,
    calculated_at: datetime,
    currency_code: str | None = None,
    minor_units: int | None = None,
) -> DepreciationRun:
    run_reference = _clean(run_ref, "run reference")
    period_reference = _clean(period_ref, "period reference")
    existing = db.scalar(
        select(DepreciationRun).where(
            DepreciationRun.tenant_id == tenant_id,
            DepreciationRun.run_ref == run_reference,
        )
    )
    if existing is not None:
        raise FinanceConflict("depreciation run reference already exists")
    query = select(AssetBook).where(
        AssetBook.tenant_id == tenant_id,
        AssetBook.status == BookStatus.ACTIVE.value,
        AssetBook.available_for_use_on <= through_date,
    )
    selected_currency = _currency_code(currency_code) if currency_code else None
    if selected_currency is not None:
        query = query.where(AssetBook.currency_code == selected_currency)
    books = list(db.scalars(query.order_by(AssetBook.id)).all())
    currencies = {(book.currency_code, book.minor_units) for book in books}
    if len(currencies) > 1:
        raise FinanceRuleViolation(
            "depreciation run spans currencies; select one functional currency"
        )
    if not currencies and (selected_currency is None or minor_units is None):
        raise FinanceRuleViolation(
            "an empty depreciation run needs an explicit currency and minor units"
        )
    if minor_units is not None and not 0 <= minor_units <= 6:
        raise FinanceRuleViolation("minor units must be between zero and six")
    currency, resolved_minor_units = next(
        iter(currencies), (selected_currency or "", minor_units or 0)
    )
    if minor_units is not None and currencies and minor_units != resolved_minor_units:
        raise FinanceRuleViolation("minor units do not match the selected books")
    run = DepreciationRun(
        tenant_id=tenant_id,
        run_ref=run_reference,
        period_ref=period_reference,
        through_date=through_date,
        status="calculated",
        assets_processed=0,
        total_depreciation=Decimal(0),
        currency_code=currency,
        minor_units=resolved_minor_units,
        created_by_id=created_by_id,
        calculated_at=calculated_at,
    )
    db.add(run)
    db.flush()
    total = Decimal(0)
    processed = 0
    for book in books:
        elapsed = min(
            book.useful_life_months,
            _elapsed_months(book.available_for_use_on, through_date),
        )
        periods = max(0, elapsed - book.depreciation_periods_taken)
        if periods == 0:
            continue
        result = calculate_depreciation(
            carrying_amount=book.carrying_amount,
            residual_value=book.residual_value,
            unimpaired_carrying_amount=book.unimpaired_carrying_amount,
            remaining_life_months=(
                book.useful_life_months - book.depreciation_periods_taken
            ),
            useful_life_months=book.useful_life_months,
            periods=periods,
            method=DepreciationMethod(book.depreciation_method),
            minor_units=book.minor_units,
        )
        if result.charge <= 0:
            continue
        db.add(
            DepreciationLine(
                tenant_id=tenant_id,
                run_id=run.id,
                book_id=book.id,
                book_version=book.version,
                periods=periods,
                carrying_amount_opening=book.carrying_amount,
                depreciation_amount=result.charge,
                carrying_amount_closing=result.closing_carrying_amount,
                unimpaired_carrying_opening=book.unimpaired_carrying_amount,
                unimpaired_depreciation_amount=result.unimpaired_charge,
                unimpaired_carrying_closing=(result.closing_unimpaired_carrying_amount),
                remaining_life_opening=(
                    book.useful_life_months - book.depreciation_periods_taken
                ),
                remaining_life_closing=result.closing_remaining_life_months,
                expense_account_ref=book.depreciation_expense_account_ref,
                accumulated_depreciation_account_ref=(
                    book.accumulated_depreciation_account_ref
                ),
                cost_center_ref=book.cost_center_ref,
            )
        )
        total += result.charge
        processed += 1
    run.total_depreciation = total
    run.assets_processed = processed
    db.flush()
    return run


def post_depreciation_run(
    db: Session,
    *,
    tenant_id: UUID,
    run_id: UUID,
    posted_by_id: UUID,
    posted_at: datetime,
) -> DepreciationRun:
    run = db.scalar(
        select(DepreciationRun)
        .where(DepreciationRun.tenant_id == tenant_id, DepreciationRun.id == run_id)
        .with_for_update()
    )
    if run is None:
        raise FinanceNotFound("depreciation run not found")
    if run.status != "calculated":
        raise FinanceConflict("depreciation run is already posted")
    if run.created_by_id == posted_by_id:
        raise FinanceRuleViolation("depreciation run creator cannot post")
    lines = list(
        db.scalars(
            select(DepreciationLine)
            .where(
                DepreciationLine.tenant_id == tenant_id,
                DepreciationLine.run_id == run.id,
            )
            .order_by(DepreciationLine.book_id)
        ).all()
    )
    if not lines:
        raise FinanceRuleViolation("depreciation run has no accounting lines")
    posting_lines: list[Line] = []
    books: list[tuple[AssetBook, DepreciationLine, Decimal]] = []
    for line in lines:
        book = _book(db, tenant_id, line.book_id)
        _active_expected(book, line.book_version)
        if book.carrying_amount != line.carrying_amount_opening:
            raise FinanceConflict("depreciation run is stale for asset book")
        before = book.carrying_amount
        books.append((book, line, before))
        posting_lines.extend(
            (
                (
                    line.expense_account_ref,
                    "debit",
                    line.depreciation_amount,
                    "depreciation_expense",
                    line.cost_center_ref,
                ),
                (
                    line.accumulated_depreciation_account_ref,
                    "credit",
                    line.depreciation_amount,
                    "accumulated_depreciation",
                    line.cost_center_ref,
                ),
            )
        )
    consequence = _consequence(
        db,
        tenant_id=tenant_id,
        source_type="depreciation_run",
        source_id=run.id,
        effective_on=run.through_date,
        currency_code=run.currency_code,
        minor_units=run.minor_units,
        description=f"Depreciation {run.period_ref}",
        evidence_ref=f"depreciation-run:{run.run_ref}",
        lines=posting_lines,
        created_at=posted_at,
    )
    for book, line, before in books:
        book.accumulated_depreciation += line.depreciation_amount
        book.carrying_amount = line.carrying_amount_closing
        book.unimpaired_carrying_amount = line.unimpaired_carrying_closing
        book.depreciation_periods_taken += line.periods
        book.version += 1
        _event(
            db,
            book=book,
            event_type="depreciated",
            effective_on=run.through_date,
            source_ref=f"depreciation-run:{run.run_ref}:{book.id}",
            source_version=str(line.book_version),
            evidence_ref=f"accounting-consequence:{consequence.id}",
            approval_ref=None,
            actor_id=posted_by_id,
            carrying_before=before,
            carrying_after=book.carrying_amount,
            event_data={
                "run_id": str(run.id),
                "period_ref": run.period_ref,
                "periods": line.periods,
                "depreciation_amount": str(line.depreciation_amount),
            },
            occurred_at=posted_at,
        )
    run.status = "posted"
    run.posted_by_id = posted_by_id
    run.posted_at = posted_at
    db.flush()
    return run


def impair_asset_book(
    db: Session,
    *,
    tenant_id: UUID,
    command: ImpairmentCommand,
    recorded_at: datetime,
) -> AccountingConsequence:
    approval_ref = _approved(
        command.requested_by_id, command.approved_by_id, command.approval_ref
    )
    book = _book(db, tenant_id, command.book_id)
    _active_expected(book, command.expected_version)
    _same_functional_currency(
        book,
        command.fair_value_less_costs_of_disposal,
        command.value_in_use,
    )
    result = calculate_impairment(
        carrying_amount=book.carrying_amount,
        unimpaired_carrying_amount=book.unimpaired_carrying_amount,
        fair_value_less_costs_of_disposal=(
            command.fair_value_less_costs_of_disposal.amount
            if command.fair_value_less_costs_of_disposal
            else None
        ),
        value_in_use=command.value_in_use.amount if command.value_in_use else None,
        impairment_loss_balance=book.impairment_loss_balance,
        reserve_reduction_balance=book.impairment_reserve_reduction_balance,
        revaluation_reserve_balance=book.revaluation_reserve_balance,
        minor_units=book.minor_units,
    )
    before = book.carrying_amount
    loss = max(Decimal(0), before - result.closing_carrying_amount)
    reversal = max(Decimal(0), result.closing_carrying_amount - before)
    if loss == 0 and reversal == 0:
        raise FinanceRuleViolation("assessment produces no impairment consequence")
    book.carrying_amount = result.closing_carrying_amount
    book.accumulated_impairment += loss - reversal
    book.revaluation_reserve_balance = result.closing_revaluation_reserve_balance
    book.impairment_loss_balance = result.closing_impairment_loss_balance
    book.impairment_reserve_reduction_balance = result.closing_reserve_reduction_balance
    book.version += 1
    event_type = "impaired" if loss > 0 else "impairment_reversed"
    event = _event(
        db,
        book=book,
        event_type=event_type,
        effective_on=command.effective_on,
        source_ref=f"impairment:{command.evidence_ref}",
        source_version=str(command.expected_version),
        evidence_ref=command.evidence_ref,
        approval_ref=approval_ref,
        actor_id=command.approved_by_id,
        carrying_before=before,
        carrying_after=book.carrying_amount,
        event_data={
            "basis": _clean(command.basis, "recoverable amount basis"),
            "recoverable_amount": str(result.recoverable_amount),
            "loss_to_reserve": str(result.loss_to_reserve),
            "loss_to_profit_or_loss": str(result.loss_to_profit_or_loss),
            "reversal_to_profit_or_loss": str(result.reversal_to_profit_or_loss),
            "reversal_to_reserve": str(result.reversal_to_reserve),
        },
        occurred_at=recorded_at,
    )
    lines: list[Line] = []
    if loss > 0:
        if result.loss_to_reserve > 0:
            reserve_account = book.revaluation_reserve_account_ref
            if reserve_account is None:
                raise FinanceRuleViolation("reserve allocation has no reserve account")
            lines.append(
                (
                    reserve_account,
                    "debit",
                    result.loss_to_reserve,
                    "impairment_oci",
                    None,
                )
            )
        lines.append(
            (
                book.impairment_loss_account_ref,
                "debit",
                result.loss_to_profit_or_loss,
                "impairment_loss",
                book.cost_center_ref,
            )
        )
        lines.append(
            (
                book.accumulated_impairment_account_ref,
                "credit",
                loss,
                "accumulated_impairment",
                None,
            )
        )
    else:
        lines.append(
            (
                book.accumulated_impairment_account_ref,
                "debit",
                reversal,
                "impairment_reversal",
                None,
            )
        )
        lines.append(
            (
                book.impairment_loss_account_ref,
                "credit",
                result.reversal_to_profit_or_loss,
                "impairment_reversal_profit_or_loss",
                book.cost_center_ref,
            )
        )
        if result.reversal_to_reserve > 0:
            reserve_account = book.revaluation_reserve_account_ref
            if reserve_account is None:
                raise FinanceRuleViolation("reserve reversal has no reserve account")
            lines.append(
                (
                    reserve_account,
                    "credit",
                    result.reversal_to_reserve,
                    "impairment_reversal_oci",
                    None,
                )
            )
    consequence = _consequence(
        db,
        tenant_id=tenant_id,
        source_type="impairment",
        source_id=event.id,
        effective_on=command.effective_on,
        currency_code=book.currency_code,
        minor_units=book.minor_units,
        description=f"Asset impairment {book.book_code}",
        evidence_ref=command.evidence_ref,
        lines=lines,
        created_at=recorded_at,
    )
    db.flush()
    return consequence


def revalue_asset_book(
    db: Session,
    *,
    tenant_id: UUID,
    command: RevaluationCommand,
    recorded_at: datetime,
) -> AccountingConsequence:
    approval_ref = _approved(
        command.requested_by_id, command.approved_by_id, command.approval_ref
    )
    book = _book(db, tenant_id, command.book_id)
    _active_expected(book, command.expected_version)
    _same_functional_currency(book, command.fair_value)
    if book.accounting_model != AccountingModel.REVALUATION.value:
        raise FinanceRuleViolation("asset book does not use the revaluation model")
    if book.accumulated_impairment > 0:
        raise FinanceRuleViolation("reverse impairment first, then revalue")
    reserve_account = book.revaluation_reserve_account_ref
    if reserve_account is None:
        raise FinanceRuleViolation("revaluation model has no reserve account")
    result = calculate_revaluation(
        carrying_amount=book.carrying_amount,
        fair_value=command.fair_value.amount,
        revaluation_reserve_balance=book.revaluation_reserve_balance,
        prior_revaluation_loss_balance=book.prior_revaluation_loss_balance,
        minor_units=book.minor_units,
    )
    before = book.carrying_amount
    difference = result.closing_carrying_amount - before
    if difference == 0:
        raise FinanceRuleViolation("valuation produces no revaluation consequence")
    accumulated_depreciation = book.accumulated_depreciation
    book.gross_carrying_amount = result.closing_carrying_amount
    book.accumulated_depreciation = Decimal(0)
    book.carrying_amount = result.closing_carrying_amount
    book.unimpaired_carrying_amount = result.closing_carrying_amount
    book.revaluation_reserve_balance = result.closing_reserve_balance
    book.prior_revaluation_loss_balance = result.closing_prior_loss_balance
    book.version += 1
    event = _event(
        db,
        book=book,
        event_type="revalued",
        effective_on=command.effective_on,
        source_ref=f"revaluation:{command.evidence_ref}",
        source_version=str(command.expected_version),
        evidence_ref=command.evidence_ref,
        approval_ref=approval_ref,
        actor_id=command.approved_by_id,
        carrying_before=before,
        carrying_after=book.carrying_amount,
        event_data={
            "fair_value": str(command.fair_value.amount),
            "valuation_method": _clean(command.valuation_method, "valuation method"),
            "surplus_to_reserve": str(result.surplus_to_reserve),
            "loss_reversed_to_profit_or_loss": str(
                result.loss_reversed_to_profit_or_loss
            ),
            "reserve_reversed": str(result.reserve_reversed),
            "loss_to_profit_or_loss": str(result.loss_to_profit_or_loss),
        },
        occurred_at=recorded_at,
    )
    lines: list[Line] = []
    if accumulated_depreciation > 0:
        lines.extend(
            (
                (
                    book.accumulated_depreciation_account_ref,
                    "debit",
                    accumulated_depreciation,
                    "eliminate_accumulated_depreciation",
                    None,
                ),
                (
                    book.asset_account_ref,
                    "credit",
                    accumulated_depreciation,
                    "eliminate_gross_carrying_amount",
                    None,
                ),
            )
        )
    if difference > 0:
        lines.append(
            (book.asset_account_ref, "debit", difference, "revaluation_increase", None)
        )
        lines.extend(
            (
                (
                    book.impairment_loss_account_ref,
                    "credit",
                    result.loss_reversed_to_profit_or_loss,
                    "revaluation_loss_reversal",
                    book.cost_center_ref,
                ),
                (
                    reserve_account,
                    "credit",
                    result.surplus_to_reserve,
                    "revaluation_surplus",
                    None,
                ),
            )
        )
    else:
        decrease = -difference
        lines.append(
            (book.asset_account_ref, "credit", decrease, "revaluation_decrease", None)
        )
        lines.extend(
            (
                (
                    reserve_account,
                    "debit",
                    result.reserve_reversed,
                    "revaluation_reserve_reversal",
                    None,
                ),
                (
                    book.impairment_loss_account_ref,
                    "debit",
                    result.loss_to_profit_or_loss,
                    "revaluation_loss",
                    book.cost_center_ref,
                ),
            )
        )
    consequence = _consequence(
        db,
        tenant_id=tenant_id,
        source_type="revaluation",
        source_id=event.id,
        effective_on=command.effective_on,
        currency_code=book.currency_code,
        minor_units=book.minor_units,
        description=f"Asset revaluation {book.book_code}",
        evidence_ref=command.evidence_ref,
        lines=lines,
        created_at=recorded_at,
    )
    db.flush()
    return consequence


def dispose_asset_book(
    db: Session,
    *,
    tenant_id: UUID,
    command: DisposalCommand,
    recorded_at: datetime,
) -> AccountingConsequence:
    approval_ref = _approved(
        command.requested_by_id, command.approved_by_id, command.approval_ref
    )
    book = _book(db, tenant_id, command.book_id)
    _active_expected(book, command.expected_version)
    _same_functional_currency(book, command.proceeds, command.costs_of_disposal)
    clearing = _clean(command.clearing_account_ref, "disposal clearing account")
    result = calculate_disposal(
        carrying_amount=book.carrying_amount,
        proceeds=command.proceeds.amount,
        costs_of_disposal=command.costs_of_disposal.amount,
        minor_units=book.minor_units,
    )
    before = book.carrying_amount
    gross = book.gross_carrying_amount
    accumulated_depreciation = book.accumulated_depreciation
    accumulated_impairment = book.accumulated_impairment
    reconstructed_carrying = gross - accumulated_depreciation - accumulated_impairment
    if reconstructed_carrying != before:
        raise FinanceConflict(
            "asset book carrying amount no longer reconciles to its valuation balances"
        )
    book.status = BookStatus.DERECOGNIZED.value
    book.derecognized_on = command.effective_on
    book.gross_carrying_amount = Decimal(0)
    book.accumulated_depreciation = Decimal(0)
    book.accumulated_impairment = Decimal(0)
    book.carrying_amount = Decimal(0)
    book.unimpaired_carrying_amount = Decimal(0)
    book.residual_value = Decimal(0)
    book.version += 1
    event = _event(
        db,
        book=book,
        event_type="derecognized",
        effective_on=command.effective_on,
        source_ref=_clean(command.asset_disposal_ref, "asset disposal reference"),
        source_version=str(command.expected_version),
        evidence_ref=command.evidence_ref,
        approval_ref=approval_ref,
        actor_id=command.approved_by_id,
        carrying_before=before,
        carrying_after=book.carrying_amount,
        event_data={
            "proceeds": str(command.proceeds.amount),
            "costs_of_disposal": str(command.costs_of_disposal.amount),
            "net_proceeds": str(result.net_proceeds),
            "gain_or_loss": str(result.gain_or_loss),
        },
        occurred_at=recorded_at,
    )
    lines: list[Line] = [
        (
            book.accumulated_depreciation_account_ref,
            "debit",
            accumulated_depreciation,
            "clear_accumulated_depreciation",
            None,
        ),
        (
            book.accumulated_impairment_account_ref,
            "debit",
            accumulated_impairment,
            "clear_accumulated_impairment",
            None,
        ),
        (book.asset_account_ref, "credit", gross, "derecognize_asset", None),
    ]
    if result.net_proceeds > 0:
        lines.append(
            (clearing, "debit", result.net_proceeds, "net_disposal_proceeds", None)
        )
    elif result.net_proceeds < 0:
        lines.append(
            (clearing, "credit", -result.net_proceeds, "net_disposal_cost", None)
        )
    if result.gain_or_loss > 0:
        lines.append(
            (
                book.disposal_gain_loss_account_ref,
                "credit",
                result.gain_or_loss,
                "disposal_gain",
                book.cost_center_ref,
            )
        )
    elif result.gain_or_loss < 0:
        lines.append(
            (
                book.disposal_gain_loss_account_ref,
                "debit",
                -result.gain_or_loss,
                "disposal_loss",
                book.cost_center_ref,
            )
        )
    consequence = _consequence(
        db,
        tenant_id=tenant_id,
        source_type="disposal",
        source_id=event.id,
        effective_on=command.effective_on,
        currency_code=book.currency_code,
        minor_units=book.minor_units,
        description=f"Asset disposal {book.book_code}",
        evidence_ref=command.evidence_ref,
        lines=lines,
        created_at=recorded_at,
    )
    db.flush()
    return consequence


__all__ = [
    "FinanceConflict",
    "FinanceNotFound",
    "calculate_depreciation_run",
    "capitalize_asset_book",
    "dispose_asset_book",
    "impair_asset_book",
    "post_depreciation_run",
    "revalue_asset_book",
]
