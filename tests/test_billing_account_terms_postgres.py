# ruff: noqa: S608, E501
"""Account terms (bi_0018): live PostgreSQL proofs on both planes.

Append-only tables, forced RLS (tenant plane), SELECT/INSERT-only grants, DB-time
back-dating refusal, uniqueness, and effective-value resolution at boundaries.
Needs ``TEST_DATABASE_URL`` (skips otherwise, like the other Billing plane proofs).
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_billing import (
    BillingConflict,
    BillingRuleViolation,
    RecordAccountTermsV1,
    RecordDefaultTermsV1,
    get_effective_account_terms,
    record_account_terms,
    record_default_terms,
)
from dotmac_billing.service import create_billing_account
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from tests.test_billing_isolation import (
    _create_tenants,
    _scratch_database,
    _session,
)

PLANES = ["tenant", "platform"]
ACTOR = "terms-test"
REASON = "test fixture"


def _prefix(plane: str) -> str:
    return "" if plane == "tenant" else "platform_"


@contextmanager
def _plane(plane: str):
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with _scratch_database((selection,)) as (admin_url, tenant_url, platform_url):
        if plane == "tenant":
            scope = TenantScope(_create_tenants(admin_url, 1)[0])
            yield admin_url, tenant_url, scope
        else:
            yield admin_url, platform_url, PlatformScope()


def _account(session, scope, ref: str | None = None):
    account = create_billing_account(
        session,
        scope=scope,
        external_account_ref=ref or f"terms-{uuid.uuid4()}",
        currency="NGN",
        minor_units=2,
    )
    session.commit()
    return account


def _default(scope, at, **overrides) -> RecordDefaultTermsV1:
    values = {
        "contract_type": "postpaid",
        "period": "monthly",
        "postpaid_billing_timing": "period_end",
        "postpaid_grace": timedelta(days=3),
    }
    values.update(overrides)
    return RecordDefaultTermsV1(
        scope=scope, effective_from=at, actor_ref=ACTOR, reason=REASON, **values
    )


def _terms(scope, account_id, at, **fields) -> RecordAccountTermsV1:
    return RecordAccountTermsV1(
        scope=scope,
        billing_account_id=account_id,
        effective_from=at,
        actor_ref=ACTOR,
        reason=REASON,
        **fields,
    )


def _base() -> datetime:
    return datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)


@pytest.mark.parametrize("plane", PLANES)
def test_effective_terms_pick_the_right_version_at_every_boundary(plane):
    with _plane(plane) as (_admin, url, scope):
        base = _base()
        with _session(url, scope) as session:
            account = _account(session, scope)
            d1 = record_default_terms(session, command=_default(scope, base))
            d2 = record_default_terms(
                session,
                command=_default(
                    scope,
                    base + timedelta(days=10),
                    contract_type="prepaid",
                    period="quarterly",
                    postpaid_billing_timing="period_start",
                    postpaid_grace=timedelta(0),
                ),
            )
            a1 = record_account_terms(
                session,
                command=_terms(
                    scope,
                    account.id,
                    base + timedelta(days=5),
                    period="daily",
                    postpaid_grace=timedelta(days=7),
                ),
            )
            a2 = record_account_terms(
                session,
                command=_terms(
                    scope,
                    account.id,
                    base + timedelta(days=15),
                    contract_type="prepaid",
                ),
            )
            session.commit()

            def at(instant):
                return get_effective_account_terms(
                    session, scope=scope, account_id=account.id, at=instant
                )

            with pytest.raises(BillingRuleViolation) as refused:
                at(base - timedelta(microseconds=1))
            assert refused.value.code == "billing.terms_default_missing"

            t = at(base)
            assert (t.contract_type.value, t.contract_type.source) == (
                "postpaid",
                "default",
            )
            assert {
                x.version_id
                for x in (
                    t.contract_type,
                    t.period,
                    t.postpaid_billing_timing,
                    t.postpaid_grace,
                )
            } == {d1.id}

            just_before = at(base + timedelta(days=5) - timedelta(microseconds=1))
            assert just_before.period.source == "default"
            assert just_before.period.value == "monthly"

            t = at(base + timedelta(days=5))  # a1 takes effect exactly here
            assert (t.period.value, t.period.source, t.period.version_id) == (
                "daily",
                "customer",
                a1.id,
            )
            assert t.postpaid_grace.value == timedelta(days=7)
            assert t.contract_type.source == "default"
            assert t.contract_type.version_id == d1.id

            t = at(base + timedelta(days=12))  # mixed: d2 default + a1 customer
            assert (t.contract_type.value, t.contract_type.version_id) == (
                "prepaid",
                d2.id,
            )
            assert t.postpaid_billing_timing.value == "period_start"
            assert (t.period.value, t.period.source) == ("daily", "customer")
            assert (t.postpaid_grace.value, t.postpaid_grace.source) == (
                timedelta(days=7),
                "customer",
            )

            # a2 is a complete statement: NULL period/grace return to d2's default.
            t = at(base + timedelta(days=15))
            assert (t.contract_type.value, t.contract_type.source) == (
                "prepaid",
                "customer",
            )
            assert t.contract_type.version_id == a2.id
            assert (t.period.value, t.period.source) == ("quarterly", "default")
            assert (t.postpaid_grace.value, t.postpaid_grace.source) == (
                timedelta(0),
                "default",
            )


@pytest.mark.parametrize("plane", PLANES)
def test_an_account_without_versions_takes_every_term_from_the_default(plane):
    with _plane(plane) as (_admin, url, scope):
        base = _base()
        with _session(url, scope) as session:
            account = _account(session, scope)
            default = record_default_terms(session, command=_default(scope, base))
            session.commit()
            terms = get_effective_account_terms(
                session, scope=scope, account_id=account.id, at=base
            )
            assert terms.contract_type.source == "default"
            assert terms.postpaid_grace.version_id == default.id
            with pytest.raises(BillingRuleViolation) as unknown:
                get_effective_account_terms(
                    session, scope=scope, account_id=uuid.uuid4(), at=base
                )
            assert unknown.value.code == "billing.account_not_found"


@pytest.mark.parametrize("plane", PLANES)
def test_back_dating_is_refused_by_database_time(plane):
    with _plane(plane) as (_admin, url, scope):
        past = datetime.now(UTC) - timedelta(hours=1)
        with _session(url, scope) as session:
            account = _account(session, scope)
            with pytest.raises(BillingRuleViolation) as one:
                record_default_terms(session, command=_default(scope, past))
            assert one.value.code == "billing.terms_back_dated"
            with pytest.raises(BillingRuleViolation) as two:
                record_account_terms(
                    session, command=_terms(scope, account.id, past, period="daily")
                )
            assert two.value.code == "billing.terms_back_dated"
            # the savepoint left the caller's transaction usable
            ok = record_default_terms(
                session, command=_default(scope, datetime.now(UTC) + timedelta(days=1))
            )
            assert ok.recorded_at <= ok.effective_from
            session.commit()


@pytest.mark.parametrize("plane", PLANES)
def test_a_caller_supplied_recorded_at_is_overwritten_by_the_database(plane):
    with _plane(plane) as (_admin, url, scope):
        prefix = _prefix(plane)
        long_ago = datetime(2000, 1, 1, tzinfo=UTC)
        tenant_column = "tenant_id," if plane == "tenant" else ""
        tenant_value = ":tenant_id," if plane == "tenant" else ""
        with _session(url, scope) as session:
            session.execute(
                text(
                    f"INSERT INTO mod_billing.{prefix}billing_terms_defaults_versions "
                    f"({tenant_column}id,effective_from,contract_type,period,"
                    "postpaid_billing_timing,postpaid_grace,actor_ref,reason,recorded_at) "
                    f"VALUES ({tenant_value}:id,:eff,'prepaid','daily','period_start',"
                    "interval '0','a','r',:recorded)"
                ),
                {
                    "tenant_id": getattr(scope, "tenant_id", None),
                    "id": uuid.uuid4(),
                    "eff": datetime.now(UTC) + timedelta(days=1),
                    "recorded": long_ago,
                },
            )
            stamped = session.execute(
                text(
                    f"SELECT recorded_at FROM mod_billing.{prefix}"
                    "billing_terms_defaults_versions"
                )
            ).scalar_one()
            assert stamped > datetime.now(UTC) - timedelta(minutes=5)
            session.rollback()


@pytest.mark.parametrize("plane", PLANES)
def test_exact_retry_at_the_same_effective_from_is_refused(plane):
    with _plane(plane) as (_admin, url, scope):
        base = _base()
        with _session(url, scope) as session:
            first, other = _account(session, scope), _account(session, scope)
            record_default_terms(session, command=_default(scope, base))
            record_account_terms(
                session, command=_terms(scope, first.id, base, period="daily")
            )
            # another account may share the instant
            record_account_terms(
                session, command=_terms(scope, other.id, base, period="daily")
            )
            session.commit()
            with pytest.raises(BillingConflict) as dup_default:
                record_default_terms(session, command=_default(scope, base))
            assert dup_default.value.code == "billing.terms_version_exists"
            with pytest.raises(BillingConflict) as dup_account:
                record_account_terms(
                    session, command=_terms(scope, first.id, base, period="daily")
                )
            assert dup_account.value.code == "billing.terms_version_exists"
            session.rollback()


@pytest.mark.parametrize("plane", PLANES)
def test_a_later_recorded_version_supersedes_a_future_one(plane):
    with _plane(plane) as (_admin, url, scope):
        prefix = _prefix(plane)
        base = _base()
        later = base + timedelta(days=10)
        with _session(url, scope) as session:
            account = _account(session, scope)
            record_default_terms(session, command=_default(scope, base))
            record_default_terms(
                session, command=_default(scope, later, period="daily")
            )
            wrong = record_account_terms(
                session,
                command=_terms(
                    scope,
                    account.id,
                    later,
                    period="annually",
                    postpaid_grace=timedelta(days=9),
                ),
            )
            right = record_account_terms(
                session,
                command=_terms(scope, account.id, later, contract_type="prepaid"),
            )
            session.commit()
            assert right.recorded_at > wrong.recorded_at
            # both rows stay (append-only)
            rows = session.execute(
                text(
                    f"SELECT count(*) FROM mod_billing.{prefix}"
                    "billing_account_terms_versions"
                )
            ).scalar_one()
            assert rows == 2
            # the superseding version replaces the earlier one wholly: period and
            # grace fall back to the default, not to the superseded row
            at_later = get_effective_account_terms(
                session, scope=scope, account_id=account.id, at=later
            )
            assert at_later.contract_type.source == "customer"
            assert at_later.contract_type.version_id == right.id
            assert at_later.period.value == "daily"
            assert at_later.period.source == "default"
            assert at_later.postpaid_grace.value == timedelta(days=3)
            assert at_later.postpaid_grace.source == "default"
            # an instant between the versions: no account version yet
            between = get_effective_account_terms(
                session,
                scope=scope,
                account_id=account.id,
                at=later - timedelta(seconds=1),
            )
            assert between.contract_type.source == "default"
            assert between.period.value == "monthly"
            # a third supersede wins again; the id/recorded_at order is stable
            third = record_account_terms(
                session, command=_terms(scope, account.id, later, period="quarterly")
            )
            session.commit()
            resolved = get_effective_account_terms(
                session, scope=scope, account_id=account.id, at=later
            )
            assert resolved.period.version_id == third.id
            assert resolved.contract_type.source == "default"


@pytest.mark.parametrize("plane", PLANES)
def test_the_default_can_be_superseded_before_it_takes_effect(plane):
    with _plane(plane) as (_admin, url, scope):
        base = _base()
        with _session(url, scope) as session:
            account = _account(session, scope)
            record_default_terms(session, command=_default(scope, base))
            fixed = record_default_terms(
                session, command=_default(scope, base, period="quarterly")
            )
            session.commit()
            resolved = get_effective_account_terms(
                session, scope=scope, account_id=account.id, at=base
            )
            assert resolved.period.value == "quarterly"
            assert resolved.period.version_id == fixed.id


@pytest.mark.parametrize("plane", PLANES)
def test_a_version_already_in_effect_cannot_be_superseded(plane):
    with _plane(plane) as (_admin, url, scope):
        soon = datetime.now(UTC) + timedelta(seconds=2)
        with _session(url, scope) as session:
            account = _account(session, scope)
            record_default_terms(session, command=_default(scope, soon))
            record_account_terms(
                session, command=_terms(scope, account.id, soon, period="daily")
            )
            session.commit()
            time.sleep(max((soon - datetime.now(UTC)).total_seconds(), 0) + 0.2)
            with pytest.raises(BillingRuleViolation) as one:
                record_default_terms(
                    session, command=_default(scope, soon, period="annually")
                )
            assert one.value.code == "billing.terms_back_dated"
            with pytest.raises(BillingRuleViolation) as two:
                record_account_terms(
                    session,
                    command=_terms(scope, account.id, soon, period="annually"),
                )
            assert two.value.code == "billing.terms_back_dated"
            resolved = get_effective_account_terms(
                session, scope=scope, account_id=account.id, at=soon
            )
            assert resolved.period.value == "daily"


@pytest.mark.parametrize("plane", PLANES)
def test_uniqueness_is_effective_from_and_recorded_at_by_constraint_name(plane):
    with _plane(plane) as (admin_url, _url, _scope):
        prefix = _prefix(plane)
        engine = create_engine(admin_url)
        with engine.connect() as connection:
            for table in (
                "billing_account_terms_versions",
                "billing_terms_defaults_versions",
            ):
                name = f"{prefix}{table}"
                columns = connection.execute(
                    text(
                        "SELECT array_agg(a.attname::text ORDER BY a.attname) "
                        "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                        "JOIN pg_namespace n ON n.oid = t.relnamespace "
                        "JOIN pg_attribute a ON a.attrelid = t.oid "
                        "AND a.attnum = ANY (c.conkey) "
                        "WHERE n.nspname = 'mod_billing' AND t.relname = :t "
                        "AND c.conname = :c"
                    ),
                    {"t": name, "c": f"uq_{name}_effective"},
                ).scalar_one()
                assert {"effective_from", "recorded_at"} <= set(columns)
        engine.dispose()


@pytest.mark.parametrize("plane", PLANES)
def test_online_role_has_select_insert_only_and_rows_are_immutable(plane):
    prefix = _prefix(plane)
    tables = (
        f"{prefix}billing_account_terms_versions",
        f"{prefix}billing_terms_defaults_versions",
    )
    with _plane(plane) as (admin_url, url, scope):
        role = "app_user" if plane == "tenant" else "platform_api"
        admin = create_engine(admin_url)
        try:
            with admin.connect() as connection:
                for table in tables:
                    grants = {
                        privilege: connection.execute(
                            text("SELECT has_table_privilege(:r, :t, :p)"),
                            {"r": role, "t": f"mod_billing.{table}", "p": privilege},
                        ).scalar_one()
                        for privilege in (
                            "SELECT",
                            "INSERT",
                            "UPDATE",
                            "DELETE",
                            "TRUNCATE",
                        )
                    }
                    assert grants == {
                        "SELECT": True,
                        "INSERT": True,
                        "UPDATE": False,
                        "DELETE": False,
                        "TRUNCATE": False,
                    }
        finally:
            admin.dispose()

        with _session(url, scope) as session:
            account = _account(session, scope)
            record_default_terms(session, command=_default(scope, _base()))
            record_account_terms(
                session, command=_terms(scope, account.id, _base(), period="daily")
            )
            session.commit()
            for table in tables:
                for statement in (
                    f"UPDATE mod_billing.{table} SET reason = 'x'",
                    f"DELETE FROM mod_billing.{table}",
                ):
                    with pytest.raises(DBAPIError):
                        session.execute(text(statement))
                    session.rollback()

        # even the owning role is stopped by the append-only trigger
        owner = create_engine(admin_url, isolation_level="AUTOCOMMIT")
        try:
            with owner.connect() as connection:
                for table in tables:
                    with pytest.raises(DBAPIError, match="immutable"):
                        connection.execute(
                            text(f"UPDATE mod_billing.{table} SET reason = 'x'")
                        )
                    with pytest.raises(DBAPIError, match="immutable"):
                        connection.execute(text(f"DELETE FROM mod_billing.{table}"))
        finally:
            owner.dispose()


@pytest.mark.parametrize("plane", PLANES)
def test_database_refuses_out_of_domain_values(plane):
    prefix = _prefix(plane)
    tenant_column = "tenant_id," if plane == "tenant" else ""
    tenant_value = ":tenant_id," if plane == "tenant" else ""
    with _plane(plane) as (_admin, url, scope):
        with _session(url, scope) as session:
            for column, bad in (
                ("contract_type", "'hybrid'"),
                ("period", "'weekly'"),
                ("postpaid_billing_timing", "'mid'"),
            ):
                values = {
                    "contract_type": "'prepaid'",
                    "period": "'daily'",
                    "postpaid_billing_timing": "'period_start'",
                }
                values[column] = bad
                with pytest.raises(DBAPIError):
                    session.execute(
                        text(
                            f"INSERT INTO mod_billing.{prefix}"
                            f"billing_terms_defaults_versions ({tenant_column}id,"
                            "effective_from,contract_type,period,"
                            "postpaid_billing_timing,postpaid_grace,actor_ref,reason)"
                            f" VALUES ({tenant_value}:id,:eff,{values['contract_type']},"
                            f"{values['period']},{values['postpaid_billing_timing']},"
                            "interval '0','a','r')"
                        ),
                        {
                            "tenant_id": getattr(scope, "tenant_id", None),
                            "id": uuid.uuid4(),
                            "eff": datetime.now(UTC) + timedelta(days=1),
                        },
                    )
                session.rollback()


def test_rls_is_forced_and_tenants_are_isolated():
    with _plane("tenant") as (admin_url, url, scope_a):
        (tenant_b,) = _create_tenants(admin_url, 1)
        scope_b = TenantScope(tenant_b)
        admin = create_engine(admin_url)
        try:
            with admin.connect() as connection:
                for table in (
                    "billing_account_terms_versions",
                    "billing_terms_defaults_versions",
                ):
                    flags = connection.execute(
                        text(
                            "SELECT c.relrowsecurity, c.relforcerowsecurity "
                            "FROM pg_class c JOIN pg_namespace n "
                            "ON n.oid = c.relnamespace "
                            "WHERE n.nspname = 'mod_billing' AND c.relname = :t"
                        ),
                        {"t": table},
                    ).one()
                    assert tuple(flags) == (True, True)
        finally:
            admin.dispose()

        base = _base()
        with _session(url, scope_a) as session_a:
            account_a_id = _account(session_a, scope_a).id
            record_default_terms(session_a, command=_default(scope_a, base))
            record_account_terms(
                session_a, command=_terms(scope_a, account_a_id, base, period="daily")
            )
            session_a.commit()
        with _session(url, scope_b) as session_b:
            for table in (
                "billing_account_terms_versions",
                "billing_terms_defaults_versions",
            ):
                count = session_b.execute(
                    text(f"SELECT count(*) FROM mod_billing.{table}")
                ).scalar_one()
                assert count == 0
            # tenant B cannot see, resolve or write against tenant A's account
            with pytest.raises(BillingRuleViolation) as unseen:
                get_effective_account_terms(
                    session_b, scope=scope_b, account_id=account_a_id, at=base
                )
            assert unseen.value.code == "billing.account_not_found"
            with pytest.raises(BillingRuleViolation) as unwritable:
                record_account_terms(
                    session_b, command=_terms(scope_b, account_a_id, base)
                )
            assert unwritable.value.code == "billing.account_not_found"
            # raw insert naming tenant A is stopped by the policy's WITH CHECK
            with pytest.raises(DBAPIError):
                session_b.execute(
                    text(
                        "INSERT INTO mod_billing.billing_terms_defaults_versions "
                        "(tenant_id,id,effective_from,contract_type,period,"
                        "postpaid_billing_timing,postpaid_grace,actor_ref,reason) "
                        "VALUES (:t,:id,:eff,'prepaid','daily','period_start',"
                        "interval '0','a','r')"
                    ),
                    {"t": scope_a.tenant_id, "id": uuid.uuid4(), "eff": base},
                )
            session_b.rollback()
            # B has no default of its own, so even a valid account refuses
            account_b = _account(session_b, scope_b)
            with pytest.raises(BillingRuleViolation) as missing:
                get_effective_account_terms(
                    session_b, scope=scope_b, account_id=account_b.id, at=base
                )
            assert missing.value.code == "billing.terms_default_missing"
