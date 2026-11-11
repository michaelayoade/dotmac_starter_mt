"""Pure proofs for the sealed account statement (no database)."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from dotmac_billing import (
    AccountStatementLineV1,
    AccountStatementV1,
    StatementBalancesV1,
    compute_account_statement_digest,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency, Money

NGN = Currency("NGN", 2)
START = datetime(2026, 8, 1, tzinfo=UTC)
END = datetime(2026, 9, 1, tzinfo=UTC)


def _m(value: str) -> Money:
    return Money(Decimal(value), NGN)


def _bal(r: str, c: str = "0", p: str = "0") -> StatementBalancesV1:
    return StatementBalancesV1(_m(r), _m(c), _m(p))


def _statement() -> AccountStatementV1:
    scope = TenantScope(uuid4())
    account = uuid4()
    opening = _bal("10")
    line = AccountStatementLineV1(
        1,
        uuid4(),
        1,
        "invoice_issued",
        "document:x:issue",
        START,
        None,
        _bal("120"),
        _bal("130"),
    )
    closing = _bal("130")
    digest = compute_account_statement_digest(
        scope=scope,
        billing_account_id=account,
        currency=NGN,
        occurred_from=START,
        occurred_to=END,
        watermark_version=1,
        opening=opening,
        lines=(line,),
        closing=closing,
    )
    return AccountStatementV1(
        scope, account, NGN, START, END, 1, opening, (line,), closing, digest
    )


def test_sealed_statement_builds_and_digest_is_deterministic():
    one = _statement()
    assert one.digest == compute_account_statement_digest(
        scope=one.scope,
        billing_account_id=one.billing_account_id,
        currency=NGN,
        occurred_from=START,
        occurred_to=END,
        watermark_version=1,
        opening=one.opening,
        lines=one.lines,
        closing=one.closing,
    )


def test_edited_digest_line_or_closing_cannot_be_constructed():
    s = _statement()
    with pytest.raises(ValueError):
        replace(s, digest="0" * 64)
    with pytest.raises(ValueError):
        replace(s, closing=_bal("999"))
    with pytest.raises(ValueError):
        replace(s, lines=(replace(s.lines[0], source_ref="forged"),))
    with pytest.raises(ValueError):
        replace(s, lines=(replace(s.lines[0], balance=_bal("131")),))
