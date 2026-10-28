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

        engine = create_engine(db.superuser_url)
        with engine.connect() as connection:
            trans = connection.begin()
            connection.execute(text("SET LOCAL ROLE app_user"))
            ctx = MigrationContext.configure(connection)
            with Operations.context(ctx), pytest.raises(DBAPIError):
                module._refuse_if_template_rows("upgrade")
            trans.rollback()
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
