"""Replacement ownership proofs. Execute only in Git-hosted PostgreSQL CI."""

from dataclasses import replace
from datetime import date

import pytest
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_numbering import (
    CollisionDomain,
    NumberCollision,
    NumberingError,
    SeriesConfiguration,
    allocate,
    configure_series,
    create_series,
    enroll_series,
    read_period_state,
    read_series,
    reserve_number,
)
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

# Reuse the migrated, separate-role, real-connection harness rather than
# inventing a second transaction or database authority for these canaries.
from tests.test_numbering_isolation import _make_tenants, _session
from tests.test_numbering_isolation import scratch as _scratch

scratch = _scratch

DAY = date(2026, 1, 15)


def _create(session, scope, code="invoice", *, partitioned=False):
    return create_series(
        session,
        scope=scope,
        configuration=SeriesConfiguration(series_code=code, prefix="INV"),
        domain=CollisionDomain(code="ar.invoice", partitioned=partitioned),
    )


def _allocate(session, scope, code="invoice", key="first", document="doc:1", **kw):
    return allocate(
        session,
        scope=scope,
        series_code=code,
        reference_date=DAY,
        idempotency_key=key,
        document_reference=document,
        **kw,
    )


def test_strict_identity_is_frozen_before_any_counter_exists(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        original = read_series(session, scope=scope, series_code="invoice")
        with pytest.raises(NumberingError):
            configure_series(
                session,
                scope=scope,
                configuration=replace(original.configuration, start_value=8),
            )
        session.commit()
    with _session(user, tenant) as session, pytest.raises(DBAPIError):
        session.execute(text("UPDATE mod_numbering.number_series SET start_value=8"))


def test_cross_replacement_collision_rolls_back_counter_and_replay_ledger(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        _create(session, scope, "credit")
        _allocate(session, scope)
        session.commit()
    with _session(user, tenant) as session:
        with pytest.raises(NumberCollision):
            _allocate(session, scope, "credit", "second", "doc:2")
        assert (
            read_period_state(
                session, scope=scope, series_code="credit", reference_date=DAY
            ).next_value
            == 1
        )
        assert (
            session.execute(
                text("SELECT count(*) FROM mod_numbering.number_claims")
            ).scalar()
            == 1
        )


def test_historical_exact_strings_preserve_provenance_without_receipts(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        kwargs = {
            "scope": scope,
            "series_code": "invoice",
            "formatted_number": "legacy?unknown",
            "document_reference": "document:old",
            "provenance": "census:2026:row:19",
            "origin": "historical",
            "idempotency_key": "census:19",
        }
        first = reserve_number(session, **kwargs)
        replay = reserve_number(session, **kwargs)
        assert replay.replayed and replay.claim_id == first.claim_id
        assert (
            session.execute(
                text("SELECT count(*) FROM mod_numbering.allocation_receipts")
            ).scalar()
            == 0
        )
        with pytest.raises(IdempotencyConflict):
            reserve_number(session, **{**kwargs, "provenance": "changed"})
        with pytest.raises(NumberCollision):
            reserve_number(
                session,
                **{
                    **kwargs,
                    "idempotency_key": "other",
                    "document_reference": "document:other",
                },
            )
        session.commit()


def test_partition_does_not_reseed_series_counter_and_caller_rollback_is_atomic(
    scratch,
):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope, partitioned=True)
        session.commit()
    with _session(user, tenant) as session:
        assert _allocate(session, scope, partition_key="account:a").value == 1
        session.rollback()
    with _session(user, tenant) as session:
        assert _allocate(session, scope, partition_key="account:b").value == 1
        assert (
            _allocate(
                session,
                scope,
                key="second",
                document="doc:2",
                partition_key="account:a",
            ).value
            == 2
        )
        session.commit()


def test_claims_are_isolated_and_platform_remains_reachable(scratch):
    admin, user, platform = scratch
    left, right = _make_tenants(admin)
    with _session(user, left) as session:
        scope = TenantScope(tenant_id=left)
        _create(session, scope)
        _allocate(session, scope)
        session.commit()
    with _session(user, right) as session:
        assert (
            session.execute(
                text("SELECT count(*) FROM mod_numbering.number_claims")
            ).scalar()
            == 0
        )
        _create(session, TenantScope(tenant_id=right))
        _allocate(session, TenantScope(tenant_id=right))
        session.commit()
    with _session(platform) as session:
        _create(session, PlatformScope())
        _allocate(session, PlatformScope())
        session.commit()
    with _session(user, left) as session, pytest.raises(DBAPIError):
        session.execute(text("SELECT * FROM mod_numbering.platform_number_claims"))


def test_enrollment_requires_exact_observed_configuration_and_preserves_counter(
    scratch,
):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        configuration = SeriesConfiguration(series_code="invoice", prefix="OLD")
        configure_series(session, scope=scope, configuration=configuration)
        allocate(
            session,
            scope=scope,
            series_code="invoice",
            reference_date=DAY,
            idempotency_key="legacy",
        )
        with pytest.raises(NumberingError):
            enroll_series(
                session,
                scope=scope,
                expected_configuration=replace(configuration, prefix="STALE"),
                domain=CollisionDomain(code="ar.invoice"),
            )
        enroll_series(
            session,
            scope=scope,
            expected_configuration=configuration,
            domain=CollisionDomain(code="ar.invoice"),
        )
        state = read_period_state(
            session, scope=scope, series_code="invoice", reference_date=DAY
        )
        assert state.next_value == 2
        assert state.last_allocation.formatted_number == "OLD-000001"
        session.commit()


def test_duplicate_document_cannot_enroll_a_second_number(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        arguments = {
            "scope": scope,
            "series_code": "invoice",
            "document_reference": "doc:one",
            "provenance": "import:row:1",
            "origin": "external",
        }
        reserve_number(
            session, **arguments, formatted_number="EXTERNAL-1", idempotency_key="one"
        )
        with pytest.raises(NumberCollision):
            reserve_number(
                session,
                **arguments,
                formatted_number="EXTERNAL-2",
                idempotency_key="two",
            )
        session.commit()


def test_read_facade_ignores_dirty_and_expired_identity_map_without_flushing(scratch):
    from dotmac_numbering.models import NumberSeries
    from sqlalchemy import select

    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        session.commit()
        row = session.execute(select(NumberSeries)).scalar_one()
        row.prefix = "UNFLUSHED"
        session.expire(row, ["start_value"])
        assert (
            read_series(
                session, scope=scope, series_code="invoice"
            ).configuration.prefix
            == "INV"
        )
        state = read_period_state(
            session, scope=scope, series_code="invoice", reference_date=DAY
        )
        assert state.next_value == 1 and not state.counter_exists
        assert row in session.dirty
        session.rollback()


def test_stale_identity_map_cannot_enroll_a_concurrently_changed_definition(scratch):
    from dotmac_numbering.models import NumberSeries
    from sqlalchemy import select

    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    old = SeriesConfiguration(series_code="invoice", prefix="OLD")
    with _session(user, tenant) as session:
        configure_series(session, scope=scope, configuration=old)
        session.commit()
    with _session(user, tenant) as stale:
        cached = stale.execute(select(NumberSeries)).scalar_one()
        assert cached.prefix == "OLD"
        with _session(user, tenant) as writer:
            configure_series(
                writer, scope=scope, configuration=replace(old, prefix="NEW")
            )
            writer.commit()
        with pytest.raises(NumberingError) as error:
            enroll_series(
                stale,
                scope=scope,
                expected_configuration=old,
                domain=CollisionDomain(code="ar.invoice"),
            )
        assert error.value.code == "numbering.stale_series_configuration"
        assert not read_series(stale, scope=scope, series_code="invoice").strict


def test_last_actual_receipt_follows_counter_order_not_transaction_start_time(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        session.commit()
    with _session(user, tenant) as older:
        older.execute(text("SELECT now()"))
        with _session(user, tenant) as newer:
            assert _allocate(newer, scope).value == 1
            newer.commit()
        assert _allocate(older, scope, key="second", document="doc:2").value == 2
        older.commit()
    with _session(user, tenant) as session:
        state = read_period_state(
            session, scope=scope, series_code="invoice", reference_date=DAY
        )
        assert state.last_allocation.value == 2


def _race_commands(user, tenant, commands):
    import threading

    barrier = threading.Barrier(len(commands))
    outcomes, errors = [], []

    def worker(command):
        try:
            with _session(user, tenant) as session:
                session.execute(text("SELECT 1"))
                barrier.wait(timeout=15)
                result = command(session)
                session.commit()
                outcomes.append(result)
        except BaseException as error:
            errors.append(error)

    threads = [threading.Thread(target=worker, args=(command,)) for command in commands]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)
    assert not any(thread.is_alive() for thread in threads)
    return outcomes, errors


def test_concurrent_replacement_codes_have_one_typed_collision_winner(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        _create(session, scope, "credit")
        session.commit()
    outcomes, errors = _race_commands(
        user,
        tenant,
        [
            lambda session: _allocate(session, scope),
            lambda session: _allocate(session, scope, "credit", "second", "doc:2"),
        ],
    )
    assert len(outcomes) == 1
    assert len(errors) == 1 and isinstance(errors[0], NumberCollision)


def _synchronize_kernel_misses(monkeypatch, key):
    import threading

    from dotmac_kernel import idempotency

    barrier = threading.Barrier(2)
    lookup = idempotency._lookup

    def synchronized(*args, **kwargs):
        result = lookup(*args, **kwargs)
        if result is None and kwargs.get("key") == key:
            barrier.wait(timeout=15)
        return result

    monkeypatch.setattr(idempotency, "_lookup", synchronized)


def test_concurrent_protected_allocation_identical_keys_replay(scratch, monkeypatch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        session.commit()

    def command(session):
        return _allocate(session, scope)

    _synchronize_kernel_misses(monkeypatch, "first")
    outcomes, errors = _race_commands(user, tenant, [command, command])
    assert not errors
    assert sorted(result.replayed for result in outcomes) == [False, True]
    assert {result.value for result in outcomes} == {1}


def test_concurrent_changed_provenance_uses_kernel_fingerprint_conflict(
    scratch, monkeypatch
):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        session.commit()

    def command(session, provenance):
        return reserve_number(
            session,
            scope=scope,
            series_code="invoice",
            formatted_number="legacy",
            document_reference="old:1",
            provenance=provenance,
            origin="historical",
            idempotency_key="same",
        )

    _synchronize_kernel_misses(monkeypatch, "same")
    outcomes, errors = _race_commands(
        user,
        tenant,
        [
            lambda session: command(session, "census:a"),
            lambda session: command(session, "census:b"),
        ],
    )
    assert len(outcomes) == 1
    assert len(errors) == 1 and isinstance(errors[0], IdempotencyConflict)


def test_concurrent_domain_creation_cannot_disagree_on_partition_policy(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    outcomes, errors = _race_commands(
        user,
        tenant,
        [
            lambda session: _create(session, scope, "invoice", partitioned=False),
            lambda session: _create(session, scope, "credit", partitioned=True),
        ],
    )
    assert len(outcomes) == 1
    assert len(errors) == 1 and isinstance(errors[0], NumberingError)
    assert errors[0].code == "numbering.domain_policy_conflict"


def test_legacy_fingerprint_replays_after_explicit_enrollment(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    configuration = SeriesConfiguration(series_code="invoice", prefix="INV")
    with _session(user, tenant) as session:
        configure_series(session, scope=scope, configuration=configuration)
        original = allocate(
            session,
            scope=scope,
            series_code="invoice",
            reference_date=DAY,
            idempotency_key="legacy",
        )
        enroll_series(
            session,
            scope=scope,
            expected_configuration=configuration,
            domain=CollisionDomain(code="ar.invoice"),
        )
        replay = allocate(
            session,
            scope=scope,
            series_code="invoice",
            reference_date=DAY,
            idempotency_key="legacy",
        )
        assert replay.replayed and replay.formatted_number == original.formatted_number
        with pytest.raises(NumberingError):
            allocate(
                session,
                scope=scope,
                series_code="invoice",
                reference_date=DAY,
                idempotency_key="fresh-without-document",
            )
        session.commit()


def test_cached_legacy_series_cannot_bypass_concurrent_enrollment(scratch):
    from dotmac_numbering.models import NumberSeries
    from sqlalchemy import select

    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    configuration = SeriesConfiguration(series_code="invoice", prefix="INV")
    with _session(user, tenant) as session:
        configure_series(session, scope=scope, configuration=configuration)
        session.commit()
    with _session(user, tenant) as stale:
        cached = stale.execute(select(NumberSeries)).scalar_one()
        assert cached.domain_code is None
        with _session(user, tenant) as writer:
            enroll_series(
                writer,
                scope=scope,
                expected_configuration=configuration,
                domain=CollisionDomain(code="ar.invoice"),
            )
            writer.commit()
        with pytest.raises(NumberingError) as error:
            allocate(
                stale,
                scope=scope,
                series_code="invoice",
                reference_date=DAY,
                idempotency_key="unenrolled-call",
            )
        assert error.value.code == "numbering.invalid_document_reference"
        assert (
            read_period_state(
                stale, scope=scope, series_code="invoice", reference_date=DAY
            ).next_value
            == 1
        )


def test_import_and_replacement_allocation_share_one_collision_claim(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        _create(session, scope, "credit")
        session.commit()
    outcomes, errors = _race_commands(
        user,
        tenant,
        [
            lambda session: reserve_number(
                session,
                scope=scope,
                series_code="invoice",
                formatted_number="INV-000001",
                document_reference="retained:voided",
                provenance="import:19",
                origin="historical",
                idempotency_key="import:19",
            ),
            lambda session: _allocate(session, scope, "credit", "allocation", "new:1"),
        ],
    )
    assert len(outcomes) == 1
    assert len(errors) == 1 and isinstance(errors[0], NumberCollision)


def test_hostile_without_domain_unique_guard_replacement_reuses_a_string(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        _create(session, scope, "credit")
        first = _allocate(session, scope)
        session.commit()
    with _session(admin) as administrator:
        administrator.execute(
            text(
                "ALTER TABLE mod_numbering.number_claims "
                "DROP CONSTRAINT uq_number_claims_rendered"
            )
        )
        administrator.commit()
    with _session(user, tenant) as session:
        duplicate = _allocate(session, scope, "credit", "second", "doc:2")
        assert duplicate.formatted_number == first.formatted_number
        session.commit()


@pytest.mark.parametrize("table", ["number_domains", "number_claims"])
def test_new_tenant_evidence_is_append_only_even_for_owner_role(scratch, table):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    with _session(user, tenant) as session:
        _create(session, scope)
        _allocate(session, scope)
        session.commit()
    # The admin role is covered by the trigger, rather than by online-role grants.
    with _session(admin, tenant) as administrator, pytest.raises(DBAPIError):
        administrator.execute(text(f"DELETE FROM mod_numbering.{table}"))  # noqa: S608


def test_same_document_and_exact_provenance_can_retry_after_ledger_retention(scratch):
    admin, user, _ = scratch
    (tenant,) = _make_tenants(admin, 1)
    scope = TenantScope(tenant_id=tenant)
    arguments = {
        "scope": scope,
        "series_code": "invoice",
        "formatted_number": "external-19",
        "document_reference": "document:19",
        "provenance": "import:19",
        "origin": "external",
    }
    with _session(user, tenant) as session:
        _create(session, scope)
        first = reserve_number(session, **arguments, idempotency_key="initial")
        again = reserve_number(
            session, **arguments, idempotency_key="later-reconciliation"
        )
        assert again.claim_id == first.claim_id
        assert (
            session.execute(
                text("SELECT count(*) FROM mod_numbering.number_claims")
            ).scalar()
            == 1
        )
        session.commit()
