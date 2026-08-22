"""PostgreSQL RLS canary for operational escalations."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
_PACKAGE = ROOT / "packages/dotmac-operational-escalations"
VERSIONS = _PACKAGE / "src/dotmac_operational_escalations/migrations/versions"
TABLES = ("escalation_policies", "escalation_policy_versions", "escalation_instances")


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
    name = f"operational_escalations_{uuid.uuid4().hex[:12]}"
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


def test_all_operational_escalations_rows_are_cross_tenant_isolated(
    migrated: tuple[str, str],
) -> None:
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
                {"id": tenant, "slug": f"oe-{index}"},
            )
            policy = uuid.uuid4()
            version = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO mod_escalations.escalation_policies "
                    "(id, tenant_id, code, name, subject_type, trigger) VALUES "
                    "(:id, :tenant, :code, 'Outage', 'OUTAGE', 'UNRESOLVED')"
                ),
                {"id": policy, "tenant": tenant, "code": f"OUTAGE-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_escalations.escalation_policy_versions "
                    "(id, tenant_id, policy_id, version, level, channels, "
                    "cooldown_seconds, state, activated_at) VALUES "
                    "(:id, :tenant, :policy, 1, 1, '[\"EMAIL\"]', 0, 'ACTIVE', now())"
                ),
                {"id": version, "tenant": tenant, "policy": policy},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_escalations.escalation_instances "
                    "(id, tenant_id, policy_version_id, subject_type, "
                    "subject_reference, trigger, level, dedup_key, status, "
                    "raised_at) VALUES (:id, :tenant, :version, 'OUTAGE', "
                    ":subject, 'UNRESOLVED', 1, :dedup, 'OPEN', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "version": version,
                    "subject": f"outage-{index}",
                    "dedup": f"outage-{index}:unresolved",
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
                    query = f"SELECT tenant_id FROM mod_escalations.{table}"  # noqa: S608
                    assert set(connection.execute(text(query)).scalars()) == {tenant}
    finally:
        app.dispose()
