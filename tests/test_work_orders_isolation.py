"""Postgres RLS canaries for tenant-scoped physical work execution."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_work_orders import TENANT_TABLES, module
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
WORK_ORDER_VERSIONS = (
    REPO_ROOT / "packages/dotmac-work-orders/src/dotmac_work_orders/migrations/versions"
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
    name = f"workorders_rls_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
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
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {WORK_ORDER_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :n AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _insert_work_order(conn, tenant_id: uuid.UUID, public_id: str) -> uuid.UUID:  # type: ignore[no-untyped-def]
    row_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_workorders.work_orders ("
            "id, tenant_id, public_id, title, status, priority"
            ") VALUES ("
            ":id, :tenant, :public_id, 'Install service', 'scheduled', 'normal'"
            ")"
        ),
        {
            "id": row_id,
            "tenant": tenant_id,
            "public_id": public_id,
        },
    )
    return row_id


def _seed_two_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant_id, "slug": slug, "name": slug.title()},
                )
                _insert_work_order(conn, tenant_id, f"WO-{slug}")
    finally:
        engine.dispose()
    return tenant_a, tenant_b


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_every_table_has_forced_rls_and_a_tenant_policy(
    migrated_scratch: tuple[str, str], table: str
) -> None:
    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            enabled, forced = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:table_name AS regclass)"
                ),
                {"table_name": f"mod_workorders.{table}"},
            ).one()
            assert enabled
            assert forced
            policies = list(
                conn.execute(
                    text(
                        "SELECT policyname FROM pg_policies "
                        "WHERE schemaname = 'mod_workorders' AND tablename = :table"
                    ),
                    {"table": table},
                ).scalars()
            )
            assert f"{table}_tenant_isolation" in policies
    finally:
        engine.dispose()


def test_schema_passes_the_kernel_live_catalog_contract(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            assert audit_live_schemas(conn, registry) == ()
    finally:
        engine.dispose()


def test_online_role_sees_only_the_bound_tenants_work(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            visible = list(
                conn.execute(
                    text("SELECT tenant_id FROM mod_workorders.work_orders")
                ).scalars()
            )
            assert visible == [tenant_a]
            assert tenant_b not in visible
    finally:
        engine.dispose()


def test_online_role_without_context_sees_no_work(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            assert (
                conn.execute(
                    text("SELECT count(*) FROM mod_workorders.work_orders")
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


def test_online_role_cannot_write_another_tenants_work(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_two_tenants(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            with pytest.raises(DBAPIError):
                _insert_work_order(conn, tenant_b, "WO-cross-tenant")
    finally:
        engine.dispose()
