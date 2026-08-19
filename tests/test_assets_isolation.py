"""PostgreSQL RLS and cross-tenant relationship canaries for dotmac-assets."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
ASSETS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-assets/src/dotmac_assets/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the assets canary needs Postgres")
    return url


def _url_for(base_url: str, database: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{database}"


@pytest.fixture
def assets_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"assets_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    previous_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {ASSETS_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        if previous_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous_url
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


def _seed_assets(admin_url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    asset_a = uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
        connection.execute(
            text(
                "INSERT INTO mod_assets.assets "
                "(id, tenant_id, code, name, kind, state, condition) VALUES "
                "(:a_id, :a_tenant, 'AST-1', 'Alpha asset', 'equipment', "
                "'registered', 'good'), "
                "(:b_id, :b_tenant, 'AST-1', 'Bravo asset', 'equipment', "
                "'registered', 'good')"
            ),
            {
                "a_id": asset_a,
                "a_tenant": tenant_a,
                "b_id": uuid.uuid4(),
                "b_tenant": tenant_b,
            },
        )
    engine.dispose()
    return tenant_a, tenant_b, asset_a


def test_a_tenant_sees_only_its_assets(assets_database: tuple[str, str]) -> None:
    admin_url, app_user_url = assets_database
    tenant_a, tenant_b, _ = _seed_assets(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            assert connection.execute(
                text("SELECT tenant_id FROM mod_assets.assets")
            ).scalars().all() == [tenant_a]
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_b)},
            )
            assert connection.execute(
                text("SELECT tenant_id FROM mod_assets.assets")
            ).scalars().all() == [tenant_b]
    finally:
        engine.dispose()


def test_a_cross_tenant_assignment_is_impossible(
    assets_database: tuple[str, str],
) -> None:
    admin_url, _ = assets_database
    _, tenant_b, asset_a = _seed_assets(admin_url)
    engine = create_engine(admin_url)
    try:
        with engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO mod_assets.asset_assignments "
                    "(id, tenant_id, asset_id, custodian_id, starts_on, status) "
                    "VALUES (:id, :tenant, :asset, :custodian, CURRENT_DATE, 'active')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_b,
                    "asset": asset_a,
                    "custodian": uuid.uuid4(),
                },
            )
    finally:
        engine.dispose()


def test_lifecycle_evidence_refuses_rewrite_even_for_the_migration_role(
    assets_database: tuple[str, str],
) -> None:
    admin_url, _ = assets_database
    tenant_a, _, asset_a = _seed_assets(admin_url)
    event_id = uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO mod_assets.asset_lifecycle_events "
                    "(id, tenant_id, asset_id, event_type, occurred_at) "
                    "VALUES (:id, :tenant, :asset, 'asset_created', now())"
                ),
                {"id": event_id, "tenant": tenant_a, "asset": asset_a},
            )
        with engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE mod_assets.asset_lifecycle_events "
                    "SET notes = 'rewritten' WHERE id = :id"
                ),
                {"id": event_id},
            )
    finally:
        engine.dispose()


def test_the_rls_canary_is_sensitive_to_a_disabled_guard(
    assets_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = assets_database
    tenant_a, _, _ = _seed_assets(admin_url)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    online = create_engine(app_user_url)
    try:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_assets.assets DISABLE ROW LEVEL SECURITY")
            )
        with online.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            visible = (
                connection.execute(
                    text("SELECT tenant_id FROM mod_assets.assets ORDER BY tenant_id")
                )
                .scalars()
                .all()
            )
        assert len(visible) == 2 and tenant_a in visible
    finally:
        with admin.connect() as connection:
            connection.execute(
                text("ALTER TABLE mod_assets.assets ENABLE ROW LEVEL SECURITY")
            )
            connection.execute(
                text("ALTER TABLE mod_assets.assets FORCE ROW LEVEL SECURITY")
            )
        online.dispose()
        admin.dispose()
