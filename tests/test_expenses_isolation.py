"""PostgreSQL canaries for the Expenses tenant plane."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_expenses.contracts import CreateCategory
from dotmac_expenses.manifest import module
from dotmac_expenses.models import ExpenseCategory
from dotmac_expenses.service import create_category
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
EXPENSES_VERSIONS = (
    ROOT / "packages/dotmac-expenses/src/dotmac_expenses/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — Expenses RLS needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_scratch() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"expenses_rls_{uuid.uuid4().hex[:12]}"
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

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations", f"{KERNEL_VERSIONS} {EXPENSES_VERSIONS}"
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


def _seed_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
    engine.dispose()
    return tenant_a, tenant_b


def _tenant_session(app_url: str, tenant_id: uuid.UUID) -> Session:
    session = Session(create_engine(app_url))
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, false)"),
        {"tenant": str(tenant_id)},
    )
    return session


def test_live_catalog_proves_forced_rls_and_composite_contract(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert audit_live_schemas(conn, registry) == ()
    engine.dispose()


def test_online_role_cannot_read_or_forge_another_tenant(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    tenant_a, tenant_b = _seed_tenants(admin_url)
    with _tenant_session(app_url, tenant_a) as db:
        row = create_category(
            db,
            scope=TenantScope(tenant_a),
            command=CreateCategory(code="FUEL", name="Fuel"),
        )
        row_id = row.id
        db.commit()
    with _tenant_session(app_url, tenant_b) as db:
        assert db.scalar(select(func.count()).select_from(ExpenseCategory)) == 0
        with pytest.raises(DBAPIError):
            create_category(
                db,
                scope=TenantScope(tenant_a),
                command=CreateCategory(code="FORGED", name="Forged"),
            )
    assert row_id is not None
