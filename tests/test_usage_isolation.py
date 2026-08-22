"""PostgreSQL RLS canary for normalized usage state."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
VERSIONS = ROOT / "packages/dotmac-usage/src/dotmac_usage/migrations/versions"
TABLES = ("usage_observations", "usage_corrections", "usage_aggregates")


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
    name = f"usage_{uuid.uuid4().hex[:12]}"
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


def test_all_usage_rows_are_cross_tenant_isolated(migrated: tuple[str, str]) -> None:
    admin_url, app_url = migrated
    tenants = (uuid.uuid4(), uuid.uuid4())
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        for index, tenant in enumerate(tenants):
            observation = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :slug)"
                ),
                {"id": tenant, "slug": f"u-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_usage.usage_observations "
                    "(id, tenant_id, service_reference, meter_code, period_start, "
                    "period_end, quantity, unit, source_reference, source_event_id) "
                    "VALUES (:id, :tenant, :service, 'bytes', now(), "
                    "now() + interval '1 hour', 100, 'byte', 'collector', :event)"
                ),
                {
                    "id": observation,
                    "tenant": tenant,
                    "service": f"service-{index}",
                    "event": f"event-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_usage.usage_corrections "
                    "(id, tenant_id, observation_id, delta_quantity, reason, "
                    "corrected_at) VALUES (:id, :tenant, :observation, -1, "
                    "'rounding', now())"
                ),
                {"id": uuid.uuid4(), "tenant": tenant, "observation": observation},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_usage.usage_aggregates "
                    "(id, tenant_id, service_reference, meter_code, window_start, "
                    "window_end, quantity, computed_at) VALUES "
                    "(:id, :tenant, :service, 'bytes', now(), "
                    "now() + interval '1 day', 99, now())"
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
                    query = f"SELECT tenant_id FROM mod_usage.{table}"  # noqa: S608
                    assert set(connection.execute(text(query)).scalars()) == {tenant}
    finally:
        app.dispose()
