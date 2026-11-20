"""Tenant-wide receivable control position: one pure read for a ledger tie.

``get_receivable_control_position_v1`` sums the latest persisted V2 receivable
position of every billing account in scope, per currency, in exact minor units.
It exists so a consuming ledger can compare its control account with Billing in
ONE snapshot. The caller owns that snapshot: it opens a ``READ ONLY`` +
``REPEATABLE READ`` (or ``SERIALIZABLE``) transaction, reads its own ledger and
this function inside it, and commits or rolls back itself. Billing never begins,
commits or changes the isolation of the caller's transaction (an internal
snapshot would need a second connection and could compare two different
instants). No locks are taken and nothing is flushed.

Per-row re-verification is deliberately not done here: the rows are append-only
(``refuse_mutation``) and envelope-checked at insert; the per-account verified
reads remain ``get_current_receivable_position`` and ``get_receivable_position_v2``.
Anything that is not provably complete is refused, never summed: a tie over a
partial history would be a false tie.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, DecimalException, localcontext
from typing import Any, Final, Literal, NoReturn
from uuid import UUID

from dotmac_kernel.cache import PlatformScope, Scope, TenantScope
from dotmac_kernel.fingerprints import fingerprint_of
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from dotmac_billing._opening_values import cash_decimal_context
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.opening_queries import _scoped, _table

RECEIVABLE_CONTROL_POSITION_CONTRACT: Final = "billing.receivable.control_position.v1"
_OWNER: Final = "dotmac-billing"
_ISOLATION: Final = frozenset({"REPEATABLE READ", "SERIALIZABLE"})


@dataclass(frozen=True, slots=True)
class ReceivableControlCurrencyV1:
    currency: str
    precision: int
    accounts: int
    collectible_receivable_minor: int
    available_credit_minor: int
    available_non_cash_credit_minor: int
    prepaid_funding_minor: int
    position_fingerprint: str


@dataclass(frozen=True, slots=True)
class ReceivableControlPositionV1:
    scope: Scope
    contract: Literal["billing.receivable.control_position.v1"]
    #: Database ``transaction_timestamp()``: the transaction start. The
    #: REPEATABLE READ snapshot is taken at its first statement, at or after
    #: this instant. Never the Python clock.
    snapshot_at: datetime
    currencies: tuple[ReceivableControlCurrencyV1, ...]


def _refuse(code: str, message: str) -> NoReturn:
    raise BillingRuleViolation(code, message)


def _scope_json(scope: Scope) -> dict[str, str]:
    return (
        {"kind": "tenant", "tenant_id": str(scope.tenant_id)}
        if isinstance(scope, TenantScope)
        else {"kind": "platform"}
    )


def control_position_fingerprint(
    *,
    scope: Scope,
    currency: str,
    precision: int,
    rows: Iterable[tuple[UUID, int, str]],
) -> str:
    """The per-currency fingerprint ERP can recompute.

    ``fingerprint_of`` over (sorted keys, compact separators, ASCII-escaped
    UTF-8, sha256 hex)::

        {"contract": "billing.receivable.control_position.v1",
         "scope": {"kind": "tenant", "tenant_id": "<uuid>"} | {"kind": "platform"},
         "currency": "NGN", "precision": 2,
         "rows": [["<account uuid>", "NGN", <source_version>,
                   "<state_fingerprint>"], ...]}

    ``rows`` are sorted by lowercase account uuid text. Scope, contract and
    precision are included for domain separation.
    """
    return fingerprint_of(
        {
            "contract": RECEIVABLE_CONTROL_POSITION_CONTRACT,
            "scope": _scope_json(scope),
            "currency": currency,
            "precision": precision,
            "rows": [
                [str(account), currency, version, state]
                for account, version, state in sorted(rows, key=lambda row: str(row[0]))
            ],
        }
    )


def _minor(amount: Any, precision: int) -> int:
    with localcontext(cash_decimal_context()):
        try:
            scaled = Decimal(amount).scaleb(precision)
            if scaled != scaled.to_integral_value():
                raise ValueError("amount is not an exact minor-unit integer")
            return int(scaled)
        except (ValueError, TypeError, DecimalException):
            _refuse(
                "control_position_precision_conflict",
                "a stored amount is not exact at its stored precision",
            )


def assemble_control_position(
    *,
    scope: Scope,
    snapshot_at: datetime,
    accounts: Mapping[UUID, tuple[str, int]],
    positions: Mapping[UUID, Mapping[str, Any]],
    latest_groups: Mapping[UUID, tuple[UUID, int]],
    group_counts: Mapping[UUID, int],
) -> ReceivableControlPositionV1:
    """Pure assembly and completeness check over already-read rows."""
    incomplete = 0
    for account_id in set(latest_groups) | set(positions):
        group = latest_groups.get(account_id)
        position = positions.get(account_id)
        if group is None:
            incomplete += 1  # a position row with no posting history
            continue
        if account_id not in accounts or position is None:
            incomplete += 1  # history with no V2 row (pre-bi_0005 V1 only)
            continue
        group_id, version = group
        currency, precision = accounts[account_id]
        if (
            group_counts.get(account_id) != version
            or position["source_version"] != version
            or position["posting_group_watermark"] != group_id
            or (position["currency"], position["minor_units"]) != (currency, precision)
            or position["completeness"] != "complete"
            or position["projection_mode"] != "authoritative"
            or position["source_authority"] != "internal"
        ):
            incomplete += 1
    if incomplete:
        _refuse(
            "control_position_incomplete",
            f"{incomplete} account(s) lack a complete authoritative V2 position",
        )
    precisions: dict[str, set[int]] = defaultdict(set)
    for position in positions.values():
        precisions[position["currency"]].add(position["minor_units"])
    if any(len(values) != 1 for values in precisions.values()):
        _refuse(
            "control_position_precision_conflict",
            "one currency is stored at more than one precision",
        )
    totals: dict[str, list[int]] = {}
    rows: dict[str, list[tuple[UUID, int, str]]] = defaultdict(list)
    for account_id, position in positions.items():
        code, precision = position["currency"], position["minor_units"]
        bucket = totals.setdefault(code, [0, 0, 0, 0, 0])
        bucket[0] += 1
        for index, name in enumerate(
            (
                "collectible_receivable",
                "available_credit",
                "available_non_cash_credit",
                "prepaid_funding",
            ),
            start=1,
        ):
            bucket[index] += _minor(position[name], precision)
        rows[code].append(
            (account_id, position["source_version"], position["state_fingerprint"])
        )
    currencies = tuple(
        ReceivableControlCurrencyV1(
            code,
            next(iter(precisions[code])),
            bucket[0],
            bucket[1],
            bucket[2],
            bucket[3],
            bucket[4],
            control_position_fingerprint(
                scope=scope,
                currency=code,
                precision=next(iter(precisions[code])),
                rows=rows[code],
            ),
        )
        for code, bucket in sorted(totals.items())
    )
    return ReceivableControlPositionV1(
        scope, RECEIVABLE_CONTROL_POSITION_CONTRACT, snapshot_at, currencies
    )


def get_receivable_control_position_v1(
    db: Session, *, scope: Scope
) -> ReceivableControlPositionV1:
    """Per-currency sums of the latest V2 position of every account in scope.

    Caller contract: a ``READ ONLY`` + ``REPEATABLE READ`` (or ``SERIALIZABLE``)
    transaction that the caller owns. Otherwise this refuses with
    ``control_position_requires_repeatable_read_read_only``. On the tenant plane
    the session's ``app.current_tenant`` must equal the scope's tenant, because
    forced row-level security would otherwise hide every row and return zeros
    (``control_position_tenant_context``). A currency appears only if it has at
    least one position row; a consumer treats a missing currency as zero.
    ``open_documents`` is intentionally absent from v1.
    """
    if not isinstance(scope, TenantScope | PlatformScope) or (
        isinstance(scope, TenantScope) and type(scope.tenant_id) is not UUID
    ):
        raise BillingRuleViolation(
            "scope_mismatch", "control position requires an explicit scope"
        )
    with db.no_autoflush:
        if (
            db.connection().get_isolation_level() not in _ISOLATION
            or db.execute(text("SHOW transaction_read_only")).scalar_one() != "on"
        ):
            _refuse(
                "control_position_requires_repeatable_read_read_only",
                "control position requires a READ ONLY REPEATABLE READ transaction",
            )
        if isinstance(scope, TenantScope):
            current = db.execute(
                text("SELECT current_setting('app.current_tenant', true)")
            ).scalar_one()
            if current != str(scope.tenant_id):
                _refuse(
                    "control_position_tenant_context",
                    "the session tenant context does not equal the requested scope",
                )
        snapshot_at = db.execute(text("SELECT transaction_timestamp()")).scalar_one()

        account_table = _table(scope, "billing_accounts")
        position_table = _table(scope, "receivable_position_facts_v2")
        group_table = _table(scope, "posting_groups")

        accounts = {
            row.id: (row.currency, row.minor_units)
            for row in db.execute(
                _scoped(
                    select(
                        account_table.c.id,
                        account_table.c.currency,
                        account_table.c.minor_units,
                    ),
                    scope,
                    account_table,
                )
            )
        }
        if db.execute(
            _scoped(
                select(position_table.c.id)
                .where(position_table.c.source_owner != _OWNER)
                .limit(1),
                scope,
                position_table,
            )
        ).first():
            _refuse(
                "control_position_source_owner",
                "a position row has a source owner other than dotmac-billing",
            )
        columns = (
            "billing_account_id",
            "source_version",
            "posting_group_watermark",
            "currency",
            "minor_units",
            "collectible_receivable",
            "available_credit",
            "available_non_cash_credit",
            "prepaid_funding",
            "state_fingerprint",
            "source_authority",
            "projection_mode",
            "completeness",
        )
        # DISTINCT ON follows the unique index (owner, account, currency, version)
        # so the planner can scan it backwards; an account has one currency.
        position_rows = [
            dict(row)
            for row in db.execute(
                _scoped(
                    select(*(position_table.c[name] for name in columns))
                    .where(position_table.c.source_owner == _OWNER)
                    .distinct(
                        position_table.c.billing_account_id,
                        position_table.c.currency,
                    )
                    .order_by(
                        position_table.c.billing_account_id.desc(),
                        position_table.c.currency.desc(),
                        position_table.c.source_version.desc(),
                    ),
                    scope,
                    position_table,
                )
            )
            .mappings()
            .all()
        ]
        positions: dict[UUID, Mapping[str, Any]] = {
            row["billing_account_id"]: row for row in position_rows
        }
        if len(positions) != len(position_rows):
            _refuse(
                "control_position_incomplete",
                "an account has position rows in more than one currency",
            )
        latest_groups = {
            row.billing_account_id: (row.id, row.source_version)
            for row in db.execute(
                _scoped(
                    select(
                        group_table.c.billing_account_id,
                        group_table.c.id,
                        group_table.c.source_version,
                    )
                    .distinct(group_table.c.billing_account_id)
                    .order_by(
                        group_table.c.billing_account_id.desc(),
                        group_table.c.source_version.desc(),
                    ),
                    scope,
                    group_table,
                )
            )
        }
        group_counts = {
            row.billing_account_id: row.total
            for row in db.execute(
                _scoped(
                    select(
                        group_table.c.billing_account_id,
                        func.count().label("total"),
                    ).group_by(group_table.c.billing_account_id),
                    scope,
                    group_table,
                )
            )
        }
    return assemble_control_position(
        scope=scope,
        snapshot_at=snapshot_at,
        accounts=accounts,
        positions=positions,
        latest_groups=latest_groups,
        group_counts=group_counts,
    )


__all__ = [
    "RECEIVABLE_CONTROL_POSITION_CONTRACT",
    "ReceivableControlCurrencyV1",
    "ReceivableControlPositionV1",
    "assemble_control_position",
    "control_position_fingerprint",
    "get_receivable_control_position_v1",
]
