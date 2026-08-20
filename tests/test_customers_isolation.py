"""Real-PostgreSQL RLS canary for dotmac-customers."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
CUSTOMER_VERSIONS = (
    REPO_ROOT / "packages/dotmac-customers/src/dotmac_customers/migrations/versions"
)
TABLES = ("customer_accounts", "customer_profiles", "customer_party_references")


def _url_for(base_url: str, database: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        scheme_userhost = f"{scheme}://{user}@{userhost.rpartition('@')[2]}"
    return f"{scheme_userhost}/{database}"


@pytest.fixture
def migrated_customers() -> Iterator[tuple[str, str]]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    if not superuser:
        pytest.skip("TEST_DATABASE_URL not set — the RLS canary needs PostgreSQL")
    name = f"customers_rls_{uuid.uuid4().hex[:12]}"
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
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations", f"{KERNEL_VERSIONS} {CUSTOMER_VERSIONS}"
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
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


def test_customer_plane_is_forced_and_cross_tenant_reads_are_impossible(
    migrated_customers: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_customers
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        for tenant, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            account = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant, "slug": slug, "name": slug.title()},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_customers.customer_accounts "
                    "(id, tenant_id, account_number, display_name, status) "
                    "VALUES (:id, :tenant, :number, :name, 'ACTIVE')"
                ),
                {"id": account, "tenant": tenant, "number": slug, "name": slug},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_customers.customer_profiles "
                    "(id, tenant_id, account_id, segment) "
                    "VALUES (:id, :tenant, :account, 'RETAIL')"
                ),
                {"id": uuid.uuid4(), "tenant": tenant, "account": account},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_customers.customer_party_references "
                    "(id, tenant_id, account_id, party_system, party_reference, role) "
                    "VALUES (:id, :tenant, :account, 'party', :reference, "
                    "'ACCOUNT_HOLDER')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "account": account,
                    "reference": slug,
                },
            )
    admin.dispose()

    app = create_engine(app_url)
    try:
        for tenant in (tenant_a, tenant_b):
            with app.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant)},
                )
                for table in TABLES:
                    assert set(
                        connection.execute(
                            text(f"SELECT tenant_id FROM mod_customers.{table}")  # noqa: S608
                        ).scalars()
                    ) == {tenant}
    finally:
        app.dispose()
