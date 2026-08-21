"""Real-PostgreSQL isolation canaries for ``dotmac-sites``.

This test is checked in before implementation. Gate 2 composes only kernel and
sites in a disposable database and drives the real ``app_user`` role so service
filters cannot hide an RLS defect.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_sites.manifest import module
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
SITES_VERSIONS = (
    REPO_ROOT / "packages/dotmac-sites/src/dotmac_sites/migrations/versions"
)
TABLES = (
    "sites",
    "pages",
    "page_revisions",
    "site_revisions",
    "site_revision_pages",
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — sites RLS needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_sites() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"sites_rls_{uuid.uuid4().hex[:12]}"
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
        cfg.set_main_option("version_locations", f"{KERNEL_VERSIONS} {SITES_VERSIONS}")
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
            site_id = uuid.uuid4()
            page_id = uuid.uuid4()
            page_revision_id = uuid.uuid4()
            site_revision_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_sites.sites "
                    "(id, tenant_id, slug, name, state, created_by_ref) "
                    "VALUES (:id, :tenant, :slug, :name, 'active', :actor)"
                ),
                {
                    "id": site_id,
                    "tenant": tenant_id,
                    "slug": slug,
                    "name": slug.title(),
                    "actor": uuid.uuid4(),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_sites.pages "
                    "(id, tenant_id, site_id, page_key, created_by_ref) "
                    "VALUES (:id, :tenant, :site, 'home', :actor)"
                ),
                {
                    "id": page_id,
                    "tenant": tenant_id,
                    "site": site_id,
                    "actor": uuid.uuid4(),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_sites.page_revisions "
                    "(id, tenant_id, site_id, page_id, revision_number, title, "
                    "body, seo_payload, file_refs, form_refs, content_digest, "
                    "created_by_ref) VALUES (:id, :tenant, :site, :page, 1, "
                    "'Home', 'Welcome', CAST('{}' AS jsonb), CAST('[]' AS jsonb), "
                    "CAST('[]' AS jsonb), :digest, :actor)"
                ),
                {
                    "id": page_revision_id,
                    "tenant": tenant_id,
                    "site": site_id,
                    "page": page_id,
                    "digest": "1" * 64,
                    "actor": uuid.uuid4(),
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_sites.site_revisions "
                    "(id, tenant_id, site_id, revision_number, state, "
                    "snapshot_payload, snapshot_digest, created_by_ref, "
                    "ready_at) VALUES "
                    "(:id, :tenant, :site, 1, 'ready', CAST(:payload AS jsonb), "
                    ":digest, :actor, :ready_at)"
                ),
                {
                    "id": site_revision_id,
                    "tenant": tenant_id,
                    "site": site_id,
                    "payload": '{"schema_version": 1}',
                    "digest": "2" * 64,
                    "actor": uuid.uuid4(),
                    "ready_at": "2026-08-19T10:00:00+00:00",
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_sites.site_revision_pages "
                    "(id, tenant_id, site_id, site_revision_id, page_id, "
                    "page_revision_id, path, sort_order) VALUES "
                    "(:id, :tenant, :site, :site_revision, :page, "
                    ":page_revision, '/', 0)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_id,
                    "site": site_id,
                    "site_revision": site_revision_id,
                    "page": page_id,
                    "page_revision": page_revision_id,
                },
            )
    engine.dispose()
    return tenant_a, tenant_b


def test_live_catalog_proves_the_complete_forced_rls_plane(
    migrated_sites: tuple[str, str],
) -> None:
    admin_url, _ = migrated_sites
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert audit_live_schemas(conn, registry) == ()
        rows = list(
            conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='mod_sites' AND c.relkind='r'"
                )
            )
        )
    engine.dispose()
    assert {row[0] for row in rows} == set(TABLES)
    assert all(row[1] and row[2] for row in rows)


def test_real_app_user_sees_only_its_tenant_on_all_five_tables(
    migrated_sites: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_sites
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
                            text(
                                f"SELECT tenant_id FROM mod_sites.{table}"  # noqa: S608
                            )
                        ).scalars()
                    )
                    assert tenants == {tenant_id}, table
    finally:
        engine.dispose()


def test_real_app_user_cannot_write_for_another_tenant(
    migrated_sites: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_sites
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
                        "INSERT INTO mod_sites.sites "
                        "(id, tenant_id, slug, name, state, created_by_ref) "
                        "VALUES (:id, :tenant, 'forged', 'Forged', 'active', :actor)"
                    ),
                    {"id": uuid.uuid4(), "tenant": tenant_b, "actor": uuid.uuid4()},
                )
    finally:
        engine.dispose()


def test_unscoped_app_user_fails_closed_on_all_five_tables(
    migrated_sites: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_sites
    _seed_plane(admin_url)
    engine = create_engine(app_url)
    try:
        with engine.begin() as conn:
            for table in TABLES:
                assert (
                    conn.execute(
                        text(f"SELECT count(*) FROM mod_sites.{table}")  # noqa: S608
                    ).scalar_one()
                    == 0
                ), table
    finally:
        engine.dispose()


def test_database_refuses_mutation_of_immutable_revision_rows(
    migrated_sites: tuple[str, str],
) -> None:
    admin_url, _ = migrated_sites
    _seed_plane(admin_url)
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            page_revision = conn.execute(
                text("SELECT id FROM mod_sites.page_revisions LIMIT 1")
            ).scalar_one()
            with pytest.raises(DBAPIError, match="append-only"):
                conn.execute(
                    text(
                        "UPDATE mod_sites.page_revisions SET title='Changed' "
                        "WHERE id=:id"
                    ),
                    {"id": page_revision},
                )
    finally:
        engine.dispose()
