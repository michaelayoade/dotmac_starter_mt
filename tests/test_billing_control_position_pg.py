"""Hosted PostgreSQL proofs for the receivable control position read (R1).

Both planes. The caller owns a READ ONLY REPEATABLE READ transaction; the read
refuses anything else, never takes locks and never sees a later commit.
"""

# SQL identifiers come only from fixed table names.
# ruff: noqa: S608
from uuid import uuid4

import pytest
from dotmac_billing import (
    get_current_receivable_position,
    get_receivable_control_position_v1,
    service,
)
from dotmac_billing.contracts import GetCurrentReceivablePositionV1
from dotmac_billing.errors import BillingRuleViolation
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.money import Currency
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from tests import test_billing_isolation as b
from tests import test_billing_non_cash_credit as runtime
from tests import test_billing_opening_cash_import as opening

scratch = opening.scratch


def _settle(db, scope, account):
    service.accept_settlement(
        db,
        scope=scope,
        command=b._settlement_command(account, scope=scope, source_key=str(uuid4())),
        accepted_confirmation_evidence=frozenset({"provider_settled"}),
    )


def _populate(scratch, url, scope):
    """X: invoiced. Y: invoiced and paid. Z: pristine (no posting history)."""
    with opening._session(scratch, url, scope) as db:
        x, y, z = (opening._new_account(db, scope) for _ in range(3))
        opening._invoice(db, scope, x)
        opening._invoice(db, scope, y)
        _settle(db, scope, y)
        db.commit()
    return x, y, z


def _snapshot(db, mode="REPEATABLE READ"):
    db.rollback()
    db.execute(text(f"SET TRANSACTION ISOLATION LEVEL {mode} READ ONLY"))


def _read(url, scope):
    with b._session(url, scope) as db:
        _snapshot(db)
        return get_receivable_control_position_v1(db, scope=scope)


def _superuser(url):
    return create_engine(b._url_for(b._superuser_url(), make_url(url).database))


def _tamper(url, plane, statement, **params):
    """Plant an impossible stored state, past the append-only guards."""
    engine = _superuser(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET session_replication_role = replica"))
            connection.execute(text(statement), params)
    finally:
        engine.dispose()


def _table(plane):
    prefix = "platform_" if plane == "platform" else ""
    return f"mod_billing.{prefix}receivable_position_facts_v2"


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_exact_sums_ignore_a_pristine_account_and_take_the_latest_version(
    scratch, plane
):
    scope, url = runtime._scope_url(scratch, plane)
    x, y, z = _populate(scratch, url, scope)
    result = _read(url, scope)
    assert result.contract == "billing.receivable.control_position.v1"
    assert result.snapshot_at is not None
    (ngn,) = result.currencies
    assert (ngn.currency, ngn.precision, ngn.accounts) == ("NGN", 2, 2)
    assert ngn.collectible_receivable_minor == 24000  # two issued invoices
    assert ngn.available_credit_minor == 12000  # one confirmed settlement
    assert ngn.available_non_cash_credit_minor == 0
    assert ngn.prepaid_funding_minor == 0
    assert len(ngn.position_fingerprint) == 64
    # Each account's own verified read agrees with its part of the sum.
    with b._session(url, scope) as db:
        for account, receivable in ((x, "120"), (y, "120")):
            current = get_current_receivable_position(
                db,
                scope=scope,
                request=GetCurrentReceivablePositionV1(
                    scope, account, Currency("NGN", 2)
                ),
            )
            assert current.position.collectible_receivable == b._money(receivable)
    # A later posting moves the latest version and the fingerprint (X posts again).
    with opening._session(scratch, url, scope) as db:
        opening._invoice(db, scope, x)
        db.commit()
    again = _read(url, scope).currencies[0]
    assert again.collectible_receivable_minor == 36000
    assert again.position_fingerprint != ngn.position_fingerprint


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("mode", ["READ COMMITTED", "READ ONLY"])
def test_a_wrong_transaction_is_refused(scratch, plane, mode):
    scope, url = runtime._scope_url(scratch, plane)
    _populate(scratch, url, scope)
    with b._session(url, scope) as db:
        db.rollback()
        if mode == "READ ONLY":  # read-only but READ COMMITTED
            db.execute(text("SET TRANSACTION READ ONLY"))
        else:  # read-write READ COMMITTED
            db.execute(text("SELECT 1"))
        with pytest.raises(BillingRuleViolation) as refused:
            get_receivable_control_position_v1(db, scope=scope)
        assert refused.value.code == (
            "billing.control_position_requires_repeatable_read_read_only"
        )
    with b._session(url, scope) as db:  # read-write REPEATABLE READ
        db.rollback()
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        with pytest.raises(BillingRuleViolation) as refused:
            get_receivable_control_position_v1(db, scope=scope)
        assert refused.value.code == (
            "billing.control_position_requires_repeatable_read_read_only"
        )


def test_serializable_read_only_is_accepted(scratch):
    scope, url = runtime._scope_url(scratch, "tenant")
    _populate(scratch, url, scope)
    with b._session(url, scope) as db:
        _snapshot(db, "SERIALIZABLE")
        assert get_receivable_control_position_v1(db, scope=scope).currencies


@pytest.mark.parametrize("value", ["", None, "other"])
def test_a_wrong_or_unset_tenant_context_is_refused_not_zero(scratch, value):
    scope, url = runtime._scope_url(scratch, "tenant")
    _populate(scratch, url, scope)
    other = str(uuid4()) if value == "other" else value
    with b._session(url, scope) as db:
        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :v, false)"),
            {"v": other or ""},
        )
        db.commit()
        _snapshot(db)
        with pytest.raises(BillingRuleViolation) as refused:
            get_receivable_control_position_v1(db, scope=scope)
        assert refused.value.code == "billing.control_position_tenant_context"


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_a_commit_after_the_snapshot_began_is_invisible(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    x, _y, _z = _populate(scratch, url, scope)
    with b._session(url, scope) as snapshot:
        _snapshot(snapshot)
        first = get_receivable_control_position_v1(snapshot, scope=scope)
        with opening._session(scratch, url, scope) as other:  # second connection
            opening._invoice(other, scope, x)
            other.commit()
        second = get_receivable_control_position_v1(snapshot, scope=scope)
        assert second.currencies == first.currencies  # the same instant
        assert second.snapshot_at == first.snapshot_at
    assert _read(url, scope).currencies != first.currencies  # a new transaction sees it


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_a_history_without_its_v2_position_is_refused_not_summed(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    x, _y, _z = _populate(scratch, url, scope)
    # Pre-bi_0005 shape: posting groups exist but no V2 row (V1 only).
    _tamper(
        url,
        plane,
        f"DELETE FROM {_table(plane)} WHERE billing_account_id = :a",
        a=x,
    )
    with b._session(url, scope) as db:
        _snapshot(db)
        with pytest.raises(BillingRuleViolation) as refused:
            get_receivable_control_position_v1(db, scope=scope)
        assert refused.value.code == "billing.control_position_incomplete"
        assert "1 account" in refused.value.message


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_a_foreign_source_owner_row_is_refused(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    x, _y, _z = _populate(scratch, url, scope)
    _tamper(
        url,
        plane,
        f"UPDATE {_table(plane)} SET source_owner = 'someone-else' "
        "WHERE billing_account_id = :a",
        a=x,
    )
    with b._session(url, scope) as db:
        _snapshot(db)
        with pytest.raises(BillingRuleViolation) as refused:
            get_receivable_control_position_v1(db, scope=scope)
        assert refused.value.code == "billing.control_position_source_owner"


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_the_latest_version_read_can_use_the_unique_version_index(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    _populate(scratch, url, scope)
    where = "tenant_id = :t AND " if plane == "tenant" else ""
    params = {"t": scope.tenant_id} if isinstance(scope, TenantScope) else {}
    engine = _superuser(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("SET LOCAL enable_seqscan = off"))
            plan = "\n".join(
                row[0]
                for row in connection.execute(
                    text(
                        f"EXPLAIN SELECT DISTINCT ON (billing_account_id, currency) "
                        f"billing_account_id, source_version FROM {_table(plane)} "
                        f"WHERE {where}source_owner = 'dotmac-billing' "
                        "ORDER BY billing_account_id DESC, currency DESC, "
                        "source_version DESC"
                    ),
                    params,
                )
            )
    finally:
        engine.dispose()
    assert "uq_" in plan and "receivable_position_facts_v2_version" in plan, plan
