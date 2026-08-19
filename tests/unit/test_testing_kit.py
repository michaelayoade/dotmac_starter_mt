"""Tests for the packaged test kit `dotmac_kernel.testing` (kernel Task 5).

Proves the kit a consumer assembly depends on actually works: the harness wiring
(engine/session/TestClient), the deterministic fakes, and the parametrized
provisioning-provider contract driven against `FakeProvisioningProvider`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dotmac_kernel import ProductAssemblySpec, create_app
from dotmac_kernel.models import Base
from dotmac_kernel.providers.provisioning import (
    ProvisioningProvider,
    ProvisioningRequest,
    ProvisioningStatus,
)
from dotmac_kernel.testing import (
    FakeClock,
    FakeProvisioningProvider,
    FakeSeeder,
    InMemoryRateLimitStore,
    assembly_test_client,
    check_provisioning_provider_contract,
    create_test_engine,
    fake_branding,
    isolated_session,
)
from sqlalchemy import Column, Integer, MetaData, Table, inspect, text


def _public_tables():
    """Kernel/host tables needed by the generic harness examples."""
    return tuple(table for table in Base.metadata.tables.values() if not table.schema)


# ── harness ──────────────────────────────────────────────────────────────────
def test_assembly_test_client_boots_and_serves_health() -> None:
    """The kit boots a real create_app assembly and overrides its DB deps onto
    an isolated session — the same path a consumer's integration-ish unit test
    takes."""
    engine = create_test_engine(tables=_public_tables())
    try:
        with isolated_session(engine) as session:
            app = create_app(ProductAssemblySpec(name="kit-test", modules=()))
            with assembly_test_client(app, session=session) as client:
                resp = client.get("/health")
                assert resp.status_code == 200
                assert resp.json() == {"status": "ok"}
            # overrides removed on exit — the app is left clean.
            assert app.dependency_overrides == {}
    finally:
        engine.dispose()


def test_isolated_session_rolls_back_between_uses() -> None:
    from dotmac_kernel.models import Tenant

    engine = create_test_engine(tables=_public_tables())
    try:
        with isolated_session(engine) as session:
            session.add(Tenant(slug="acme", name="Acme"))
            session.commit()  # even a commit is rolled back by the savepoint
            assert session.query(Tenant).count() == 1
        with isolated_session(engine) as session:
            assert session.query(Tenant).count() == 0
    finally:
        engine.dispose()


def test_module_schemas_are_explicit_not_inferred_from_imported_metadata() -> None:
    """An imported optional package is not an installed module."""
    engine = create_test_engine(tables=())
    try:
        with engine.connect() as connection:
            attached = connection.execute(text("PRAGMA database_list")).all()
        assert [row[1] for row in attached if row[1] != "temp"] == ["main"]
        assert "templates" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_an_explicit_module_schema_is_attached_and_created() -> None:
    selected = tuple(
        table
        for table in Base.metadata.tables.values()
        if table.schema == "mod_tstudio"
    )
    assert selected
    engine = create_test_engine(tables=selected)
    try:
        with engine.connect() as connection:
            attached = connection.execute(text("PRAGMA database_list")).all()
        assert [row[1] for row in attached if row[1] != "temp"] == [
            "main",
            "mod_tstudio",
        ]
        assert "templates" in inspect(engine).get_table_names(schema="mod_tstudio")
    finally:
        engine.dispose()


def test_an_explicit_table_slice_does_not_attach_unselected_schemas() -> None:
    selected = tuple(
        table
        for table in Base.metadata.tables.values()
        if table.schema == "mod_tstudio"
    )
    engine = create_test_engine(tables=selected)
    try:
        with engine.connect() as connection:
            attached = {
                row[1] for row in connection.execute(text("PRAGMA database_list")).all()
            }
        assert attached == {"main", "mod_tstudio"}
    finally:
        engine.dispose()


def test_too_many_explicit_module_schemas_are_refused_without_translation() -> None:
    metadata = MetaData()
    tables = tuple(
        Table(
            f"probe_{index}",
            metadata,
            Column("id", Integer, primary_key=True),
            schema=f"mod_probe_{index:02d}",
        )
        for index in range(11)
    )

    with pytest.raises(ValueError, match="split this unit composition"):
        create_test_engine(tables=tables)


# ── fakes ────────────────────────────────────────────────────────────────────
def test_harness_can_select_the_exact_tables_owned_by_its_test_assembly() -> None:
    """Unrelated package imports must not exhaust SQLite's attachment limit."""

    engine = create_test_engine(tables=_public_tables())
    try:
        with engine.connect() as connection:
            attached = {
                row[1]
                for row in connection.exec_driver_sql("PRAGMA database_list").all()
            }
        assert not {name for name in attached if name.startswith("mod_")}
    finally:
        engine.dispose()


def test_fake_clock_is_deterministic_and_advanceable() -> None:
    clock = FakeClock()
    start = clock.now()
    assert start.tzinfo is UTC
    clock.advance(60)
    assert (clock.now() - start).total_seconds() == 60
    clock.set(datetime(2030, 6, 1, tzinfo=UTC))
    assert clock.now() == datetime(2030, 6, 1, tzinfo=UTC)


def test_fake_seeder_records_and_can_fail() -> None:
    ok = FakeSeeder(name="feat-a")
    ok.hook()
    ok.hook()
    assert ok.calls == ["feat-a", "feat-a"]

    boom = FakeSeeder(name="feat-b", fail=True)
    with pytest.raises(RuntimeError):
        boom.hook()


def test_fake_branding_defaults_and_overrides() -> None:
    assert fake_branding()["name"] == "Test Brand"
    assert fake_branding(name="Acme")["name"] == "Acme"


def test_in_memory_rate_limit_store_is_the_shipped_store() -> None:
    from dotmac_kernel.middleware.rate_limit import MemoryStore

    assert InMemoryRateLimitStore is MemoryStore


# ── provisioning ─────────────────────────────────────────────────────────────
def test_fake_provisioning_provider_satisfies_the_contract() -> None:
    """The reusable contract, run against the fake — the same call a consumer
    makes against THEIR provider factory to prove protocol conformance."""
    check_provisioning_provider_contract(FakeProvisioningProvider)


def test_fake_provisioning_records_calls_and_conforms_to_protocol() -> None:
    provider = FakeProvisioningProvider()
    assert isinstance(provider, ProvisioningProvider)
    req = ProvisioningRequest(intent_id="i-9", spec={"n": 1})
    provider.plan(req)
    op = provider.apply(req).operation_id
    provider.observe(op)
    provider.cancel(op)
    assert [c[0] for c in provider.calls] == ["plan", "apply", "observe", "cancel"]


def test_fake_provisioning_partial_then_resume() -> None:
    provider = FakeProvisioningProvider(partial_first_apply=True)
    req = ProvisioningRequest(intent_id="i-1", spec={"n": 1})
    first = provider.apply(req)
    assert first.status is ProvisioningStatus.PARTIAL
    resume = ProvisioningRequest(
        intent_id="i-1", spec={"n": 1}, operation_id=first.operation_id
    )
    assert provider.apply(resume).status is ProvisioningStatus.SUCCEEDED


def test_fake_provisioning_cancel_is_terminal_and_idempotent() -> None:
    provider = FakeProvisioningProvider()
    req = ProvisioningRequest(intent_id="i-1", spec={"n": 1})
    op = provider.apply(req).operation_id
    assert provider.cancel(op).status is ProvisioningStatus.CANCELLED
    # A re-apply after cancel is a no-op that stays CANCELLED (terminal).
    assert provider.apply(req).status is ProvisioningStatus.CANCELLED
