"""Hosted PostgreSQL proofs for immutable real-settlement source claims."""

from __future__ import annotations

# Interpolated identifiers are fixed Billing plane/table literals.
# ruff: noqa: S608
import importlib.util
from contextlib import contextmanager
from dataclasses import replace
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from dotmac_billing import models, service
from dotmac_billing.errors import BillingConflict, BillingRuleViolation
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.planes import (
    ModulePlane,
    ModulePlaneSelection,
    install_module_plane_selections,
    installed_module_plane_selections,
)
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests import test_billing_isolation as b
from tests import test_billing_non_cash_credit as runtime
from tests import test_billing_withholding_persistence as predecessor

REVISION = "bi_0006_cash_source_claim"


@pytest.fixture(scope="module")
def scratch():
    with b._scratch_database((ModulePlane.TENANT, ModulePlane.PLATFORM)) as urls:
        yield urls


def _tables(plane):
    return (
        (
            models.CashSourceClaim,
            models.ConfirmedSettlement,
            models.PostingGroup,
            b.OutboxEvent,
            b.IdempotencyRecord,
        )
        if plane == "tenant"
        else (
            models.PlatformCashSourceClaim,
            models.PlatformConfirmedSettlement,
            models.PlatformPostingGroup,
            b.PlatformOutboxEvent,
            b.PlatformIdempotencyRecord,
        )
    )


def _account(session, scope):
    return service.create_billing_account(
        session,
        scope=scope,
        external_account_ref=str(uuid4()),
        currency="NGN",
        minor_units=2,
    )


def _accept(session, scope, offered):
    return service.accept_settlement(
        session,
        scope=scope,
        command=offered,
        accepted_confirmation_evidence=frozenset({"provider_settled"}),
    )


def _seed(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = _account(session, scope)
        offered = b._settlement_command(
            account.id, scope=scope, source_key=str(uuid4())
        )
        settlement = _accept(session, scope, offered)
        settlement_id = settlement.id
        session.commit()
    return scope, url, offered, settlement_id


def test_claim_tenant_isolation_and_platform_role_boundary(scratch):
    scope, url, offered, settlement_id = _seed(scratch, "tenant")
    other = TenantScope(b._create_tenants(scratch[0], 1)[0])
    with b._session(url, other) as session:
        assert (
            session.scalar(select(func.count()).select_from(models.CashSourceClaim))
            == 0
        )
    with b._session(url, scope) as session:
        claim = session.execute(
            select(models.CashSourceClaim).where(
                models.CashSourceClaim.settlement_id == settlement_id
            )
        ).scalar_one()
        assert claim.tenant_id == scope.tenant_id
        assert claim.billing_account_id == offered.billing_account_id
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege('app_user', "
                "'mod_billing.platform_cash_source_claims', 'SELECT')"
            )
        )
        assert not connection.scalar(
            text(
                "SELECT has_table_privilege('platform_api', "
                "'mod_billing.cash_source_claims', 'SELECT')"
            )
        )
        assert connection.scalar(
            text(
                "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                "WHERE oid='mod_billing.cash_source_claims'::regclass"
            )
        )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_new_exact_replay_retains_bytes_and_one_claim(scratch, plane):
    scope, url, offered, settlement_id = _seed(scratch, plane)
    claim, settlement, group, _, ledger = _tables(plane)
    with b._session(url, scope) as session:
        before = session.execute(
            select(ledger.result, ledger.fingerprint).where(
                ledger.scope == "billing.settlement",
                ledger.key
                == f"{offered.source_system}:{offered.source_settlement_key}",
            )
        ).one()
        replay = _accept(session, scope, offered)
        assert replay.id == settlement_id
        assert replay.observed_at == offered.observed_at
        assert (
            session.scalar(
                select(func.count())
                .select_from(claim)
                .where(claim.settlement_id == settlement_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(settlement)
                .where(settlement.id == settlement_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(group)
                .where(group.source_ref == f"settlement:{settlement_id}")
            )
            == 1
        )
        after = session.execute(
            select(ledger.result, ledger.fingerprint).where(
                ledger.scope == "billing.settlement",
                ledger.key
                == f"{offered.source_system}:{offered.source_settlement_key}",
            )
        ).one()
        assert before == after
        with pytest.raises(BillingRuleViolation):
            _accept(session, scope, replace(offered, confirmation_evidence="untrusted"))
        session.commit()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_old_concatenated_alias_and_scalar_conflicts_are_not_repaired(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = _account(session, scope)
        token = uuid4().hex
        offered = replace(
            b._settlement_command(account.id, scope=scope, source_key="c"),
            source_system=f"{token}:b",
        )
        settlement_id = _accept(session, scope, offered).id
        session.commit()
        alias = replace(offered, source_system=token, source_settlement_key="b:c")
        other = _account(session, scope)
        for changed in (alias, replace(offered, billing_account_id=other.id)):
            with pytest.raises(IdempotencyConflict):
                _accept(session, scope, changed)
        with pytest.raises(BillingRuleViolation) as mismatch:
            _accept(session, scope, replace(offered, amount=b._money("120", "USD")))
        assert mismatch.value.code == "billing.money_identity_mismatch"
        claim = _tables(plane)[0]
        assert (
            session.scalar(
                select(func.count())
                .select_from(claim)
                .where(claim.settlement_id == settlement_id)
            )
            == 1
        )
        session.rollback()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_claim_and_settlement_rollback_on_fault_after_claim(
    scratch, plane, monkeypatch
):
    scope, url = runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = _account(session, scope)
        session.commit()
        offered = b._settlement_command(
            account.id, scope=scope, source_key=str(uuid4())
        )
        real_post = service._post_group

        def fail_after_claim(*args, **kwargs):
            claim = _tables(plane)[0]
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(claim)
                    .where(
                        claim.source_system == offered.source_system,
                        claim.source_settlement_key == offered.source_settlement_key,
                    )
                )
                == 1
            )
            raise RuntimeError("fault after source claim")

        monkeypatch.setattr(service, "_post_group", fail_after_claim)
        with pytest.raises(RuntimeError, match="fault after source claim"):
            _accept(session, scope, offered)
        for table in _tables(plane)[:2]:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(table)
                    .where(
                        table.source_system == offered.source_system,
                        table.source_settlement_key == offered.source_settlement_key,
                    )
                )
                == 0
            )
        monkeypatch.setattr(service, "_post_group", real_post)
        settlement_id = _accept(session, scope, offered).id
        session.rollback()
    with b._session(url, scope) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(_tables(plane)[0])
                .where(_tables(plane)[0].settlement_id == settlement_id)
            )
            == 0
        )


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_identical_race_has_one_claim_settlement_post_and_event(scratch, plane):
    scope, url = runtime._scope_url(scratch, plane)
    with b._session(url, scope) as session:
        account = _account(session, scope)
        account_id = account.id
        session.commit()
    offered = b._settlement_command(account_id, scope=scope, source_key=str(uuid4()))
    ready = Barrier(2)
    receipts, errors = [], []

    def contender():
        try:
            with b._session(url, scope) as session:
                ready.wait(timeout=20)
                receipts.append(_accept(session, scope, offered).id)
                session.commit()
        except Exception as exc:
            errors.append(exc)

    threads = [Thread(target=contender) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert not errors
    assert len(receipts) == 2 and receipts[0] == receipts[1]
    claim, settlement, group, event, _ = _tables(plane)
    with b._session(url, scope) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(claim)
                .where(claim.settlement_id == receipts[0])
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(settlement)
                .where(settlement.id == receipts[0])
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(group)
                .where(group.source_ref == f"settlement:{receipts[0]}")
            )
            == 1
        )
        rows = session.execute(
            select(event.payload).where(
                event.event_type == "billing.settlement.accepted.v1"
            )
        ).scalars()
        assert sum(row["settlement_id"] == str(receipts[0]) for row in rows) == 1


def _raw_rows(connection, plane, settlement_id):
    claim, settlement, *_ = _tables(plane)
    receipt = dict(
        connection.execute(
            select(settlement.__table__).where(settlement.id == settlement_id)
        )
        .mappings()
        .one()
    )
    proof = dict(
        connection.execute(
            # This helper also reads a genuine bi6 predecessor. Select its
            # original columns; opening_cash_lot_id is introduced only by bi7.
            select(
                *((claim.tenant_id,) if plane == "tenant" else ()),
                claim.id,
                claim.settlement_id,
                claim.billing_account_id,
                claim.source_system,
                claim.source_settlement_key,
                claim.currency,
                claim.minor_units,
                claim.recorded_at,
            ).where(claim.settlement_id == settlement_id)
        )
        .mappings()
        .one()
    )
    return receipt, proof


@pytest.mark.parametrize("plane", ["tenant", "platform"])
@pytest.mark.parametrize(
    "field",
    [
        "billing_account_id",
        "source_system",
        "source_settlement_key",
        "currency",
        "minor_units",
    ],
)
def test_direct_sql_claim_affinity_is_exact(scratch, plane, field):
    _, _, _, settlement_id = _seed(scratch, plane)
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        receipt, proof = _raw_rows(connection, plane, settlement_id)
        connection.rollback()
        receipt.update(id=uuid4(), source_settlement_key=str(uuid4()))
        proof.update(
            id=uuid4(),
            settlement_id=receipt["id"],
            source_settlement_key=receipt["source_settlement_key"],
        )
        proof[field] = (
            uuid4()
            if field == "billing_account_id"
            else 3
            if field == "minor_units"
            else "USD"
            if field == "currency"
            else f"wrong:{uuid4()}"
        )
        transaction = connection.begin()
        connection.execute(_tables(plane)[1].__table__.insert().values(**receipt))
        with pytest.raises(DBAPIError) as error:
            connection.execute(_tables(plane)[0].__table__.insert().values(**proof))
        assert error.value.orig.sqlstate == "23503"
        transaction.rollback()
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_direct_sql_uniqueness_immutability_and_deferred_missing_claim(scratch, plane):
    _, _, _, settlement_id = _seed(scratch, plane)
    claim, settlement, *_ = _tables(plane)
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        receipt, proof = _raw_rows(connection, plane, settlement_id)
        connection.rollback()
        for statement in (
            claim.__table__.update()
            .where(claim.id == proof["id"])
            .values(source_system=proof["source_system"]),
            claim.__table__.delete().where(claim.id == proof["id"]),
            claim.__table__.insert().values(**{**proof, "id": uuid4()}),
        ):
            transaction = connection.begin()
            with pytest.raises(DBAPIError):
                connection.execute(statement)
            transaction.rollback()
        receipt.update(id=uuid4(), source_settlement_key=str(uuid4()))
        transaction = connection.begin()
        connection.execute(settlement.__table__.insert().values(**receipt))
        assert (
            connection.scalar(
                select(func.count())
                .select_from(settlement)
                .where(settlement.id == receipt["id"])
            )
            == 1
        )
        with pytest.raises(DBAPIError) as error:
            transaction.commit()
        assert error.value.orig.sqlstate == "23514"
        connection.rollback()
        assert (
            connection.scalar(
                select(func.count())
                .select_from(settlement)
                .where(settlement.id == receipt["id"])
            )
            == 0
        )
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_genuine_bi0005_upgrade_preserves_all_history_and_backfills_exact_identity(
    plane,
):
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with b._scratch_database(
        (selection,), target_revision="bi_0005_non_cash_credit"
    ) as urls:
        scope, url = runtime._scope_url(urls, plane)
        # Use the predecessor operation body by temporarily omitting only the new
        # helper in this test's historical seed; no predecessor evidence is edited.
        from unittest.mock import patch

        with (
            patch.object(service, "_require_cash_source_unclaimed", return_value=None),
            patch.object(service, "_claim_cash_source", return_value=None),
            patch.object(service, "_require_cash_source_claim", return_value=None),
        ):
            with b._session(url, scope) as session:
                account = _account(session, scope)
                offered = replace(
                    b._settlement_command(
                        account.id, scope=scope, source_key="historical:key"
                    ),
                    source_system=f"historical:{uuid4()}",
                )
                settlement_id = _accept(session, scope, offered).id
                session.commit()
        engine = create_engine(urls[0])
        names = (*b.TENANT_TABLES,) if plane == "tenant" else b.PLATFORM_TABLES
        # Preserve the complete bi5 history inventory. Later opening tables
        # do not exist at either endpoint of this bi5 -> bi6 upgrade canary.
        later_tables = {
            "opening_windows",
            "opening_window_admissions",
            "opening_window_seals",
            "cash_source_claims",
            "opening_import_receipts",
            "opening_cash_lots",
            "opening_cash_effects",
            "opening_receivable_bases",
            "opening_historical_discharge_claims",
            "receivable_exposure_facts_v2",
            "receivable_source_claims",
            "settlement_earmarks",
            "settlement_earmark_targets",
            "settlement_earmark_effects",
        }
        names = tuple(
            name for name in names if name.removeprefix("platform_") not in later_tables
        )
        with engine.connect() as connection:
            before = {
                name: connection.execute(
                    text(f"SELECT to_jsonb(x) FROM mod_billing.{name} x ORDER BY id")
                )
                .scalars()
                .all()
                for name in names
            }
            ledger = _tables(plane)[4]
            retries = connection.execute(select(ledger.__table__)).mappings().all()
            event = _tables(plane)[3]
            events = connection.execute(select(event.__table__)).mappings().all()
        command.upgrade(predecessor._config((selection,)), REVISION)
        with engine.connect() as connection:
            after = {
                name: connection.execute(
                    text(f"SELECT to_jsonb(x) FROM mod_billing.{name} x ORDER BY id")
                )
                .scalars()
                .all()
                for name in names
            }
            assert before == after
            assert (
                retries == connection.execute(select(ledger.__table__)).mappings().all()
            )
            assert (
                events == connection.execute(select(event.__table__)).mappings().all()
            )
            receipt, proof = _raw_rows(connection, plane, settlement_id)
            for field in (
                "billing_account_id",
                "source_system",
                "source_settlement_key",
                "currency",
                "minor_units",
            ):
                assert receipt[field] == proof[field]
            assert proof["recorded_at"] > receipt["observed_at"]
        with b._session(url, scope) as session:
            assert _accept(session, scope, offered).id == settlement_id
            session.commit()
        engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_claim_survives_expired_retry_and_refuses_new_operation_without_outer_rollback(
    scratch, plane
):
    scope, url, offered, settlement_id = _seed(scratch, plane)
    claim, settlement, group, _, ledger = _tables(plane)
    engine = create_engine(scratch[0])
    with engine.begin() as connection:
        connection.execute(
            ledger.__table__.delete().where(
                ledger.scope == "billing.settlement",
                ledger.key
                == f"{offered.source_system}:{offered.source_settlement_key}",
            )
        )
    with b._session(url, scope) as session:
        pending = _account(session, scope)
        pending_id = pending.id
        with pytest.raises(BillingConflict) as error:
            _accept(session, scope, offered)
        assert error.value.code == "billing.cash_source_claimed"
        account = (
            models.BillingAccount
            if plane == "tenant"
            else models.PlatformBillingAccount
        )
        assert session.get(account, pending_id) is not None
        assert (
            session.scalar(
                select(func.count())
                .select_from(claim)
                .where(claim.settlement_id == settlement_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(settlement)
                .where(settlement.id == settlement_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(group)
                .where(group.source_ref == f"settlement:{settlement_id}")
            )
            == 1
        )
        session.rollback()
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_missing_replay_claim_is_evidence_integrity_failure_not_read_repair(
    scratch, plane
):
    scope, url, offered, settlement_id = _seed(scratch, plane)
    claim = _tables(plane)[0]
    engine = create_engine(scratch[0])
    table = claim.__table__.name
    with engine.begin() as connection:
        _, proof = _raw_rows(connection, plane, settlement_id)
        # Deliberately corrupt only the isolated canary receipt as the migration
        # owner, to prove a replay cannot silently synthesize its missing claim.
        connection.execute(
            text(f"ALTER TABLE mod_billing.{table} DISABLE TRIGGER {table}_append_only")
        )
        connection.execute(claim.__table__.delete().where(claim.id == proof["id"]))
        connection.execute(
            text(f"ALTER TABLE mod_billing.{table} ENABLE TRIGGER {table}_append_only")
        )
    try:
        with b._session(url, scope) as session:
            with pytest.raises(BillingRuleViolation) as error:
                _accept(session, scope, offered)
            assert error.value.code == "billing.cash_source_claim_integrity"
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(claim)
                    .where(claim.settlement_id == settlement_id)
                )
                == 0
            )
            session.rollback()
    finally:
        with engine.begin() as connection:
            connection.execute(claim.__table__.insert().values(**proof))
        engine.dispose()


def test_direct_sql_claim_cannot_cross_tenant_and_online_insert_is_rls_bound(scratch):
    scope, url, _, settlement_id = _seed(scratch, "tenant")
    other = b._create_tenants(scratch[0], 1)[0]
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        receipt, proof = _raw_rows(connection, "tenant", settlement_id)
        connection.rollback()
        receipt.update(id=uuid4(), source_settlement_key=str(uuid4()))
        proof.update(
            id=uuid4(),
            tenant_id=other,
            settlement_id=receipt["id"],
            source_settlement_key=receipt["source_settlement_key"],
        )
        transaction = connection.begin()
        connection.execute(
            models.ConfirmedSettlement.__table__.insert().values(**receipt)
        )
        with pytest.raises(DBAPIError) as error:
            connection.execute(
                models.CashSourceClaim.__table__.insert().values(**proof)
            )
        assert error.value.orig.sqlstate == "23503"
        transaction.rollback()
    with b._session(url, scope) as session:
        with pytest.raises(DBAPIError) as error:
            session.execute(models.CashSourceClaim.__table__.insert().values(**proof))
        assert error.value.orig.sqlstate == "42501"
        session.rollback()
    engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_claim_natural_uniqueness_has_no_account_currency_or_version_escape(
    scratch, plane
):
    engine = create_engine(scratch[0])
    prefix = "platform_" if plane == "platform" else ""
    with engine.connect() as connection:
        definition = connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid=CAST(:table AS regclass) AND conname=:name"
            ),
            {
                "table": f"mod_billing.{prefix}cash_source_claims",
                "name": f"uq_{prefix}cash_claim_source",
            },
        )
        scope = "tenant_id, " if plane == "tenant" else ""
        assert definition == f"UNIQUE ({scope}source_system, source_settlement_key)"
    engine.dispose()


@contextmanager
def _migration(connection, planes):
    previous = installed_module_plane_selections()
    install_module_plane_selections(
        (ModulePlaneSelection(module="billing", planes=planes),)
    )
    spec = importlib.util.spec_from_file_location(
        "billing_cash_claim_migration_canary", b.BILLING_VERSIONS / f"{REVISION}.py"
    )
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    try:
        with Operations.context(MigrationContext.configure(connection)):
            yield migration
    finally:
        install_module_plane_selections(previous)


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_empty_claim_upgrade_downgrade_restores_settings_and_guard_catalog(plane):
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    with b._scratch_database(
        (selection,), target_revision="bi_0005_non_cash_credit"
    ) as urls:
        engine = create_engine(urls[0])
        with engine.begin() as connection:
            connection.execute(
                text(
                    "SET LOCAL lock_timeout='137ms'; "
                    "SET LOCAL statement_timeout='19s'; "
                    "SET LOCAL row_security=on"
                )
            )
            before = predecessor._catalog(connection)
            with _migration(connection, (selection,)) as migration:
                migration.upgrade()
                assert connection.scalar(text("SHOW lock_timeout")) == "137ms"
                assert connection.scalar(text("SHOW statement_timeout")) == "19s"
                assert connection.scalar(text("SHOW row_security")) == "on"
                migration.downgrade()
                assert connection.scalar(text("SHOW lock_timeout")) == "137ms"
                assert connection.scalar(text("SHOW statement_timeout")) == "19s"
                assert connection.scalar(text("SHOW row_security")) == "on"
            assert predecessor._catalog(connection) == before
        engine.dispose()


@pytest.mark.parametrize("plane", ["tenant", "platform"])
def test_populated_claim_downgrade_refuses_globally_with_guards_unchanged(
    scratch, plane
):
    _seed(scratch, plane)
    selection = ModulePlane.TENANT if plane == "tenant" else ModulePlane.PLATFORM
    engine = create_engine(scratch[0])
    with engine.connect() as connection:
        before = predecessor._catalog(connection)
        connection.rollback()
        transaction = connection.begin()
        with _migration(connection, (selection,)) as migration:
            with pytest.raises(RuntimeError, match="cash-source claims exist"):
                migration.downgrade()
        transaction.rollback()
        assert predecessor._catalog(connection) == before
    engine.dispose()
