"""Real-Postgres isolation canaries for all implemented ADR-0040 tenant modules."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_ai_operations.manifest import module as ai_module
from dotmac_compliance_reporting.manifest import module as compliance_module
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_referrals.manifest import module as referrals_module
from dotmac_remote_access.manifest import module as remote_module
from dotmac_reseller_management.manifest import module as reseller_module
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
REMOTE = (
    ROOT / "packages/dotmac-remote-access/src/dotmac_remote_access/migrations/versions"
)
COMPLIANCE = (
    ROOT
    / "packages/dotmac-compliance-reporting"
    / "src/dotmac_compliance_reporting/migrations/versions"
)
AI = ROOT / "packages/dotmac-ai-operations/src/dotmac_ai_operations/migrations/versions"
REFERRALS = ROOT / "packages/dotmac-referrals/src/dotmac_referrals/migrations/versions"
RESELLER = (
    ROOT
    / "packages/dotmac-reseller-management"
    / "src/dotmac_reseller_management/migrations/versions"
)


def _url(base: str, database: str, user: str | None = None) -> str:
    prefix, _, _ = base.rpartition("/")
    if user:
        scheme, _, authority = prefix.partition("://")
        prefix = f"{scheme}://{user}@{authority.rpartition('@')[2]}"
    return f"{prefix}/{database}"


def _superuser_url() -> str:
    value = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL not set — tenant RLS needs Postgres")
    return value


def _migrated_database(prefix: str, versions: Path) -> Iterator[tuple[str, str]]:
    """Build and tear down one lineage-specific database.

    Referrals and Reseller Management retain their original independent proof;
    the new three-module cohort has an additional composed proof below.
    """
    superuser = _superuser_url()
    name = f"{prefix}_{uuid.uuid4().hex[:10]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin = _url(superuser, name, "app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("version_locations", f"{KERNEL} {versions}")
        os.environ["MIGRATION_DATABASE_URL"] = admin
        command.upgrade(cfg, "heads")
        yield admin, _url(superuser, name, "app_user")
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
def referrals_database() -> Iterator[tuple[str, str]]:
    yield from _migrated_database("referrals_rls", REFERRALS)


@pytest.fixture
def reseller_database() -> Iterator[tuple[str, str]]:
    yield from _migrated_database("reseller_rls", RESELLER)


@pytest.fixture(scope="module")
def database() -> Iterator[tuple[str, str]]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    if not superuser:
        pytest.skip("TEST_DATABASE_URL not set — tenant RLS needs Postgres")
    name = f"sub_vnext_{uuid.uuid4().hex[:10]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin = _url(superuser, name, "app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("version_locations", f"{KERNEL} {REMOTE} {COMPLIANCE} {AI}")
        os.environ["MIGRATION_DATABASE_URL"] = admin
        command.upgrade(cfg, "heads")
        yield admin, _url(superuser, name, "app_user")
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


def test_all_three_modules_force_rls_and_hide_cross_tenant_rows(
    database: tuple[str, str],
) -> None:
    admin_url, app_url = database
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
                "INSERT INTO mod_remoteaccess.remote_access_requests "
                "(id, tenant_id, request_key, request_digest, target_ref, purpose, "
                "scopes, requester_ref, status, requested_at) VALUES "
                "(:id, :tenant, 'r:1', :digest, 'device:1', 'diagnose', "
                "'[\"read\"]', 'op:1', 'pending', now())"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a, "digest": "a" * 64},
        )
        conn.execute(
            text(
                "INSERT INTO mod_compliance.reporting_obligations "
                "(id, tenant_id, code, jurisdiction, title, active) "
                "VALUES (:id, :tenant, 'ncc', 'NG-NCC', 'NCC', true)"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a},
        )
        conn.execute(
            text(
                "INSERT INTO mod_aiops.ai_policies "
                "(id, tenant_id, code, title, active) "
                "VALUES (:id, :tenant, 'intake', 'Intake', true)"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a},
        )
        assert (
            audit_live_schemas(
                conn,
                NamespaceRegistry.from_manifests(
                    [remote_module, compliance_module, ai_module]
                ),
            )
            == ()
        )
    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant_b)},
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM mod_remoteaccess.remote_access_requests")
            )
            == 0
        )
        assert (
            conn.scalar(
                text("SELECT count(*) FROM mod_compliance.reporting_obligations")
            )
            == 0
        )
        assert conn.scalar(text("SELECT count(*) FROM mod_aiops.ai_policies")) == 0
    app.dispose()
    admin.dispose()


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
                "(:id, :tenant, 'A', 'Alpha', 'sub:q:v1', "
                "'billing:r:v1', 'active')"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a},
        )
        assert (
            audit_live_schemas(
                conn, NamespaceRegistry.from_manifests([referrals_module])
            )
            == ()
        )
    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant_b)},
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM mod_referrals.referral_programmes"))
            == 0
        )
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
                "(id, tenant_id, code, name, party_role_ref, status) "
                "VALUES (:id, :tenant, 'MASTER', 'Master', "
                "'party-role:1', 'active')"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a},
        )
        assert (
            audit_live_schemas(
                conn, NamespaceRegistry.from_manifests([reseller_module])
            )
            == ()
        )
    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant_b)},
        )
        assert (
            conn.scalar(text("SELECT count(*) FROM mod_reseller.reseller_accounts"))
            == 0
        )
    app.dispose()
    admin.dispose()
