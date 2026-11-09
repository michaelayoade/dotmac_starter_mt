"""Read-only account statement projection over the ordered ledger (Billing a12).

A statement is a pure projection of one account's immutable posting groups and
effects in one currency, in the ledger's own total order, with running balances
for the three separate lanes and a sealed digest. It takes no lock, performs no
write, repairs nothing and publishes no event; every figure is recomputed from
persisted evidence on each call.

Order and window. Lines are the account's posting groups whose business time
``occurred_at`` lies in the half-open window, ordered by
``(occurred_at, source_version)``; ``source_version`` is the ledger's gap-free
per-account sequence, so the order is total and independent of commit timing.
The opening balance is every earlier group in that same order (all groups with
``occurred_at`` before the window start). The statement is anchored to a ledger
watermark, the highest ``source_version`` visible when it was read; rows posted
later (or concurrently) are excluded, so one read is internally consistent. A
backdated group posted after the statement was taken changes the next
statement, not this one; the watermark and digest make that visible.

The digest is a SHA-256 over a domain tag, the identifying header, the opening
balances, every line (identity, kind, source, instant, deltas and running
balances) and the closing balances. ``AccountStatementV1`` verifies it on
construction, so a statement that was edited after sealing cannot be built.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException
from typing import Literal
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.money import Currency, Money
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_queries import _scoped, _table

STATEMENT_CONTRACT = "billing.account.statement.v1"
STATEMENT_DIGEST_DOMAIN = "billing.account.statement.v1"
MAX_STATEMENT_LINES = 5000
_LANES = ("receivable", "available_credit", "prepaid_funding")


def _scope_text(scope: Scope) -> str:
    return f"tenant:{scope.tenant_id}" if isinstance(scope, TenantScope) else "platform"


@dataclass(frozen=True, slots=True)
class StatementBalancesV1:
    """The three lanes are never netted against one another."""

    receivable: Money
    available_credit: Money
    prepaid_funding: Money

    def __post_init__(self) -> None:
        currencies = {
            self.receivable.currency,
            self.available_credit.currency,
            self.prepaid_funding.currency,
        }
        if len(currencies) != 1:
            raise ValueError("statement lanes must share one currency")


@dataclass(frozen=True, slots=True)
class AccountStatementLineV1:
    sequence: int
    posting_group_id: UUID
    source_version: int
    group_kind: str
    source_ref: str
    occurred_at: datetime
    reverses_group_id: UUID | None
    delta: StatementBalancesV1
    balance: StatementBalancesV1


@dataclass(frozen=True, slots=True)
class GetAccountStatementV1:
    """Statement of one account and currency over ``[occurred_from, occurred_to)``."""

    scope: Scope
    billing_account_id: UUID
    currency: Currency
    occurred_from: datetime
    occurred_to: datetime


def _balances_line(label: str, balances: StatementBalancesV1) -> str:
    return (
        f"{label}|{balances.receivable.amount}|{balances.available_credit.amount}|"
        f"{balances.prepaid_funding.amount}\n"
    )


def compute_account_statement_digest(
    *,
    scope: Scope,
    billing_account_id: UUID,
    currency: Currency,
    occurred_from: datetime,
    occurred_to: datetime,
    watermark_version: int,
    opening: StatementBalancesV1,
    lines: tuple[AccountStatementLineV1, ...],
    closing: StatementBalancesV1,
) -> str:
    """The sealed digest; pure, so a consumer can recompute it independently."""

    digest = hashlib.sha256(f"{STATEMENT_DIGEST_DOMAIN}\n".encode())
    digest.update(
        (
            f"header|{_scope_text(scope)}|{billing_account_id}|{currency.code}|"
            f"{currency.minor_units}|{occurred_from.isoformat()}|"
            f"{occurred_to.isoformat()}|{watermark_version}\n"
        ).encode()
    )
    digest.update(_balances_line("opening", opening).encode())
    for line in lines:
        digest.update(
            (
                f"line|{line.sequence}|{line.posting_group_id}|{line.source_version}|"
                f"{line.group_kind}|{line.source_ref}|{line.occurred_at.isoformat()}|"
                f"{line.reverses_group_id or ''}\n"
            ).encode()
        )
        digest.update(_balances_line("delta", line.delta).encode())
        digest.update(_balances_line("balance", line.balance).encode())
    digest.update(_balances_line("closing", closing).encode())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class AccountStatementV1:
    """Immutable, sealed statement. Construction re-derives and checks everything."""

    scope: Scope
    billing_account_id: UUID
    currency: Currency
    occurred_from: datetime
    occurred_to: datetime
    watermark_version: int
    opening: StatementBalancesV1
    lines: tuple[AccountStatementLineV1, ...]
    closing: StatementBalancesV1
    digest: str
    contract_version: Literal["billing.account.statement.v1"] = (
        "billing.account.statement.v1"
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "lines", tuple(self.lines))
        running = self.opening
        for index, line in enumerate(self.lines, start=1):
            expected = StatementBalancesV1(
                *(
                    Money(
                        getattr(running, lane).amount
                        + getattr(line.delta, lane).amount,
                        self.currency,
                    )
                    for lane in _LANES
                )
            )
            if line.sequence != index or line.balance != expected:
                raise ValueError("statement lines do not carry the running balances")
            running = line.balance
        if running != self.closing:
            raise ValueError("statement closing differs from its lines")
        expected_digest = compute_account_statement_digest(
            scope=self.scope,
            billing_account_id=self.billing_account_id,
            currency=self.currency,
            occurred_from=self.occurred_from,
            occurred_to=self.occurred_to,
            watermark_version=self.watermark_version,
            opening=self.opening,
            lines=self.lines,
            closing=self.closing,
        )
        if expected_digest != self.digest:
            raise ValueError("statement digest does not seal its content")


def _money(value: object, currency: Currency) -> Money:
    try:
        amount = Decimal(str(value))
        scaled = amount.quantize(Decimal(1).scaleb(-currency.minor_units))
        if not amount.is_finite() or scaled != amount:
            raise ValueError("amount is not exact in the currency scale")
        return Money(scaled, currency)
    except (ValueError, DecimalException, ArithmeticError) as exc:
        raise BillingRuleViolation(
            "statement_integrity", "ledger amount is not exact for the currency"
        ) from exc


def _lane_totals(rows: object, currency: Currency) -> dict[str, Money]:
    totals = {lane: Decimal(0) for lane in _LANES}
    for lane, amount in rows:  # type: ignore[attr-defined]
        if lane not in totals:
            raise BillingRuleViolation(
                "statement_integrity", "ledger effect has an unknown lane"
            )
        totals[lane] += Decimal(amount)
    return {lane: _money(value, currency) for lane, value in totals.items()}


def _validate(scope: Scope, request: GetAccountStatementV1) -> None:
    if not isinstance(scope, TenantScope | PlatformScope) or (
        isinstance(scope, TenantScope) and not isinstance(scope.tenant_id, UUID)
    ):
        raise BillingRuleViolation(
            "scope_mismatch", "account statements require explicit scope"
        )
    if not isinstance(request, GetAccountStatementV1) or request.scope != scope:
        raise BillingRuleViolation(
            "scope_mismatch", "the request scope must equal the routed scope"
        )
    if not isinstance(request.billing_account_id, UUID) or not isinstance(
        request.currency, Currency
    ):
        raise BillingRuleViolation(
            "invalid_statement_request", "account and currency are required"
        )
    start, end = request.occurred_from, request.occurred_to
    if (
        not isinstance(start, datetime)
        or not isinstance(end, datetime)
        or start.tzinfo is None
        or end.tzinfo is None
        or start >= end
    ):
        raise BillingRuleViolation(
            "invalid_window", "an aware, non-empty half-open window is required"
        )


def get_account_statement(
    db: Session, *, scope: Scope, request: GetAccountStatementV1
) -> AccountStatementV1:
    """Project one sealed statement. Read-only, lock-free, never repairs."""

    _validate(scope, request)
    currency = request.currency
    groups = _table(scope, "posting_groups")
    effects = _table(scope, "posting_effects")
    accounts = _table(scope, "billing_accounts")
    join = effects.c.posting_group_id == groups.c.id
    if isinstance(scope, TenantScope):
        join = join & (effects.c.tenant_id == groups.c.tenant_id)
    with db.no_autoflush:
        account = (
            db.execute(
                _scoped(
                    select(accounts.c.id).where(
                        accounts.c.id == request.billing_account_id
                    ),
                    scope,
                    accounts,
                )
            )
            .mappings()
            .one_or_none()
        )
        if account is None:
            raise BillingRuleViolation("not_found", "billing account was not found")
        read_through_version = int(
            db.execute(
                _scoped(
                    select(func.coalesce(func.max(groups.c.source_version), 0)).where(
                        groups.c.billing_account_id == request.billing_account_id,
                        groups.c.currency == currency.code,
                    ),
                    scope,
                    groups,
                )
            ).scalar_one()
        )
        base = (
            groups.c.billing_account_id == request.billing_account_id,
            groups.c.currency == currency.code,
            groups.c.minor_units == currency.minor_units,
            groups.c.source_version <= read_through_version,
        )
        opening_rows = db.execute(
            _scoped(
                select(effects.c.lane, func.sum(effects.c.amount_delta))
                .select_from(effects.join(groups, join))
                .where(*base, groups.c.occurred_at < request.occurred_from)
                .group_by(effects.c.lane),
                scope,
                groups,
            )
        ).all()
        opening = StatementBalancesV1(**_lane_totals(opening_rows, currency))
        group_rows = (
            db.execute(
                _scoped(
                    select(
                        groups.c.id,
                        groups.c.source_version,
                        groups.c.group_kind,
                        groups.c.source_ref,
                        groups.c.occurred_at,
                        groups.c.reverses_group_id,
                    )
                    .where(
                        *base,
                        groups.c.occurred_at >= request.occurred_from,
                        groups.c.occurred_at < request.occurred_to,
                    )
                    .order_by(groups.c.occurred_at, groups.c.source_version)
                    .limit(MAX_STATEMENT_LINES + 1),
                    scope,
                    groups,
                )
            )
            .mappings()
            .all()
        )
        if len(group_rows) > MAX_STATEMENT_LINES:
            raise BillingRuleViolation(
                "statement_too_large",
                "narrow the statement window; it exceeds the supported size",
            )
        by_group: dict[UUID, list[tuple[str, Decimal]]] = {}
        ids = [row["id"] for row in group_rows]
        if ids:
            for group_id, lane, amount in db.execute(
                _scoped(
                    select(
                        effects.c.posting_group_id,
                        effects.c.lane,
                        func.sum(effects.c.amount_delta),
                    )
                    .select_from(effects.join(groups, join))
                    .where(*base, effects.c.posting_group_id.in_(ids))
                    .group_by(effects.c.posting_group_id, effects.c.lane),
                    scope,
                    groups,
                )
            ):
                by_group.setdefault(group_id, []).append((lane, amount))
    running = opening
    lines: list[AccountStatementLineV1] = []
    for sequence, row in enumerate(group_rows, start=1):
        delta = StatementBalancesV1(
            **_lane_totals(by_group.get(row["id"], []), currency)
        )
        running = StatementBalancesV1(
            *(
                Money(
                    getattr(running, lane).amount + getattr(delta, lane).amount,
                    currency,
                )
                for lane in _LANES
            )
        )
        lines.append(
            AccountStatementLineV1(
                sequence,
                row["id"],
                row["source_version"],
                row["group_kind"],
                row["source_ref"],
                row["occurred_at"],
                row["reverses_group_id"],
                delta,
                running,
            )
        )
    digest = compute_account_statement_digest(
        scope=scope,
        billing_account_id=request.billing_account_id,
        currency=currency,
        occurred_from=request.occurred_from,
        occurred_to=request.occurred_to,
        watermark_version=read_through_version,
        opening=opening,
        lines=tuple(lines),
        closing=running,
    )
    return AccountStatementV1(
        scope,
        request.billing_account_id,
        currency,
        request.occurred_from,
        request.occurred_to,
        read_through_version,
        opening,
        tuple(lines),
        running,
        digest,
    )


__all__ = [
    "AccountStatementLineV1",
    "AccountStatementV1",
    "GetAccountStatementV1",
    "MAX_STATEMENT_LINES",
    "STATEMENT_CONTRACT",
    "StatementBalancesV1",
    "compute_account_statement_digest",
    "get_account_statement",
]
