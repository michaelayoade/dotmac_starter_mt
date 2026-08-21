"""Real-Postgres platform isolation canary for ``dotmac-support-access``."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_support_access.manifest import module
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
VERSIONS = ROOT / "packages/dotmac-support-access/src/dotmac_support_access/migrations/versions"
TABLES = module.platform_tables
PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


def _url(base: str, database: str, user: str | None = None) -> str:
    prefix, _, _ = base.rpartition("/")
    if user:
        scheme, _, authority = prefix.partition("://")
        prefix = f"{scheme}://{user}@{authority.rpartition('@')[2]}"
    return f"{prefix}/{database}"


@pytest.fixture(scope="module")
def database() -> Iterator[tuple[str, str, str]]:
    superuser = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not superuser:
        pytest.skip("TEST_DATABASE_URL not set — platform grants need Postgres")
    name = f"support_access_{uuid.uuid4().hex[:10]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    setup = create_engine(_url(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        for role in ("platform_api", "app_user"):
            conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO {role}'))
            conn.execute(text(f"GRANT USAGE ON SCHEMA public TO {role}"))
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
        yield admin, _url(superuser, name, "platform_api"), _url(superuser, name, "app_user")
    finally:
        with server.connect() as conn:
            conn.execute(text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name AND pid <> pg_backend_pid()"), {"name": name})
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def test_support_access_platform_catalog_and_privileges(database: tuple[str, str, str]) -> None:
    admin_url, platform_url, app_url = database
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert audit_live_schemas(conn, NamespaceRegistry.from_manifests([module])) == ()
        secretish = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_schema = 'mod_supportaccess' AND column_name IN ('token', 'credential', 'password', 'secret', 'private_key')")).all()
        assert not secretish
        for table in TABLES:
            enabled, forced = conn.execute(text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = CAST(:table AS regclass)"), {"table": f"mod_supportaccess.{table}"}).one()
            assert not enabled and not forced
            assert any(conn.scalar(text("SELECT has_table_privilege('platform_api', :table, :privilege)"), {"table": f"mod_supportaccess.{table}", "privilege": p}) for p in ("SELECT", "INSERT", "UPDATE", "DELETE"))
            assert not any(conn.scalar(text("SELECT has_table_privilege('app_user', :table, :privilege)"), {"table": f"mod_supportaccess.{table}", "privilege": p}) for p in PRIVILEGES)
    engine.dispose()
    platform = create_engine(platform_url)
    with platform.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM mod_supportaccess.support_access_requests")) == 0
    platform.dispose()
    app = create_engine(app_url)
    with app.connect() as conn, pytest.raises((DBAPIError, ProgrammingError)):
        conn.execute(text("SELECT 1 FROM mod_supportaccess.support_access_requests"))
    app.dispose()

