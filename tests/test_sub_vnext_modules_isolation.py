"""Real-Postgres isolation canaries for the Sub vNext parity cohort.

Each capability is added here before its implementation.  The scratch database
composes only the kernel and the lineage under test, and is always destroyed.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_referrals.manifest import module as referrals_module
from dotmac_reseller_management.manifest import module as reseller_module
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
REFERRALS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-referrals/src/dotmac_referrals/migrations/versions"
)
RESELLER_VERSIONS = (
    REPO_ROOT
    / "packages/dotmac-reseller-management"
    / "src/dotmac_reseller_management/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — Sub vNext RLS needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def referrals_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"referrals_rls_{uuid.uuid4().hex[:12]}"
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
            "version_locations", f"{KERNEL_VERSIONS} {REFERRALS_VERSIONS}"
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


@pytest.fixture
def reseller_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"reseller_rls_{uuid.uuid4().hex[:12]}"
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
            "version_locations", f"{KERNEL_VERSIONS} {RESELLER_VERSIONS}"
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


def test_referrals_live_catalog_and_cross_tenant_isolation(
    referrals_database: tuple[str, str],
) -> None:
    admin_url, app_url = referrals_database
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as conn:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
        conn.execute(
            text(
                "INSERT INTO mod_referrals.referral_programmes "
                "(id, tenant_id, code, name, qualification_policy_ref, "
                "reward_policy_ref, status) VALUES "
                "(:id, :tenant, 'A', 'Alpha', 'sub:q:v1', 'billing:r:v1', 'active')"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a},
        )
        registry = NamespaceRegistry.from_manifests([referrals_module])
        assert audit_live_schemas(conn, registry) == ()
    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant_b)},
        )
        count = conn.scalar(
            text("SELECT count(*) FROM mod_referrals.referral_programmes")
        )
        assert count == 0
    app.dispose()
    admin.dispose()


def test_reseller_live_catalog_and_cross_tenant_isolation(
    reseller_database: tuple[str, str],
) -> None:
    admin_url, app_url = reseller_database
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as conn:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
        conn.execute(
            text(
                "INSERT INTO mod_reseller.reseller_accounts "
                "(id, tenant_id, code, name, party_role_ref, status) VALUES "
                "(:id, :tenant, 'MASTER', 'Master', 'party-role:1', 'active')"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a},
        )
        registry = NamespaceRegistry.from_manifests([reseller_module])
        assert audit_live_schemas(conn, registry) == ()
    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant_b)},
        )
        count = conn.scalar(
            text("SELECT count(*) FROM mod_reseller.reseller_accounts")
        )
        assert count == 0
    app.dispose()
    admin.dispose()
