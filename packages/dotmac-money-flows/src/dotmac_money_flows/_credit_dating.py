"""Read-only dating of credit that reached an account (shared by F1 and F2).

The one place flows reads Billing's account statement. A payment is never dated
before the money that paid it reached the account, so each caller asks for the
newest statement line that added available credit in a window and takes the later
of that instant and its own base date. Flows reads no clock and writes nothing
here; one account, no lock.

Billing caps one statement; when the window overflows (``statement_too_large``)
it is split at its midpoint and the NEWER half is searched first, so the newest
credit wins and the older half is read only if the newer holds none. The reads
are deterministic and bounded by the windows that overflow. A window that cannot
be split (one microsecond) raises ``BalancePassFailed``.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from dotmac_billing import (
    BillingRuleViolation,
    GetAccountStatementV1,
    get_account_statement,
)
from dotmac_kernel.cache import TenantScope
from sqlalchemy.orm import Session

from dotmac_money_flows.contracts import BalancePassFailed

#: Far bound of the statement window (Billing accepts any aware, non-empty
#: half-open window).
FAR_FUTURE = datetime(9999, 12, 31, tzinfo=UTC)
_STATEMENT_TOO_LARGE = "billing.statement_too_large"


def newest_credit_at(
    db: Session,
    scope: TenantScope,
    account_id: UUID,
    currency: Any,
    start: datetime,
    end: datetime = FAR_FUTURE,
    *,
    kinds: Collection[str] | None = None,
) -> datetime | None:
    """Instant of the newest line that adds credit in ``[start, end)``, or None.

    ``kinds`` limits the statement lines to those group kinds (F1 passes
    ``{"settlement_accepted"}``); None accepts a line of any group kind.
    """
    try:
        statement = get_account_statement(
            db,
            scope=scope,
            request=GetAccountStatementV1(scope, account_id, currency, start, end),
        )
    except BillingRuleViolation as exc:
        if exc.code != _STATEMENT_TOO_LARGE:
            raise
        middle = start + (end - start) / 2
        if not start < middle < end:
            raise BalancePassFailed("statement_unsplittable") from exc
        newer = newest_credit_at(
            db, scope, account_id, currency, middle, end, kinds=kinds
        )
        if newer is not None:
            return newer
        return newest_credit_at(
            db, scope, account_id, currency, start, middle, kinds=kinds
        )
    found = [
        line.occurred_at
        for line in statement.lines
        if (kinds is None or line.group_kind in kinds)
        and line.delta.available_credit.is_positive
    ]
    return max(found) if found else None
