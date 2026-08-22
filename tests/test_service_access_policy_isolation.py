"""PostgreSQL RLS canary for desired service-access policy."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
VERSIONS = (
    ROOT
    / "packages/dotmac-service-access-policy"
    / "src/dotmac_service_access_policy/migrations/versions"
)
TABLES = ("service_access_inputs", "desired_access_decisions")


def _url(base: str, database: str, user: str | None = None) -> str:
    prefix, _, _ = base.rpartition("/")
    if user:
        scheme, _, host = prefix.partition("://")
        prefix = f"{scheme}://{user}@{host.rpartition('@')[2]}"
    return f"{prefix}/{database}"


@pytest.fixture
def migrated() -> Iterator[tuple[str, str]]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv(
        "TEST_DATABASE_URL"
    )
    if not superuser:
        pytest.skip("PostgreSQL URL required")
    name = f"access_policy_{uuid.uuid4().hex[:9]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()
    admin = _url(superuser, name, "app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option("version_locations", f"{KERNEL} {VERSIONS}")
        os.environ["MIGRATION_DATABASE_URL"] = admin
        command.upgrade(cfg, "heads")
        yield admin, _url(superuser, name, "app_user")
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


def test_policy_rows_are_cross_tenant_isolated(migrated: tuple[str, str]) -> None:
    admin_url, app_url = migrated
    tenants = (uuid.uuid4(), uuid.uuid4())
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        for index, tenant in enumerate(tenants):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :slug)"
                ),
                {"id": tenant, "slug": f"sa-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_serviceaccess.service_access_inputs "
                    "(id, tenant_id, service_reference, signal, active, "
                    "source_reference, observed_at) VALUES "
                    "(:id, :tenant, :service, 'FUP_EXHAUSTED', true, 'fup', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "service": f"service-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_serviceaccess.desired_access_decisions "
                    "(id, tenant_id, service_reference, desired_access, reason_code, "
                    "decided_at) VALUES (:id, :tenant, :service, 'RESTRICT', "
                    "'FUP_EXHAUSTED', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "service": f"service-{index}",
                },
            )
    admin.dispose()
    app = create_engine(app_url)
    try:
        for tenant in tenants:
            with app.begin() as connection:
                connection.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant)},
                )
                for table in TABLES:
                    query = f"SELECT tenant_id FROM mod_serviceaccess.{table}"  # noqa: S608
                    assert set(connection.execute(text(query)).scalars()) == {tenant}
    finally:
        app.dispose()
