"""B3: read-only account statement over the ordered ledger (live PostgreSQL)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from dotmac_billing import (
    GetAccountStatementV1,
    get_account_statement,
)
from dotmac_billing.commands import ApplyAvailableCreditCommand
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.service import (
    accept_settlement,
    apply_available_credit,
    rebuild_receivable_position,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.money import Currency
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import event

from tests import test_billing_isolation as billing

NGN = Currency("NGN", 2)


@pytest.fixture(scope="module")
def scratch():
    with billing._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _scope(plane, admin_url):
    return (
        TenantScope(billing._create_tenants(admin_url, 1)[0])
        if plane == "tenant"
        else PlatformScope()
    )


def _request(scope, account_id, *, start=None, end=None):
    return GetAccountStatementV1(
        scope,
        account_id,
        NGN,
        start or billing.NOW - timedelta(days=30),
        end or billing.NOW + timedelta(days=30),
    )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_statement_follows_the_ledger_and_is_read_only(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = _scope(plane, admin_url)
    url = tenant_url if plane == "tenant" else platform_url
    with billing._session(url, scope) as session:
        account, _, invoice = billing._seed_invoice(session, scope, suffix=plane)
        accept_settlement(
            session,
            scope=scope,
            command=billing._settlement_command(
                account.id, scope=scope, source_key=f"stmt-{plane}", amount="120.00"
            ),
            accepted_confirmation_evidence=frozenset({"provider_settled"}),
        )
        apply_available_credit(
            session,
            scope=scope,
            command=ApplyAvailableCreditCommand(
                account.id, invoice.id, billing._money("50.00"), billing.NOW, f"s-{plane}"
            ),
        )
        session.commit()

        statements: list[str] = []
        event.listen(
            session.get_bind(),
            "before_cursor_execute",
            lambda c, cur, stmt, *a: statements.append(stmt),
        )
        statement = get_account_statement(
            session, scope=scope, request=_request(scope, account.id)
        )
        assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
        assert not session.new and not session.dirty

        kinds = [line.group_kind for line in statement.lines]
        assert kinds[0] == "invoice_issued"
        assert [line.sequence for line in statement.lines] == list(
            range(1, len(kinds) + 1)
        )
        position = rebuild_receivable_position(
            session,
            scope=scope,
            billing_account_id=account.id,
            currency="NGN",
            minor_units=2,
        )
        assert statement.opening.receivable.amount == Decimal(0)
        assert statement.closing.receivable.amount == position.collectible_receivable
        assert statement.closing.available_credit.amount == position.available_credit
        assert statement.closing.prepaid_funding.amount == position.prepaid_funding
        # Lanes are separate and a deterministic re-read seals identically.
        again = get_account_statement(
            session, scope=scope, request=_request(scope, account.id)
        )
        assert again.digest == statement.digest


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_window_opening_balance_and_isolation(scratch, plane):
    admin_url, tenant_url, platform_url = scratch
    scope = _scope(plane, admin_url)
    url = tenant_url if plane == "tenant" else platform_url
    with billing._session(url, scope) as session:
        account, _, _ = billing._seed_invoice(session, scope, suffix=f"w-{plane}")
        other, _, _ = billing._seed_invoice(session, scope, suffix=f"o-{plane}")
        after = get_account_statement(
            session,
            scope=scope,
            request=_request(
                scope,
                account.id,
                start=billing.NOW + timedelta(days=1),
                end=billing.NOW + timedelta(days=2),
            ),
        )
        # Everything is before the window: it is all opening, no lines.
        assert after.lines == ()
        assert after.opening == after.closing
        assert after.closing.receivable.amount == Decimal("120")
        full = get_account_statement(
            session, scope=scope, request=_request(scope, account.id)
        )
        assert all(line.posting_group_id for line in full.lines)
        assert other.id != account.id
        with pytest.raises(BillingRuleViolation):
            get_account_statement(
                session,
                scope=scope,
                request=replace(
                    _request(scope, account.id), occurred_to=billing.NOW - timedelta(days=60)
                ),
            )
        session.rollback()


def test_other_tenant_cannot_read_the_account(scratch):
    admin_url, tenant_url, _ = scratch
    left, right = billing._create_tenants(admin_url, 2)
    left_scope, right_scope = TenantScope(left), TenantScope(right)
    with billing._session(tenant_url, left_scope) as session:
        account, _, _ = billing._seed_invoice(session, left_scope, suffix="iso")
        account_id = account.id
    with billing._session(tenant_url, right_scope) as session:
        with pytest.raises(BillingRuleViolation):
            get_account_statement(
                session, scope=right_scope, request=_request(right_scope, account_id)
            )
