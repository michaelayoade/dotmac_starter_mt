"""Real-PostgreSQL isolation canaries for the optional content module.

The reference assembly builds but does not compose ``dotmac-content``. This
suite creates a disposable database, composes only the kernel and content
lineages, and drives the real ``app_user`` role so service filtering cannot
hide a missing or incomplete RLS policy.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_content.manifest import module
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
CONTENT_VERSIONS = (
    REPO_ROOT / "packages/dotmac-content/src/dotmac_content/migrations/versions"
)
TABLES = (
    "content_plans",
    "content_items",
    "content_variants",
    "content_plan_creatives",
    "content_item_creatives",
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — content RLS needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_content() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"content_rls_{uuid.uuid4().hex[:12]}"
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
            "version_locations", f"{KERNEL_VERSIONS} {CONTENT_VERSIONS}"
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


def _seed_plane(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            plan_id = uuid.uuid4()
            item_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_content.content_plans "
                    "(id, tenant_id, name, status, created_by_ref) "
                    "VALUES (:id, :tenant, :name, 'draft', :actor)"
                ),
                {
                    "id": plan_id,
                    "tenant": tenant_id,
                    "name": f"{slug} plan",
                    "actor": uuid.uuid4(),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_content.content_items "
                    "(id, tenant_id, content_plan_id, title, body, state, "
                    "created_by_ref) VALUES "
                    "(:id, :tenant, :plan, :title, :body, 'draft', :actor)"
                ),
                {
                    "id": item_id,
                    "tenant": tenant_id,
                    "plan": plan_id,
                    "title": f"{slug} item",
                    "body": f"{slug} body",
                    "actor": uuid.uuid4(),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_content.content_variants "
                    "(id, tenant_id, content_item_id, variant_key, sort_order) "
                    "VALUES (:id, :tenant, :item, 'short', 0)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_id, "item": item_id},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_content.content_plan_creatives "
                    "(id, tenant_id, content_plan_id, file_ref, role, sort_order) "
                    "VALUES (:id, :tenant, :plan, :file, 'hero', 0)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_id,
                    "plan": plan_id,
                    "file": uuid.uuid4(),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_content.content_item_creatives "
                    "(id, tenant_id, content_item_id, file_ref, role, sort_order) "
                    "VALUES (:id, :tenant, :item, :file, 'inline', 0)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_id,
                    "item": item_id,
                    "file": uuid.uuid4(),
                },
            )
    engine.dispose()
    return tenant_a, tenant_b


def test_live_catalog_proves_the_complete_forced_rls_plane(
    migrated_content: tuple[str, str],
) -> None:
    admin_url, _ = migrated_content
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert audit_live_schemas(conn, registry) == ()
        rows = list(
            conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='mod_content' AND c.relkind='r'"
                )
            )
        )
    engine.dispose()
    assert {row[0] for row in rows} == set(TABLES)
    assert all(row[1] and row[2] for row in rows)


def test_real_app_user_sees_only_its_tenant_on_all_five_tables(
    migrated_content: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_content
    tenant_a, tenant_b = _seed_plane(admin_url)
    engine = create_engine(app_url)
    try:
        for tenant_id in (tenant_a, tenant_b):
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant_id)},
                )
                for table in TABLES:
                    tenants = set(
                        conn.execute(
                            text(f"SELECT tenant_id FROM mod_content.{table}")  # noqa: S608
                        ).scalars()
                    )
                    assert tenants == {tenant_id}, table
    finally:
        engine.dispose()


def test_real_app_user_cannot_write_for_another_tenant(
    migrated_content: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_content
    tenant_a, tenant_b = _seed_plane(admin_url)
    engine = create_engine(app_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_a)},
            )
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(
                    text(
                        "INSERT INTO mod_content.content_plans "
                        "(id, tenant_id, name, status, created_by_ref) "
                        "VALUES (:id, :tenant, 'forged', 'draft', :actor)"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant_b,
                        "actor": uuid.uuid4(),
                    },
                )
    finally:
        engine.dispose()


def test_unscoped_online_role_fails_closed(migrated_content: tuple[str, str]) -> None:
    admin_url, app_url = migrated_content
    _seed_plane(admin_url)
    engine = create_engine(app_url)
    try:
        with engine.begin() as conn:
            for table in TABLES:
                assert (
                    conn.execute(
                        text(f"SELECT count(*) FROM mod_content.{table}")  # noqa: S608
                    ).scalar_one()
                    == 0
                )
    finally:
        engine.dispose()
