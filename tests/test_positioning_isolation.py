"""PostgreSQL RLS canaries for the tenant-only positioning module."""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
POSITIONING_VERSIONS = (
    REPO_ROOT / "packages/dotmac-positioning/src/dotmac_positioning/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the RLS canary needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_scratch() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"positioning_rls_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {POSITIONING_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_units(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant_id, slug in ((tenant_a, "position-a"), (tenant_b, "position-b")):
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant_id, "slug": slug, "name": slug.title()},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_pos.tracked_units "
                        "(id, tenant_id, is_active) VALUES (:id, :tenant, true)"
                    ),
                    {"id": uuid.uuid4(), "tenant": tenant_id},
                )
    finally:
        engine.dispose()
    return tenant_a, tenant_b


def test_all_declared_tables_have_forced_rls_and_one_tenant_policy(
    migrated_scratch: tuple[str, str],
) -> None:
    from dotmac_positioning.models import TENANT_TABLES

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            for table_name in TENANT_TABLES:
                enabled, forced = conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = CAST(:table_name AS regclass)"
                    ),
                    {"table_name": f"mod_pos.{table_name}"},
                ).one()
                assert enabled
                assert forced
                policies = conn.execute(
                    text(
                        "SELECT count(*) FROM pg_policies "
                        "WHERE schemaname = 'mod_pos' AND tablename = :table_name"
                    ),
                    {"table_name": table_name},
                ).scalar_one()
                assert policies == 1
    finally:
        engine.dispose()


def test_schema_passes_the_kernel_live_catalog_contract(
    migrated_scratch: tuple[str, str],
) -> None:
    from dotmac_kernel.migrations.catalog import audit_live_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry
    from dotmac_positioning.manifest import module

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            assert (
                audit_live_schemas(conn, NamespaceRegistry.from_manifests((module,)))
                == ()
            )
    finally:
        engine.dispose()


def test_online_role_sees_only_the_bound_tenants_units(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_units(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            visible = list(
                conn.execute(
                    text("SELECT tenant_id FROM mod_pos.tracked_units")
                ).scalars()
            )
            assert visible == [tenant_a]
            assert tenant_b not in visible
    finally:
        engine.dispose()


def test_online_role_cannot_insert_for_another_tenant(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_units(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            with pytest.raises(DBAPIError):
                conn.execute(
                    text(
                        "INSERT INTO mod_pos.tracked_units "
                        "(id, tenant_id, is_active) VALUES (:id, :tenant, true)"
                    ),
                    {"id": uuid.uuid4(), "tenant": tenant_b},
                )
    finally:
        engine.dispose()


def test_online_role_without_tenant_context_sees_no_units(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    _seed_units(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM mod_pos.tracked_units")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_concurrent_identical_collection_grants_replay_one_row(
    migrated_scratch: tuple[str, str],
) -> None:
    """The unique constraint arbitrates a real two-session replay race."""

    from dotmac_kernel.cache import TenantScope
    from dotmac_positioning import CollectionGrantInput, grant_collection

    admin_url, app_user_url = migrated_scratch
    tenant_id = uuid.uuid4()
    tracked_unit_id = uuid.uuid4()
    grant_id = uuid.uuid4()
    now = datetime.now(UTC)

    admin_engine = create_engine(admin_url)
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name) VALUES (:id, :slug, :name)"
            ),
            {
                "id": tenant_id,
                "slug": f"position-race-{tenant_id.hex[:12]}",
                "name": "Positioning replay race",
            },
        )
        conn.execute(
            text(
                "INSERT INTO mod_pos.tracked_units "
                "(id, tenant_id, is_active) VALUES (:id, :tenant, true)"
            ),
            {"id": tracked_unit_id, "tenant": tenant_id},
        )

    engine = create_engine(app_user_url)
    barrier = threading.Barrier(2, timeout=10)
    marker = f"positioning-grant-race-{grant_id}"

    def synchronise_initial_lookup(
        conn, cursor, statement, parameters, context, executemany
    ) -> None:
        del cursor, parameters, context, executemany
        if (
            statement.lstrip().startswith("SELECT")
            and "mod_pos.collection_grants" in statement
            and not conn.info.get(marker)
        ):
            conn.info[marker] = True
            barrier.wait()

    event.listen(engine, "after_cursor_execute", synchronise_initial_lookup)

    def worker() -> uuid.UUID:
        with Session(engine) as db:
            db.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_id)},
            )
            row = grant_collection(
                db,
                scope=TenantScope(tenant_id),
                grant=CollectionGrantInput(
                    grant_id=grant_id,
                    tracked_unit_id=tracked_unit_id,
                    purpose="service_delivery",
                    granted_at=now,
                    expires_at=now + timedelta(hours=1),
                ),
            )
            row_id = row.id
            db.commit()
            return row_id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            row_ids = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(engine, "after_cursor_execute", synchronise_initial_lookup)
        engine.dispose()

    assert row_ids == [grant_id, grant_id]
    with admin_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM mod_pos.collection_grants "
                "WHERE tenant_id = :tenant AND id = :id"
            ),
            {"tenant": tenant_id, "id": grant_id},
        ).scalar_one()
    admin_engine.dispose()
    assert count == 1


def test_concurrent_identical_geofences_replay_one_row(
    migrated_scratch: tuple[str, str],
) -> None:
    """An idempotent geofence identity also survives a two-session race."""

    from dotmac_kernel.cache import TenantScope
    from dotmac_positioning import CircleFence, create_geofence

    admin_url, app_user_url = migrated_scratch
    tenant_id = uuid.uuid4()
    geofence_id = uuid.uuid4()
    now = datetime.now(UTC)

    admin_engine = create_engine(admin_url)
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name) VALUES (:id, :slug, :name)"
            ),
            {
                "id": tenant_id,
                "slug": f"fence-race-{tenant_id.hex[:12]}",
                "name": "Geofence replay race",
            },
        )

    engine = create_engine(app_user_url)
    barrier = threading.Barrier(2, timeout=10)
    marker = f"positioning-fence-race-{geofence_id}"

    def synchronise_initial_lookup(
        conn, cursor, statement, parameters, context, executemany
    ) -> None:
        del cursor, parameters, context, executemany
        if (
            statement.lstrip().startswith("SELECT")
            and "mod_pos.geofences" in statement
            and not conn.info.get(marker)
        ):
            conn.info[marker] = True
            barrier.wait()

    event.listen(engine, "after_cursor_execute", synchronise_initial_lookup)

    def worker() -> uuid.UUID:
        with Session(engine) as db:
            db.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_id)},
            )
            row = create_geofence(
                db,
                scope=TenantScope(tenant_id),
                geofence_id=geofence_id,
                shape=CircleFence(latitude=9.071, longitude=7.451, radius_m=100),
                now=now,
            )
            row_id = row.id
            db.commit()
            return row_id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker) for _ in range(2)]
            row_ids = [future.result(timeout=20) for future in futures]
    finally:
        event.remove(engine, "after_cursor_execute", synchronise_initial_lookup)
        engine.dispose()

    assert row_ids == [geofence_id, geofence_id]
    with admin_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM mod_pos.geofences "
                "WHERE tenant_id = :tenant AND id = :id"
            ),
            {"tenant": tenant_id, "id": geofence_id},
        ).scalar_one()
    admin_engine.dispose()
    assert count == 1
