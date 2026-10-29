"""dotmac-ipam 0.2.0a1 on real PostgreSQL (CI integration job).

Covers what SQLite cannot: the ip_0001 <-> ip_0002 round trip, the fail-closed
template-row refusal under forced RLS, concurrent allocation without deadlock,
partial unique indexes, append-only utilization snapshots and the DML guard.

Skips only when no TEST_*DATABASE_URL is configured (never in CI).
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy import create_engine, insert, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from alembic.migration import MigrationContext
from alembic.operations import Operations
from tests.test_network_suite_isolation import (
    ASSEMBLY_VERSIONS,
    KERNEL_VERSIONS,
    REPO_ROOT,
    _superuser_url,
    _url_for,
)

IPAM_VERSIONS = REPO_ROOT / "packages/dotmac-ipam/src/dotmac_ipam/migrations/versions"
SITE = uuid.UUID("00000000-0000-4000-8000-0000000000a1")


@dataclass(frozen=True)
class Db:
    name: str
    superuser_url: str
    admin_url: str
    app_url: str
    config: Any  # alembic Config with the ipam lineage


def _config(admin_url: str, *, with_ipam: bool) -> Any:
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    locations = (
        KERNEL_VERSIONS,
        ASSEMBLY_VERSIONS,
        *((IPAM_VERSIONS,) if with_ipam else ()),
    )
    config.set_main_option("version_locations", " ".join(str(p) for p in locations))
    return config


@contextmanager
def _database(monkeypatch: pytest.MonkeyPatch) -> Iterator[Db]:
    """Fresh database with kernel + assembly at head and NO ipam revision."""
    from alembic import command

    superuser = _superuser_url()
    name = f"ipam_it_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    db_super = _url_for(superuser, name)
    setup = create_engine(db_super, isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin_url = _url_for(superuser, name, user="app_admin")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
    try:
        command.upgrade(_config(admin_url, with_ipam=False), "heads")
        yield Db(
            name,
            db_super,
            admin_url,
            _url_for(superuser, name, user="app_user"),
            _config(admin_url, with_ipam=True),
        )
    finally:
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _tables(url: str) -> set[str]:
    engine = create_engine(url)
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'mod_ipam'")
        ).scalars()
        found = set(rows)
    engine.dispose()
    return found


def _tenant(url: str) -> uuid.UUID:
    tenant = uuid.uuid4()
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO public.tenants (id, slug, name) VALUES (:i, :s, :n)"),
            {"i": tenant, "s": f"ipam-{tenant.hex[:8]}", "n": "IPAM IT"},
        )
    engine.dispose()
    return tenant


TEMPLATE = {
    "address_spaces",
    "pools",
    "addresses",
    "assignments",
    "utilization_snapshots",
}
EXTRACTED = {
    "ip_pools",
    "ip_blocks",
    "ipv4_addresses",
    "ipv6_addresses",
    "ip_assignments",
    "subscriber_additional_routes",
    "ipv6_delegated_prefixes",
    "ip_pool_utilization_snapshots",
}


def test_round_trip_ip_0001_ip_0002_ip_0001_ip_0002(monkeypatch) -> None:
    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0001_ipam")
        assert _tables(db.admin_url) == TEMPLATE | {"ipam_events"}
        command.upgrade(db.config, "ip_0002_extracted")
        assert _tables(db.admin_url) == EXTRACTED | {"ipam_events"}
        command.downgrade(db.config, "ip_0001_ipam")
        assert _tables(db.admin_url) == TEMPLATE | {"ipam_events"}
        command.upgrade(db.config, "ip_0002_extracted")
        assert _tables(db.admin_url) == EXTRACTED | {"ipam_events"}


def _seed_template_row(db: Db) -> None:
    tenant = _tenant(db.superuser_url)
    engine = create_engine(db.superuser_url)  # superuser: bypasses RLS to plant it
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mod_ipam.address_spaces "
                "(id, tenant_id, code, name, family, prefix) VALUES "
                "(:i, :t, 'tpl', 'Template', 'ipv4', '192.0.2.0/24')"
            ),
            {"i": uuid.uuid4(), "t": tenant},
        )
    engine.dispose()


def test_a_seeded_template_row_refuses_the_upgrade_and_changes_nothing(
    monkeypatch,
) -> None:
    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0001_ipam")
        _seed_template_row(db)
        with pytest.raises(Exception, match="holds data"):
            command.upgrade(db.config, "ip_0002_extracted")
        assert _tables(db.admin_url) == TEMPLATE | {"ipam_events"}


def test_the_refusal_fails_closed_for_a_role_that_cannot_bypass_rls(
    monkeypatch,
) -> None:
    """Forced RLS hides rows from a non-BYPASSRLS role, so a plain EXISTS would
    report an empty table (fail open). With ``row_security = off`` the check
    must raise instead."""
    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0001_ipam")
        _seed_template_row(db)
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ip_0002_extracted", IPAM_VERSIONS / "ip_0002_extracted.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # A NOBYPASSRLS role that DOES hold UPDATE/DELETE on the template tables,
        # so the LOCK succeeds and the refusal is decided by row_security alone.
        role = f"ipam_nb_{uuid.uuid4().hex[:8]}"
        engine = create_engine(db.superuser_url)
        with engine.connect() as connection:
            connection.execute(text(f"CREATE ROLE {role} LOGIN NOBYPASSRLS"))
            connection.execute(text(f"GRANT USAGE ON SCHEMA mod_ipam TO {role}"))
            connection.execute(
                text(
                    "GRANT SELECT, UPDATE, DELETE ON ALL TABLES IN SCHEMA mod_ipam "
                    f"TO {role}"
                )
            )
            connection.commit()
        try:
            with engine.connect() as connection:
                trans = connection.begin()
                connection.execute(text(f"SET LOCAL ROLE {role}"))
                ctx = MigrationContext.configure(connection)
                with (
                    Operations.context(ctx),
                    pytest.raises(DBAPIError, match="row-level security"),
                ):
                    module._refuse_if_template_rows("upgrade")
                trans.rollback()
        finally:
            with engine.connect() as connection:
                connection.execute(text(f"DROP OWNED BY {role}"))
                connection.execute(text(f"DROP ROLE {role}"))
                connection.commit()
            engine.dispose()


def _seed_estate(
    db: Db, tenant: uuid.UUID, pools: list[tuple[str, str]]
) -> list[uuid.UUID]:
    from dotmac_ipam import CreatePool, PoolPurpose, create_pool

    ids: list[uuid.UUID] = []
    engine = create_engine(db.admin_url)
    with Session(engine) as session:
        for name, cidr in pools:
            snapshot = create_pool(
                session,
                tenant_id=tenant,
                command=CreatePool(
                    name=name, cidr=cidr, purpose=PoolPurpose.CUSTOMER, site_ref=SITE
                ),
            )
            ids.append(snapshot.id)
        session.commit()
    engine.dispose()
    return ids


def test_concurrent_allocations_get_distinct_addresses_without_deadlock(
    monkeypatch,
) -> None:
    from dotmac_ipam import AllocateAddress, allocate_address

    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0002_extracted")
        tenant = _tenant(db.superuser_url)
        p1, p2 = _seed_estate(
            db, tenant, [("one", "192.0.2.0/28"), ("two", "198.51.100.0/28")]
        )
        workers = 8
        barrier = threading.Barrier(workers)
        results: dict[int, str] = {}
        errors: list[BaseException] = []

        def run(index: int) -> None:
            pools = (p1, p2) if index % 2 == 0 else (p2, p1)  # opposite orders
            engine = create_engine(db.app_url)
            try:
                with Session(engine) as session:
                    session.execute(
                        text("SELECT set_config('app.current_tenant', :t, true)"),
                        {"t": str(tenant)},
                    )
                    session.execute(text("SET LOCAL statement_timeout = '20s'"))
                    barrier.wait(timeout=30)
                    snap = allocate_address(
                        session,
                        tenant_id=tenant,
                        command=AllocateAddress(
                            pool_ids=pools,
                            subscription_id=uuid.uuid4(),
                            subscriber_id=uuid.uuid4(),
                        ),
                    )
                    session.commit()
                    results[index] = snap.address
            except BaseException as exc:
                errors.append(exc)
            finally:
                engine.dispose()

        threads = [threading.Thread(target=run, args=(i,)) for i in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert not any(t.is_alive() for t in threads), "allocation deadlocked"
        assert not errors, errors
        assert len(results) == workers
        assert len(set(results.values())) == workers


def test_partial_unique_indexes_are_enforced(monkeypatch) -> None:
    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0002_extracted")
        tenant = _tenant(db.superuser_url)
        (pool,) = _seed_estate(db, tenant, [("p", "192.0.2.0/24")])
        engine = create_engine(db.admin_url)
        addr_a, addr_b, sub = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        with engine.begin() as c:
            for ident, ip in ((addr_a, "192.0.2.10"), (addr_b, "192.0.2.11")):
                c.execute(
                    text(
                        "INSERT INTO mod_ipam.ipv4_addresses "
                        "(id, tenant_id, address, pool_id, is_reserved) "
                        "VALUES (:i, :t, :a, :p, false)"
                    ),
                    {"i": ident, "t": tenant, "a": ip, "p": pool},
                )

        def assign(address: uuid.UUID, *, active: bool, primary: bool, who: uuid.UUID):
            sql = text(
                "INSERT INTO mod_ipam.ip_assignments (id, tenant_id, subscription_id, "
                "ip_version, ipv4_address_id, is_active, is_primary) VALUES "
                "(:i, :t, :s, 'ipv4', :a, :act, :pri)"
            )
            with engine.begin() as c:
                c.execute(
                    sql,
                    {
                        "i": uuid.uuid4(),
                        "t": tenant,
                        "s": who,
                        "a": address,
                        "act": active,
                        "pri": primary,
                    },
                )

        assign(addr_a, active=True, primary=True, who=sub)
        with pytest.raises(IntegrityError):  # one ACTIVE row per address
            assign(addr_a, active=True, primary=False, who=uuid.uuid4())
        with pytest.raises(IntegrityError):  # one active PRIMARY per service
            assign(addr_b, active=True, primary=True, who=sub)
        assign(addr_a, active=False, primary=True, who=uuid.uuid4())  # history ok
        assign(addr_b, active=True, primary=False, who=sub)
        engine.dispose()


def test_utilization_snapshots_are_append_only(monkeypatch) -> None:
    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0002_extracted")
        tenant = _tenant(db.superuser_url)
        (pool,) = _seed_estate(db, tenant, [("p", "192.0.2.0/24")])
        snap = uuid.uuid4()
        admin = create_engine(db.admin_url)
        with admin.begin() as c:
            c.execute(
                text(
                    "INSERT INTO mod_ipam.ip_pool_utilization_snapshots "
                    "(id, tenant_id, pool_id, captured_at, total, used, reserved, "
                    "available, percent) "
                    "VALUES (:i, :t, :p, now(), 10, 1, 0, 9, 10)"
                ),
                {"i": snap, "t": tenant, "p": pool},
            )
        with pytest.raises(DBAPIError, match="append-only"):  # trigger (even for admin)
            with admin.begin() as c:
                c.execute(
                    text(
                        "UPDATE mod_ipam.ip_pool_utilization_snapshots SET used = 2 "
                        "WHERE id = :i"
                    ),
                    {"i": snap},
                )
        app = create_engine(db.app_url)
        with pytest.raises(DBAPIError, match="permission denied"):  # no UPDATE grant
            with app.begin() as c:
                c.execute(
                    text("SELECT set_config('app.current_tenant', :t, true)"),
                    {"t": str(tenant)},
                )
                c.execute(
                    text("UPDATE mod_ipam.ip_pool_utilization_snapshots SET used = 3")
                )
        with pytest.raises(DBAPIError):  # pool FK is RESTRICT
            with admin.begin() as c:
                c.execute(
                    text("DELETE FROM mod_ipam.ip_pools WHERE id = :p"), {"p": pool}
                )
        with admin.begin() as c:  # retention prune is still allowed
            c.execute(text("DELETE FROM mod_ipam.ip_pool_utilization_snapshots"))
        admin.dispose()
        app.dispose()


def test_bulk_dml_outside_a_command_is_refused_on_postgres(monkeypatch) -> None:
    from dotmac_ipam import IpamWriteOutsideCommand
    from dotmac_ipam.models import IPv4Address

    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0002_extracted")
        tenant = _tenant(db.superuser_url)
        _seed_estate(db, tenant, [("p", "192.0.2.0/24")])
        engine = create_engine(db.admin_url)
        with Session(engine) as session:
            with pytest.raises(IpamWriteOutsideCommand):
                session.execute(update(IPv4Address).values(notes="x"))
            with pytest.raises(IpamWriteOutsideCommand):
                session.execute(insert(IPv4Address).values(tenant_id=tenant))
            with pytest.raises(IpamWriteOutsideCommand):
                session.execute(insert(IPv4Address.__table__).values(tenant_id=tenant))
            session.rollback()
        engine.dispose()


def test_two_workers_allocating_for_one_service_get_one_assignment(
    monkeypatch,
) -> None:
    from dotmac_ipam import AllocateAddress, allocate_address

    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0002_extracted")
        tenant = _tenant(db.superuser_url)
        (pool,) = _seed_estate(db, tenant, [("p", "192.0.2.0/28")])
        subscription, subscriber = uuid.uuid4(), uuid.uuid4()
        barrier = threading.Barrier(2)
        results: list[str] = []
        errors: list[BaseException] = []

        def run() -> None:
            engine = create_engine(db.app_url)
            try:
                with Session(engine) as session:
                    session.execute(
                        text("SELECT set_config('app.current_tenant', :t, true)"),
                        {"t": str(tenant)},
                    )
                    barrier.wait(timeout=30)
                    snap = allocate_address(
                        session,
                        tenant_id=tenant,
                        command=AllocateAddress((pool,), subscription, subscriber),
                    )
                    session.commit()
                    results.append(snap.address)
            except BaseException as exc:
                errors.append(exc)
            finally:
                engine.dispose()

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert not errors, errors
        assert len(results) == 2 and results[0] == results[1]
        admin = create_engine(db.admin_url)
        with admin.connect() as c:
            count = c.execute(
                text(
                    "SELECT count(*) FROM mod_ipam.ip_assignments "
                    "WHERE subscription_id = :s AND is_active"
                ),
                {"s": subscription},
            ).scalar()
        admin.dispose()
        assert count == 1


@pytest.mark.parametrize("operation", ["assign", "assign_inferred", "repair"])
def test_explicit_assignment_and_reservation_lock_pool_before_address(
    monkeypatch, operation
) -> None:
    from dotmac_ipam import (
        ApplyRepair,
        AssignAddress,
        IpamConflict,
        RepairFacts,
        ReserveAddress,
        StalePreview,
        apply_repair,
        assign_address,
        preview_repair,
        reserve_address,
    )
    from dotmac_ipam.models import IPv4Address
    from sqlalchemy import event, select

    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0002_extracted")
        tenant = _tenant(db.superuser_url)
        (pool,) = _seed_estate(db, tenant, [("p", "192.0.2.0/28")])
        admin = create_engine(db.admin_url)
        # A released, materialised address is assignable and reservable.
        with admin.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO mod_ipam.ipv4_addresses "
                    "(id, tenant_id, pool_id, address, is_reserved) "
                    "VALUES (:i, :t, :p, '192.0.2.1', false)"
                ),
                {"i": uuid.uuid4(), "t": tenant, "p": pool},
            )
        admin.dispose()
        pool_held = threading.Event()
        assignment_waiting = threading.Event()
        errors: list[BaseException] = []
        outcomes: list[str] = []
        reservation_engine = create_engine(db.app_url)
        assignment_engine = create_engine(db.app_url)

        def pool_lock(statement: str) -> bool:
            return "mod_ipam.ip_pools" in statement and "FOR UPDATE" in statement

        @event.listens_for(reservation_engine, "after_cursor_execute")
        def hold_pool(conn, cursor, statement, parameters, context, executemany):
            if pool_lock(statement):
                pool_held.set()
                assert assignment_waiting.wait(15), "assignment never requested pool"

        @event.listens_for(assignment_engine, "before_cursor_execute")
        def attempting_pool(conn, cursor, statement, parameters, context, executemany):
            if pool_lock(statement):
                assignment_waiting.set()

        def run(reserving: bool) -> None:
            engine = reservation_engine if reserving else assignment_engine
            try:
                with Session(engine) as session:
                    session.execute(
                        text("SELECT set_config('app.current_tenant', :t, true)"),
                        {"t": str(tenant)},
                    )
                    session.execute(text("SET LOCAL statement_timeout = '20s'"))
                    if reserving:
                        reserve_address(
                            session,
                            tenant_id=tenant,
                            command=ReserveAddress(pool, "192.0.2.1"),
                        )
                        session.commit()
                        outcomes.append("reserved")
                    else:
                        assert pool_held.wait(15), "reservation never locked pool"
                        # Hold an identity-map copy from before the reservation.
                        address = session.scalar(select(IPv4Address))
                        assert address is not None and not address.is_reserved
                        subscription, subscriber = uuid.uuid4(), uuid.uuid4()
                        if operation == "repair":
                            facts = RepairFacts()
                            preview = preview_repair(
                                session,
                                tenant_id=tenant,
                                subscription_id=subscription,
                                subscriber_id=subscriber,
                                desired_address_id=address.id,
                                facts=facts,
                            )
                            assert preview.applicable
                            with pytest.raises(StalePreview):
                                apply_repair(
                                    session,
                                    tenant_id=tenant,
                                    command=ApplyRepair(
                                        subscription_id=subscription,
                                        subscriber_id=subscriber,
                                        desired_address_id=address.id,
                                        deactivate_assignment_ids=(),
                                        preview_fingerprint=preview.fingerprint,
                                        idempotency_key="concurrent-reservation",
                                        facts=facts,
                                    ),
                                )
                        else:
                            with pytest.raises(IpamConflict, match="reserved or held"):
                                assign_address(
                                    session,
                                    tenant_id=tenant,
                                    command=AssignAddress(
                                        address="192.0.2.1",
                                        pool_id=None
                                        if operation == "assign_inferred"
                                        else pool,
                                        subscription_id=subscription,
                                        subscriber_id=subscriber,
                                    ),
                                )
                        outcomes.append("assignment refused")
            except BaseException as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=run, args=(value,)) for value in (True, False)
        ]
        try:
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=45)
            assert not any(t.is_alive() for t in threads), "pool/address deadlock"
            assert not errors, errors
            assert sorted(outcomes) == ["assignment refused", "reserved"]
        finally:
            reservation_engine.dispose()
            assignment_engine.dispose()


def test_duplicate_pool_rename_keeps_outer_work_and_tenant_context(monkeypatch) -> None:
    from dotmac_ipam import IpamConflict, UpdatePool, update_pool

    from alembic import command

    with _database(monkeypatch) as db:
        command.upgrade(db.config, "ip_0002_extracted")
        tenant = _tenant(db.superuser_url)
        first, _second = _seed_estate(
            db, tenant, [("first", "192.0.2.0/28"), ("second", "198.51.100.0/28")]
        )
        engine = create_engine(db.app_url)
        marker = uuid.uuid4()
        with Session(engine) as session:
            session.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(tenant)},
            )
            # Caller-owned work in the same transaction, outside module tables.
            session.execute(text("CREATE TEMP TABLE caller_work (id uuid, name text)"))
            session.execute(
                text("INSERT INTO caller_work VALUES (:i, 'outer work')"),
                {"i": marker},
            )
            with pytest.raises(IpamConflict, match="pool name already exists"):
                update_pool(
                    session, tenant_id=tenant, command=UpdatePool(first, name="second")
                )
            assert session.is_active
            assert session.scalar(
                text("SELECT current_setting('app.current_tenant')")
            ) == str(tenant)
            assert (
                session.scalar(
                    text("SELECT name FROM caller_work WHERE id = :i"), {"i": marker}
                )
                == "outer work"
            )
            update_pool(
                session,
                tenant_id=tenant,
                command=UpdatePool(first, notes="after conflict"),
            )
            session.commit()
        engine.dispose()
