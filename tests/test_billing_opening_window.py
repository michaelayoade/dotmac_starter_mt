"""Hosted PostgreSQL custody-window isolation and authority canaries."""

from __future__ import annotations

# SQL identifiers are closed plane/table names or fixture-generated role names.
# ruff: noqa: S608
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from threading import Barrier, Event, Thread
from time import monotonic, sleep
from uuid import uuid4

import pytest
from dotmac_billing import service as s
from dotmac_billing.opening_window_contracts import ProvisionOpeningWindowV1
from dotmac_billing.opening_window_queries import (
    get_opening_window,
    get_opening_window_admissions,
    get_opening_window_seal,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.planes import ModulePlane
from sqlalchemy import create_engine, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from tests import test_billing_isolation as b
from tests import test_billing_opening_cash_import as cash
from tests.billing_opening_window_fixtures import (
    TrustedWindowVerifier,
    _principal,
    _provision,
    _restricted_session,
    _seal_command,
)


def test_window_reads_never_cross_tenant_or_plane(custody_window):
    scratch, scope, window, _, _ = custody_window
    other = TenantScope(b._create_tenants(scratch[0], 1)[0])
    with b._session(scratch[1], other) as session:
        assert get_opening_window(session, scope=other) is None
    with b._session(scratch[1], scope) as session:
        assert get_opening_window(session, scope=scope) == window
        with pytest.raises(DBAPIError):
            get_opening_window(session, scope=PlatformScope())
    with b._session(scratch[2], PlatformScope()) as session:
        with pytest.raises(DBAPIError):
            get_opening_window(session, scope=scope)


def test_window_binding_is_permanent_and_migrator_has_no_financial_admission(
    custody_window,
):
    scratch, scope, window, _, command = custody_window
    with b._session(scratch[0], scope) as session:
        with pytest.raises(cash.BillingRuleViolation, match="verifier"):
            s.provision_opening_window(
                session, scope=scope, command=ProvisionOpeningWindowV1(window)
            )
        with pytest.raises(DBAPIError) as duplicate:
            s.provision_opening_window(
                session,
                scope=scope,
                command=ProvisionOpeningWindowV1(
                    replace(window, window_id=uuid4(), epoch=uuid4())
                ),
                verifier=TrustedWindowVerifier(),
            )
        assert duplicate.value.orig.sqlstate == "23505"
        session.rollback()
        with pytest.raises(DBAPIError) as refused:
            s.import_opening_account(
                session, scope=scope, command=command, verifier=cash.TrustedVerifier()
            )
        assert refused.value.orig.sqlstate == "42501"
        session.rollback()
        for statement in (
            "UPDATE mod_billing.opening_windows SET epoch=:epoch",
            "DELETE FROM mod_billing.opening_windows",
        ):
            with pytest.raises(DBAPIError):
                session.execute(text(statement), {"epoch": uuid4()})
            session.rollback()
        assert get_opening_window(session, scope=scope) == window


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_missing_window_refuses_new_opening(scratch, plane):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = cash._command(session, scope, account)
    with _principal(scratch, plane) as principal:
        with _restricted_session(scratch, scope, principal) as session:
            with pytest.raises(cash.BillingRuleViolation, match="window"):
                cash.s.import_opening_account(
                    session,
                    scope=scope,
                    command=command,
                    verifier=cash.TrustedVerifier(),
                )


@pytest.fixture
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


@pytest.fixture
def custody_window(scratch):
    scope, url = cash.runtime._scope_url(scratch, "tenant")
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = cash._command(session, scope, account)
    with _principal(scratch, "tenant") as principal:
        window = _provision(scratch, scope, principal, command)
        yield scratch, scope, window, principal, command


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("version", [1, 2])
def test_real_principal_import_seal_exact_replay_and_new_account_refusal(
    scratch, plane, version
):
    from tests import test_billing_opening_receivable_import as ar

    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        other = cash._new_account(session, scope)
        command = (cash if version == 1 else ar)._command(session, scope, account)
        other_command = (cash if version == 1 else ar)._command(session, scope, other)
    operation = (
        s.import_opening_account if version == 1 else s.import_opening_account_v2
    )
    verifier = cash.TrustedVerifier() if version == 1 else ar.evidence.TrustedVerifier()
    with _principal(scratch, plane) as principal:
        window = _provision(scratch, scope, principal, command)
        with _restricted_session(scratch, scope, principal) as session:
            assert session.scalar(text("SELECT current_user")) == principal[0]
            receipt = operation(
                session, scope=scope, command=command, verifier=verifier
            )
            session.commit()
            seal_command = _seal_command(session, scope, window)
            seal = s.seal_opening_window(
                session,
                scope=scope,
                command=seal_command,
                verifier=TrustedWindowVerifier(),
            )
            session.commit()
            before = cash._counts(session, plane, account)
            assert (
                operation(session, scope=scope, command=command, verifier=verifier)
                == receipt
            )
            assert cash._counts(session, plane, account) == before
            assert (
                get_opening_window_admissions(session, scope=scope)
                == seal_command.admissions
            )
            assert get_opening_window_seal(session, scope=scope) == seal
            assert (
                s.seal_opening_window(
                    session,
                    scope=scope,
                    command=seal_command,
                    verifier=TrustedWindowVerifier(),
                )
                == seal
            )
            with pytest.raises(cash.IdempotencyConflict):
                operation(
                    session,
                    scope=scope,
                    command=replace(command, migration_authority_ref="changed"),
                    verifier=verifier,
                )
            for offered in (replace(command, idempotency_key="new-key"), other_command):
                with pytest.raises(cash.BillingRuleViolation, match="sealed"):
                    operation(session, scope=scope, command=offered, verifier=verifier)
            assert cash._counts(session, plane, account) == before


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_ordinary_role_cannot_import_or_forge_custody_or_assume_principal(
    scratch, plane
):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = cash._command(session, scope, account)
    with _principal(scratch, plane) as principal:
        _provision(scratch, scope, principal, command)
        with b._session(url, scope) as session:
            with pytest.raises(DBAPIError):
                s.import_opening_account(
                    session,
                    scope=scope,
                    command=command,
                    verifier=cash.TrustedVerifier(),
                )
            session.rollback()
            with pytest.raises(DBAPIError):
                session.execute(text(f'SET ROLE "{principal[0]}"'))
            session.rollback()
            prefix = "platform_" if plane == "platform" else ""
            for table in (
                "opening_windows",
                "opening_window_admissions",
                "opening_window_seals",
                "opening_cash_lots",
                "opening_receivable_bases",
                "opening_historical_discharge_claims",
            ):
                qualified = f"mod_billing.{prefix}{table}"
                assert not session.scalar(
                    text(
                        "SELECT has_table_privilege(current_user, :table, 'INSERT') "
                        "OR has_any_column_privilege(current_user, :table, 'INSERT')"
                    ),
                    {"table": qualified},
                )
                with pytest.raises(DBAPIError):
                    session.execute(text(f"INSERT INTO {qualified} DEFAULT VALUES"))
                session.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_seal_refuses_missing_extra_and_local_mismatched_population(scratch, plane):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = cash._command(session, scope, account)
        baseline = cash._counts(session, plane, account)
        ledger_baseline = _ledger_counts(session, scope)
    with _principal(scratch, plane) as principal:
        window = _provision(scratch, scope, principal, command)
        with _restricted_session(scratch, scope, principal) as session:
            empty = _seal_command(session, scope, window)
            with pytest.raises(cash.BillingRuleViolation, match="population"):
                s.seal_opening_window(
                    session,
                    scope=scope,
                    command=empty,
                    verifier=TrustedWindowVerifier(),
                )
            cash.s.import_opening_account(
                session, scope=scope, command=command, verifier=cash.TrustedVerifier()
            )
            complete = _seal_command(session, scope, window)
            admission = complete.admissions[0]
            for admissions in (
                (),
                complete.admissions * 2,
                (replace(admission, request_digest="f" * 64),),
            ):
                with pytest.raises(
                    cash.BillingRuleViolation, match="admissions|population"
                ):
                    s.seal_opening_window(
                        session,
                        scope=scope,
                        command=replace(
                            complete, command_key=str(uuid4()), admissions=admissions
                        ),
                        verifier=TrustedWindowVerifier(),
                    )
            assert get_opening_window_seal(session, scope=scope) is None
            s.seal_opening_window(
                session, scope=scope, command=complete, verifier=TrustedWindowVerifier()
            )
            session.rollback()
        with _restricted_session(scratch, scope, principal) as observer:
            assert get_opening_window_admissions(observer, scope=scope) == ()
            assert get_opening_window_seal(observer, scope=scope) is None
            assert cash._counts(observer, plane, account) == baseline
            assert _ledger_counts(observer, scope) == ledger_baseline


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("refusal", ["unavailable", "different_binding"])
def test_seal_authority_is_reobserved_under_actual_window_lock_and_refusal_keeps_root(
    scratch, plane, refusal
):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = cash._command(session, scope, account)
    prefix = "platform_" if plane == "platform" else ""
    generation_sql = text(f"SELECT generation FROM mod_billing.{prefix}opening_windows")
    with _principal(scratch, plane) as principal:
        window = _provision(scratch, scope, principal, command)
        with _restricted_session(scratch, scope, principal) as session:
            s.import_opening_account(
                session, scope=scope, command=command, verifier=cash.TrustedVerifier()
            )
            session.commit()
            seal_command = _seal_command(session, scope, window)
            baseline_generation = session.scalar(generation_sql)
            baseline_ledger = _ledger_counts(session, scope)
            baseline_money = cash._counts(session, plane, account)
            root = session.get_transaction()

            class ChangingAuthority(TrustedWindowVerifier):
                def __init__(self):
                    self.phases = []

                def verify_seal(self, request):
                    generation = session.scalar(generation_sql)
                    self.phases.append(generation)
                    qualified = super().verify_seal(request)
                    if len(self.phases) == 1:
                        assert generation == baseline_generation
                        return qualified
                    assert generation == baseline_generation + 1
                    with b._session(url, scope) as independent_observer:
                        assert (
                            independent_observer.scalar(generation_sql)
                            == baseline_generation
                        )
                        assert (
                            get_opening_window_seal(independent_observer, scope=scope)
                            is None
                        )
                        assert (
                            _ledger_counts(independent_observer, scope)
                            == baseline_ledger
                        )
                    return (
                        None
                        if refusal == "unavailable"
                        else replace(qualified, coupled_watermark="different-fence")
                    )

            verifier = ChangingAuthority()
            with pytest.raises(cash.BillingRuleViolation, match="proof does not bind"):
                s.seal_opening_window(
                    session, scope=scope, command=seal_command, verifier=verifier
                )
            assert verifier.phases == [baseline_generation, baseline_generation + 1]
            assert session.get_transaction() is root and root.is_active
            assert get_opening_window_seal(session, scope=scope) is None
            assert session.scalar(generation_sql) == baseline_generation
            assert _ledger_counts(session, scope) == baseline_ledger
            assert cash._counts(session, plane, account) == baseline_money
            session.rollback()
        with b._session(url, scope) as observer:
            assert get_opening_window_seal(observer, scope=scope) is None
            assert _ledger_counts(observer, scope) == baseline_ledger
            assert cash._counts(observer, plane, account) == baseline_money


def _ledger_counts(session, scope):
    prefix = "" if isinstance(scope, TenantScope) else "platform_"
    condition = " WHERE tenant_id=:tenant" if isinstance(scope, TenantScope) else ""
    values = {"tenant": scope.tenant_id} if isinstance(scope, TenantScope) else {}
    return tuple(
        session.scalar(
            text(f"SELECT count(*) FROM public.{prefix}{table}{condition}"), values
        )
        for table in ("idempotency_records", "outbox_events")
    )


@contextmanager
def _unsafe_role_path(engine, principal, capability):
    middle = f"opening_middle_{uuid4().hex}"
    target = f"opening_escape_{uuid4().hex}"
    with engine.begin() as connection:
        connection.execute(text(f'CREATE ROLE "{middle}" NOLOGIN NOINHERIT'))
        if capability == "ordinary_reaches_principal":
            connection.execute(text(f'GRANT "{principal[0]}" TO "{middle}"'))
            connection.execute(text(f'GRANT "{middle}" TO app_user'))
        elif capability == "principal_reaches_ordinary":
            connection.execute(text(f'GRANT app_user TO "{middle}"'))
            connection.execute(text(f'GRANT "{middle}" TO "{principal[0]}"'))
        else:
            connection.execute(text(f'CREATE ROLE "{target}" NOLOGIN {capability}'))
            connection.execute(text(f'GRANT "{target}" TO "{middle}"'))
            connection.execute(text(f'GRANT "{middle}" TO "{principal[0]}"'))
            assert connection.scalar(
                text("SELECT pg_has_role(:principal, :target, 'MEMBER')"),
                {"principal": principal[0], "target": target},
            )
    try:
        yield
    finally:
        with engine.begin() as connection:
            if capability == "ordinary_reaches_principal":
                connection.execute(text(f'REVOKE "{middle}" FROM app_user'))
                connection.execute(text(f'REVOKE "{principal[0]}" FROM "{middle}"'))
            elif capability == "principal_reaches_ordinary":
                connection.execute(text(f'REVOKE "{middle}" FROM "{principal[0]}"'))
                connection.execute(text(f'REVOKE app_user FROM "{middle}"'))
            else:
                connection.execute(text(f'REVOKE "{middle}" FROM "{principal[0]}"'))
                connection.execute(text(f'REVOKE "{target}" FROM "{middle}"'))
                connection.execute(text(f'DROP ROLE "{target}"'))
            connection.execute(text(f'DROP ROLE "{middle}"'))


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_transitive_privileged_and_ordinary_role_escapes_refuse_provision_and_drift(
    scratch, plane
):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = cash._command(session, scope, account)
        baseline = cash._counts(session, plane, account)
    engine = create_engine(
        b._url_for(b._superuser_url(), make_url(scratch[0]).database)
    )
    capabilities = (
        "CREATEROLE",
        "CREATEDB",
        "REPLICATION",
        "ordinary_reaches_principal",
        "principal_reaches_ordinary",
    )
    try:
        with _principal(scratch, plane) as principal:
            for capability in capabilities:
                with _unsafe_role_path(engine, principal, capability):
                    with pytest.raises(DBAPIError) as refused:
                        _provision(scratch, scope, principal, command)
                    assert refused.value.orig.sqlstate == "42501", capability
            _provision(scratch, scope, principal, command)
            for capability in capabilities:
                with _unsafe_role_path(engine, principal, capability):
                    with _restricted_session(scratch, scope, principal) as session:
                        with pytest.raises(DBAPIError) as refused:
                            s.import_opening_account(
                                session,
                                scope=scope,
                                command=command,
                                verifier=cash.TrustedVerifier(),
                            )
                        assert refused.value.orig.sqlstate == "42501", capability
                        session.rollback()
                    if capability == "ordinary_reaches_principal":
                        with b._session(scratch[1], scope) as actual_login:
                            actual_login.execute(
                                text(f'SET LOCAL ROLE "{principal[0]}"')
                            )
                            with pytest.raises(DBAPIError):
                                s.import_opening_account(
                                    actual_login,
                                    scope=scope,
                                    command=command,
                                    verifier=cash.TrustedVerifier(),
                                )
                            actual_login.rollback()
            with b._session(url, scope) as observer:
                assert cash._counts(observer, plane, account) == baseline
                assert get_opening_window_admissions(observer, scope=scope) == ()
    finally:
        engine.dispose()


def _wait_for_database_lock(scratch, pid):
    """Prove actual database blocking before releasing the winning caller."""
    engine = create_engine(
        b._url_for(b._superuser_url(), make_url(scratch[0]).database),
        isolation_level="AUTOCOMMIT",
    )
    try:
        with engine.connect() as observer:
            deadline = monotonic() + 15
            while monotonic() < deadline:
                if observer.scalar(
                    text(
                        "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                        "WHERE pid=:pid"
                    ),
                    {"pid": pid},
                ):
                    return
                sleep(0.01)
    finally:
        engine.dispose()
    pytest.fail("losing caller never blocked on the physical opening window")


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("changed_fingerprint", [False, True])
def test_concurrent_seal_retry_rechecks_winner_after_window_wait(
    scratch, plane, changed_fingerprint, monkeypatch
):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        opening = cash._command(session, scope, account)
    with _principal(scratch, plane) as principal:
        window = _provision(scratch, scope, principal, opening)
        with _restricted_session(scratch, scope, principal) as session:
            receipt = s.import_opening_account(
                session, scope=scope, command=opening, verifier=cash.TrustedVerifier()
            )
            session.commit()
            command = _seal_command(session, scope, window)
            admissions = get_opening_window_admissions(session, scope=scope)
            money = cash._counts(session, plane, account)
            positions = tuple(
                cash.q.get_opening_cash_lot_position(
                    session, scope=scope, lot_id=lot_id
                )
                for lot_id in receipt.lot_ids
            )
        retry = (
            replace(command, authority_ref="fixture:changed-seal-authority")
            if changed_fingerprint
            else command
        )
        both_missed = Barrier(2)
        locked = Event()
        release = Event()
        original_touch = s._touch_opening_window
        results = [{}, {}]

        def touch(session, *, scope):
            index = session.info["seal_contender"]
            # Reaching this callback proves the real Kernel initial lookup missed.
            both_missed.wait(15)
            if index == 1:
                assert locked.wait(15)
                return original_touch(session, scope=scope)
            value = original_touch(session, scope=scope)
            locked.set()
            assert release.wait(20)
            return value

        monkeypatch.setattr(s, "_touch_opening_window", touch)

        def seal(index, frozen_command):
            try:
                with _restricted_session(scratch, scope, principal) as session:
                    session.info["seal_contender"] = index
                    results[index]["pid"] = session.scalar(
                        text("SELECT pg_backend_pid()")
                    )
                    try:
                        results[index]["seal"] = s.seal_opening_window(
                            session,
                            scope=scope,
                            command=frozen_command,
                            verifier=TrustedWindowVerifier(),
                        )
                        session.commit()
                    except Exception:
                        session.rollback()
                        raise
            except Exception as exc:
                results[index]["error"] = exc

        threads = [
            Thread(target=seal, args=(0, command)),
            Thread(target=seal, args=(1, retry)),
        ]
        for thread in threads:
            thread.start()
        try:
            assert locked.wait(15)
            _wait_for_database_lock(scratch, results[1]["pid"])
        finally:
            release.set()
            for thread in threads:
                thread.join(25)
        assert all(not thread.is_alive() for thread in threads)
        assert "error" not in results[0], results
        if changed_fingerprint:
            assert isinstance(
                results[1].get("error"), cash.IdempotencyConflict
            ), results
        else:
            assert "error" not in results[1], results
            assert results[1]["seal"] == results[0]["seal"]
        with _restricted_session(scratch, scope, principal) as observer:
            assert get_opening_window_seal(observer, scope=scope) == results[0]["seal"]
            assert get_opening_window_admissions(observer, scope=scope) == admissions
            assert cash._counts(observer, plane, account) == money
            assert (
                tuple(
                    cash.q.get_opening_cash_lot_position(
                        observer, scope=scope, lot_id=lot_id
                    )
                    for lot_id in receipt.lot_ids
                )
                == positions
            )
            assert (
                cash._retry_count(
                    observer,
                    plane,
                    "billing.opening.window.seal.v1",
                    command.command_key,
                )
                == 1
            )
            prefix = "platform_" if plane == "platform" else ""
            assert (
                observer.scalar(
                    text(
                        f"SELECT count(*) FROM mod_billing.{prefix}opening_window_seals"
                    )
                )
                == 1
            )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize("version", [1, 2])
def test_raw_restricted_receipt_cannot_substitute_manifest_under_frozen_fingerprint(
    scratch, plane, version
):
    from tests import test_billing_opening_receivable_import as ar

    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = (cash if version == 1 else ar)._command(session, scope, account)
    operation = (
        s.import_opening_account if version == 1 else s.import_opening_account_v2
    )
    verifier = cash.TrustedVerifier() if version == 1 else ar.evidence.TrustedVerifier()
    with _principal(scratch, plane) as principal:
        _provision(scratch, scope, principal, command)
        with _restricted_session(scratch, scope, principal) as session:
            receipt = operation(
                session, scope=scope, command=command, verifier=verifier
            )
            receipt_table = cash._models(plane)["OpeningImportReceipt"].__table__
            group_table = cash._models(plane)["PostingGroup"].__table__
            receipt_row = dict(
                session.execute(
                    select(receipt_table).where(
                        receipt_table.c.id == receipt.receipt_id
                    )
                )
                .mappings()
                .one()
            )
            group_row = dict(
                session.execute(
                    select(group_table).where(
                        group_table.c.id == receipt.posting_group_id
                    )
                )
                .mappings()
                .one()
            )
            session.rollback()
            receipt_row["evidence"] = deepcopy(receipt_row["evidence"])
            receipt_row["evidence"]["manifest"]["capture"]["snapshot_id"] = (
                "substituted-source-snapshot"
            )
            # Keep the frozen scalar fingerprint and qualified proof JSON.
            session.execute(insert(group_table).values(**group_row))
            with pytest.raises(DBAPIError) as refused:
                session.execute(insert(receipt_table).values(**receipt_row))
            assert refused.value.orig.sqlstate == "23514"
            assert "one complete manifest" in refused.value.orig.diag.message_primary
            session.rollback()
            assert get_opening_window_admissions(session, scope=scope) == ()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_copied_qualified_receipt_json_cannot_authorize_ordinary_raw_sql(
    scratch, plane
):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = cash._command(session, scope, account)
    with _principal(scratch, plane) as principal:
        _provision(scratch, scope, principal, command)
        with _restricted_session(scratch, scope, principal) as session:
            receipt = s.import_opening_account(
                session, scope=scope, command=command, verifier=cash.TrustedVerifier()
            )
            session.commit()
            table = cash._models(plane)["OpeningImportReceipt"].__table__
            row = dict(
                session.execute(select(table).where(table.c.id == receipt.receipt_id))
                .mappings()
                .one()
            )
        with b._session(url, scope) as session:
            baseline = cash._counts(session, plane, account)
            admissions = get_opening_window_admissions(session, scope=scope)
            assert len(admissions) == 1
            row["id"] = uuid4()
            row["request_identity"] = "forged-new-request"
            # Shared receipt INSERT remains for legitimate append-only inverses.
            assert session.scalar(
                text("SELECT has_table_privilege(current_user, :table, 'INSERT')"),
                {"table": f"mod_billing.{table.name}"},
            )
            with pytest.raises(DBAPIError) as refused:
                session.execute(insert(table).values(**row))
            assert refused.value.orig.sqlstate == "42501"
            session.rollback()
        # The failed caller rolled back its initial connection tenant setting.
        # A fresh scoped observer establishes the canonical context for reads.
        with b._session(url, scope) as observer:
            assert cash._counts(observer, plane, account) == baseline
            assert get_opening_window_admissions(observer, scope=scope) == admissions


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "mutation",
    ["bypassrls", "rename", "recreate", "ordinary_membership", "public_column_insert"],
)
def test_window_rechecks_actual_oid_name_attributes_and_effective_grant_closure(
    scratch, plane, mutation
):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = cash._command(session, scope, account)
        baseline = cash._counts(session, plane, account)
    prefix = "platform_" if plane == "platform" else ""
    engine = create_engine(
        b._url_for(b._superuser_url(), make_url(scratch[0]).database)
    )
    with _principal(scratch, plane) as principal:
        _provision(scratch, scope, principal, command)
        selected = principal
        with engine.begin() as connection:
            if mutation == "bypassrls":
                connection.execute(text(f'ALTER ROLE "{principal[0]}" BYPASSRLS'))
            elif mutation == "rename":
                renamed = f"renamed_{uuid4().hex}"
                connection.execute(
                    text(f'ALTER ROLE "{principal[0]}" RENAME TO "{renamed}"')
                )
                selected = (renamed, principal[1])
            elif mutation == "recreate":
                connection.execute(text(f'DROP OWNED BY "{principal[0]}"'))
                connection.execute(text(f'DROP ROLE "{principal[0]}"'))
                connection.execute(text(f'CREATE ROLE "{principal[0]}" NOLOGIN'))
                connection.execute(
                    text(f'GRANT USAGE ON SCHEMA mod_billing TO "{principal[0]}"')
                )
                connection.execute(
                    text(
                        "GRANT SELECT, UPDATE (generation) ON "
                        f"mod_billing.{prefix}opening_windows "
                        f'TO "{principal[0]}"'
                    )
                )
                assert (
                    connection.scalar(
                        text("SELECT oid FROM pg_roles WHERE rolname=:role"),
                        {"role": principal[0]},
                    )
                    != principal[1]
                )
            elif mutation == "ordinary_membership":
                connection.execute(text(f'GRANT "{principal[0]}" TO app_user'))
            else:
                connection.execute(
                    text(
                        f"GRANT INSERT (id) ON mod_billing.{prefix}opening_cash_lots "
                        "TO PUBLIC"
                    )
                )
        try:
            with _restricted_session(scratch, scope, selected) as session:
                with pytest.raises(DBAPIError) as refused:
                    s.import_opening_account(
                        session,
                        scope=scope,
                        command=command,
                        verifier=cash.TrustedVerifier(),
                    )
                assert refused.value.orig.sqlstate == "42501"
                session.rollback()
            with b._session(url, scope) as observer:
                assert cash._counts(observer, plane, account) == baseline
                assert get_opening_window_admissions(observer, scope=scope) == ()
        finally:
            with engine.begin() as connection:
                if mutation == "bypassrls":
                    connection.execute(text(f'ALTER ROLE "{principal[0]}" NOBYPASSRLS'))
                elif mutation == "rename":
                    connection.execute(
                        text(f'ALTER ROLE "{selected[0]}" RENAME TO "{principal[0]}"')
                    )
                elif mutation == "ordinary_membership":
                    connection.execute(text(f'REVOKE "{principal[0]}" FROM app_user'))
                elif mutation == "public_column_insert":
                    connection.execute(
                        text(
                            "REVOKE INSERT (id) ON "
                            f"mod_billing.{prefix}opening_cash_lots "
                            "FROM PUBLIC"
                        )
                    )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
)
@pytest.mark.parametrize("commit_import", [True, False])
@pytest.mark.parametrize("version", [1, 2])
def test_import_first_seal_race_uses_actual_mvcc_and_whole_caller_retry(
    scratch, plane, isolation, commit_import, version
):
    from tests import test_billing_opening_receivable_import as ar

    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        command = (cash if version == 1 else ar)._command(session, scope, account)
        baseline = cash._counts(session, plane, account)
    operation = (
        s.import_opening_account if version == 1 else s.import_opening_account_v2
    )
    verifier = cash.TrustedVerifier() if version == 1 else ar.evidence.TrustedVerifier()
    with _principal(scratch, plane) as principal:
        window = _provision(scratch, scope, principal, command)
        with _restricted_session(
            scratch, scope, principal, isolation=isolation
        ) as importer:
            operation(importer, scope=scope, command=command, verifier=verifier)
            seal_command = _seal_command(importer, scope, window)
            ready = Event()
            result = {}

            def seal_loser():
                with _restricted_session(
                    scratch, scope, principal, isolation=isolation
                ) as sealer:
                    result["pid"] = sealer.scalar(text("SELECT pg_backend_pid()"))
                    # Establish the older RR/serializable snapshot before waiting.
                    assert get_opening_window(sealer, scope=scope) == window
                    ready.set()
                    try:
                        result["seal"] = s.seal_opening_window(
                            sealer,
                            scope=scope,
                            command=seal_command,
                            verifier=TrustedWindowVerifier(),
                        )
                        sealer.commit()
                    except (DBAPIError, cash.BillingRuleViolation) as exc:
                        result["error"] = exc
                        sealer.rollback()

            thread = Thread(target=seal_loser)
            thread.start()
            try:
                assert ready.wait(15)
                _wait_for_database_lock(scratch, result["pid"])
                with b._session(url, scope) as observer:
                    assert cash._counts(observer, plane, account) == baseline
                    assert get_opening_window_admissions(observer, scope=scope) == ()
                    assert get_opening_window_seal(observer, scope=scope) is None
                if commit_import:
                    importer.commit()
                else:
                    importer.rollback()
            finally:
                importer.rollback()
                thread.join(20)
            assert not thread.is_alive()
            if commit_import and isolation == "READ COMMITTED":
                assert "seal" in result and "error" not in result
            elif commit_import:
                assert isinstance(result.get("error"), DBAPIError)
                assert getattr(result["error"].orig, "sqlstate", None) == "40001"
            else:
                assert isinstance(result.get("error"), cash.BillingRuleViolation)
                assert result["error"].code == "billing.opening_window_population"
            with _restricted_session(scratch, scope, principal) as observer:
                assert len(get_opening_window_admissions(observer, scope=scope)) == int(
                    commit_import
                )
                assert (get_opening_window_seal(observer, scope=scope) is not None) == (
                    commit_import and isolation == "READ COMMITTED"
                )
                if not commit_import:
                    assert cash._counts(observer, plane, account) == baseline
            if commit_import and isolation != "READ COMMITTED":
                with _restricted_session(scratch, scope, principal) as retrier:
                    s.seal_opening_window(
                        retrier,
                        scope=scope,
                        command=seal_command,
                        verifier=TrustedWindowVerifier(),
                    )
                    retrier.commit()
                    assert len(get_opening_window_admissions(retrier, scope=scope)) == 1


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"]
)
@pytest.mark.parametrize("commit_seal", [True, False])
def test_seal_first_blocks_new_historyless_account_and_preserves_observer_state(
    scratch, plane, isolation, commit_seal
):
    scope, url = cash.runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = cash._new_account(session, scope)
        fresh_account = cash._new_account(session, scope)
        command = cash._command(session, scope, account)
        fresh_command = cash._command(session, scope, fresh_account)
        fresh_baseline = cash._counts(session, plane, fresh_account)
    with _principal(scratch, plane) as principal:
        window = _provision(scratch, scope, principal, command)
        with _restricted_session(scratch, scope, principal) as importer:
            s.import_opening_account(
                importer, scope=scope, command=command, verifier=cash.TrustedVerifier()
            )
            importer.commit()
        with _restricted_session(
            scratch, scope, principal, isolation=isolation
        ) as sealer:
            seal_command = _seal_command(sealer, scope, window)
            s.seal_opening_window(
                sealer,
                scope=scope,
                command=seal_command,
                verifier=TrustedWindowVerifier(),
            )
            ready = Event()
            result = {}

            def import_loser():
                with _restricted_session(
                    scratch, scope, principal, isolation=isolation
                ) as session:
                    result["pid"] = session.scalar(text("SELECT pg_backend_pid()"))
                    assert get_opening_window_seal(session, scope=scope) is None
                    ready.set()
                    try:
                        s.import_opening_account(
                            session,
                            scope=scope,
                            command=fresh_command,
                            verifier=cash.TrustedVerifier(),
                        )
                        session.commit()
                        result["unexpected_success"] = True
                    except (DBAPIError, cash.BillingRuleViolation) as exc:
                        result["error"] = exc
                        session.rollback()

            thread = Thread(target=import_loser)
            thread.start()
            try:
                assert ready.wait(15)
                _wait_for_database_lock(scratch, result["pid"])
                with b._session(url, scope) as observer:
                    assert get_opening_window_seal(observer, scope=scope) is None
                    assert (
                        len(get_opening_window_admissions(observer, scope=scope)) == 1
                    )
                    assert (
                        cash._counts(observer, plane, fresh_account) == fresh_baseline
                    )
                if commit_seal:
                    sealer.commit()
                else:
                    sealer.rollback()
            finally:
                sealer.rollback()
                thread.join(20)
            assert not thread.is_alive() and "unexpected_success" not in result
            if commit_seal and isolation != "READ COMMITTED":
                assert isinstance(result.get("error"), DBAPIError)
                assert result["error"].orig.sqlstate == "40001"
            else:
                assert isinstance(result.get("error"), cash.BillingRuleViolation)
                assert result["error"].code == (
                    "billing.opening_window_sealed"
                    if commit_seal
                    else "billing.opening_window_manifest"
                )
            with b._session(url, scope) as observer:
                assert cash._counts(observer, plane, fresh_account) == fresh_baseline
                assert len(get_opening_window_admissions(observer, scope=scope)) == 1
                assert (
                    get_opening_window_seal(observer, scope=scope) is not None
                ) == commit_seal
