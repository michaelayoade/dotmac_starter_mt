"""PostgreSQL RLS canary for usage-rating state."""

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
    ROOT / "packages/dotmac-usage-rating/src/dotmac_usage_rating/migrations/versions"
)
TABLES = ("rating_rules", "rated_usage_obligations")


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
    name = f"usage_rating_{uuid.uuid4().hex[:10]}"
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


def test_all_rating_rows_are_cross_tenant_isolated(migrated: tuple[str, str]) -> None:
    admin_url, app_url = migrated
    tenants = (uuid.uuid4(), uuid.uuid4())
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        for index, tenant in enumerate(tenants):
            rule = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :slug)"
                ),
                {"id": tenant, "slug": f"ur-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_usage_rate.rating_rules "
                    "(id, tenant_id, code, meter_code, unit, unit_price, currency, "
                    "effective_from, effective_until) VALUES "
                    "(:id, :tenant, :code, 'bytes', 'byte', 2, 'NGN', now(), "
                    "now() + interval '1 day')"
                ),
                {"id": rule, "tenant": tenant, "code": f"rule-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_usage_rate.rated_usage_obligations "
                    "(id, tenant_id, usage_reference, service_reference, rule_id, "
                    "quantity, unit_price, net_amount, currency, usage_occurred_at, "
                    "rated_at) VALUES (:id, :tenant, :usage, :service, :rule, 2, 2, "
                    "4, 'NGN', now(), now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "usage": f"usage-{index}",
                    "service": f"service-{index}",
                    "rule": rule,
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
                    query = f"SELECT tenant_id FROM mod_usage_rate.{table}"  # noqa: S608
                    assert set(connection.execute(text(query)).scalars()) == {tenant}
    finally:
        app.dispose()
