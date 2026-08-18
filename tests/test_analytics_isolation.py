"""Real-PostgreSQL isolation and catalog canaries for ``dotmac-analytics``."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_analytics.manifest import module
from dotmac_analytics.models import APPEND_ONLY_MODELS, TENANT_TABLES
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
ANALYTICS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-analytics/src/dotmac_analytics/migrations/versions"
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
def migrated_scratch(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"analytics_rls_{uuid.uuid4().hex[:12]}"
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
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {ANALYTICS_VERSIONS}",
        )
        monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
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


def _seed_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    first, second = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant_id, slug in ((first, "first"), (second, "second")):
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant_id, "slug": slug, "name": slug.title()},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_analytics.metric_catalog_entries ("
                        "id, tenant_id, metric_code, schema_version, owner_code, "
                        "declaration_fingerprint, display_name, value_kind, unit_code, "
                        "granularities_json, dimensions_json"
                        ") VALUES (:row_id, :tenant, 'billing.revenue', 1, 'billing', "
                        ":digest, 'Revenue', 'money', 'money', '[\"day\"]', '[]')"
                    ),
                    {
                        "row_id": uuid.uuid4(),
                        "tenant": tenant_id,
                        "digest": "sha256:" + "a" * 64,
                    },
                )
    finally:
        engine.dispose()
    return first, second


def test_live_schema_contract_and_rls(migrated_scratch: tuple[str, str]) -> None:
    admin_url, _ = migrated_scratch
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            assert audit_live_schemas(conn, registry) == ()
            for table in TENANT_TABLES:
                enabled, forced = conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = CAST(:table_name AS regclass)"
                    ),
                    {"table_name": f"mod_analytics.{table}"},
                ).one()
                assert enabled and forced
    finally:
        engine.dispose()


def test_online_role_sees_only_bound_tenant_rows(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    first, second = _seed_tenants(admin_url)
    engine = create_engine(app_url)
    try:
        with engine.connect() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(first)},
            )
            visible = list(
                conn.execute(
                    text("SELECT tenant_id FROM mod_analytics.metric_catalog_entries")
                ).scalars()
            )
            assert visible == [first]
            assert second not in visible
    finally:
        engine.dispose()


def test_online_role_without_context_sees_nothing(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    _seed_tenants(admin_url)
    engine = create_engine(app_url)
    try:
        with engine.connect() as conn:
            assert conn.execute(
                text("SELECT count(*) FROM mod_analytics.metric_catalog_entries")
            ).scalar_one() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "table",
    tuple(model.__tablename__ for model in APPEND_ONLY_MODELS),
)
def test_every_evidence_table_has_trigger_and_read_insert_only_grants(
    migrated_scratch: tuple[str, str], table: str
) -> None:
    admin_url, _ = migrated_scratch
    relation = f"mod_analytics.{table}"
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            trigger_exists = conn.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_trigger "
                    "WHERE tgrelid = CAST(:relation AS regclass) "
                    "AND tgname = :trigger AND NOT tgisinternal)"
                ),
                {"relation": relation, "trigger": f"{table}_append_only"},
            ).scalar_one()
            privileges = conn.execute(
                text(
                    "SELECT "
                    "has_table_privilege('app_user', :relation, 'SELECT'), "
                    "has_table_privilege('app_user', :relation, 'INSERT'), "
                    "has_table_privilege('app_user', :relation, 'UPDATE'), "
                    "has_table_privilege('app_user', :relation, 'DELETE')"
                ),
                {"relation": relation},
            ).one()
            assert trigger_exists
            assert tuple(privileges) == (True, True, False, False)
    finally:
        engine.dispose()


@pytest.mark.parametrize("operation", ("UPDATE", "DELETE"))
def test_catalog_evidence_is_append_only_even_for_admin(
    migrated_scratch: tuple[str, str], operation: str
) -> None:
    admin_url, _ = migrated_scratch
    first, _ = _seed_tenants(admin_url)
    statement = (
        "UPDATE mod_analytics.metric_catalog_entries SET display_name = 'Changed' "
        "WHERE tenant_id = :tenant"
        if operation == "UPDATE"
        else "DELETE FROM mod_analytics.metric_catalog_entries "
        "WHERE tenant_id = :tenant"
    )
    engine = create_engine(admin_url)
    try:
        with pytest.raises(DBAPIError):
            with engine.begin() as conn:
                conn.execute(text(statement), {"tenant": first})
    finally:
        engine.dispose()
