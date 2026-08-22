"""PostgreSQL RLS canary for FX-policy observations and determinations."""

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
    ROOT / "packages/dotmac-fx-policy" / "src/dotmac_fx_policy/migrations/versions"
)
TABLES = (
    "fx_rate_types",
    "fx_rate_sources",
    "fx_selection_policies",
    "fx_rate_observations",
    "fx_rate_determinations",
)


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
    name = f"fx_policy_{uuid.uuid4().hex[:11]}"
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


def test_fx_policy_rows_are_cross_tenant_isolated(
    migrated: tuple[str, str],
) -> None:
    admin_url, app_url = migrated
    tenants = (uuid.uuid4(), uuid.uuid4())
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        for index, tenant in enumerate(tenants):
            rate_type, source = uuid.uuid4(), uuid.uuid4()
            policy, observation = uuid.uuid4(), uuid.uuid4()
            values = {
                "tenant": tenant,
                "rate_type": rate_type,
                "source": source,
                "policy": policy,
                "observation": observation,
            }
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:tenant, :slug, :slug)"
                ),
                {"tenant": tenant, "slug": f"fx-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_fx_policy.fx_rate_types "
                    "(id, tenant_id, code, name, is_default) VALUES "
                    "(:rate_type, :tenant, 'SPOT', 'Spot', true)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO mod_fx_policy.fx_rate_sources "
                    "(id, tenant_id, code, name, priority, active) VALUES "
                    "(:source, :tenant, :code, 'Manual', 10, true)"
                ),
                values | {"code": f"MANUAL-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_fx_policy.fx_selection_policies "
                    "(id, tenant_id, rate_type_id, base_currency, quote_currency, "
                    "effective_from, preferred_source_id, allow_inverse, active) "
                    "VALUES (:policy, :tenant, :rate_type, 'USD', 'NGN', "
                    "now() - interval '1 day', :source, true, true)"
                ),
                values,
            )
            connection.execute(
                text(
                    "INSERT INTO mod_fx_policy.fx_rate_observations "
                    "(id, tenant_id, rate_type_id, source_id, base_currency, "
                    "quote_currency, rate, effective_at, observed_at, "
                    "source_event_reference) VALUES "
                    "(:observation, :tenant, :rate_type, :source, 'USD', 'NGN', "
                    "1500.5, now(), now(), :event)"
                ),
                values | {"event": f"manual:{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_fx_policy.fx_rate_determinations "
                    "(id, tenant_id, request_reference, rate_type_id, policy_id, "
                    "observation_id, base_currency, quote_currency, rate, "
                    "effective_at, inverted, determined_at) VALUES "
                    "(:id, :tenant, :request, :rate_type, :policy, :observation, "
                    "'USD', 'NGN', 1500.5, now(), false, now())"
                ),
                values | {"id": uuid.uuid4(), "request": f"quote:{index}"},
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
                    query = f"SELECT tenant_id FROM mod_fx_policy.{table}"  # noqa: S608
                    assert set(connection.execute(text(query)).scalars()) == {tenant}
    finally:
        app.dispose()
