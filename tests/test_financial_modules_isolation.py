"""Real-Postgres isolation canaries for banking, tax, and payroll.

The reference assembly builds these optional modules but does not compose their
lineages.  Each case therefore creates a disposable database containing only
the kernel and one candidate module, then drives the real online tenant role.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from dotmac_banking.manifest import module as banking_module
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_payroll.manifest import module as payroll_module
from dotmac_tax.manifest import module as tax_module
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)


@dataclass(frozen=True, slots=True)
class ModuleCase:
    manifest: ModuleManifest
    distribution: str
    package_name: str
    schema: str
    probe_table: str
    seed_sql: str
    seed_values: dict[str, str]

    @property
    def versions(self) -> Path:
        return REPO_ROOT.joinpath(
            "packages",
            self.distribution,
            "src",
            self.package_name,
            "migrations",
            "versions",
        )


CASES = (
    ModuleCase(
        manifest=banking_module,
        distribution="dotmac-banking",
        package_name="dotmac_banking",
        schema="mod_banking",
        probe_table="bank_institutions",
        seed_sql=(
            "INSERT INTO mod_banking.bank_institutions "
            "(id, tenant_id, code, name, country_code, status) "
            "VALUES (:id, :tenant, :code, :name, 'NG', 'active')"
        ),
        seed_values={"code": "BANK-1", "name": "Configured institution"},
    ),
    ModuleCase(
        manifest=tax_module,
        distribution="dotmac-tax",
        package_name="dotmac_tax",
        schema="mod_tax",
        probe_table="tax_authorities",
        seed_sql=(
            "INSERT INTO mod_tax.tax_authorities "
            "(id, tenant_id, code, name, status) "
            "VALUES (:id, :tenant, :code, :name, 'active')"
        ),
        seed_values={"code": "AUTH-1", "name": "Configured authority"},
    ),
    ModuleCase(
        manifest=payroll_module,
        distribution="dotmac-payroll",
        package_name="dotmac_payroll",
        schema="mod_payroll",
        probe_table="pay_components",
        seed_sql=(
            "INSERT INTO mod_payroll.pay_components "
            "(id, tenant_id, component_code, name, kind, status) "
            "VALUES (:id, :tenant, :code, :name, 'information', 'active')"
        ),
        seed_values={"code": "INFO-1", "name": "Configured component"},
    ),
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these RLS proofs need PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture(params=CASES, ids=lambda case: case.manifest.code)
def migrated_module(
    request: pytest.FixtureRequest,
) -> Iterator[tuple[ModuleCase, str, str]]:
    case: ModuleCase = request.param
    superuser = _superuser_url()
    name = f"{case.manifest.code}_rls_{uuid.uuid4().hex[:12]}"
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

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations", f"{KERNEL_VERSIONS} {case.versions}"
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield case, admin_url, _url_for(superuser, name, user="app_user")
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


def _seed_tenants(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as connection:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
    engine.dispose()
    return tenant_a, tenant_b


def _tenant_session(url: str, tenant_id: uuid.UUID) -> Session:
    session = Session(create_engine(url))
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, false)"),
        {"tenant": str(tenant_id)},
    )
    return session


def test_live_catalog_proves_every_declared_table_is_tenant_isolated(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    case, admin_url, _ = migrated_module
    registry = NamespaceRegistry.from_manifests([case.manifest])
    engine = create_engine(admin_url)
    with engine.connect() as connection:
        assert audit_live_schemas(connection, registry) == ()
        platform_reads = {
            table: connection.execute(
                text(
                    "SELECT has_table_privilege("
                    "'platform_api', CAST(:table AS text), 'SELECT')"
                ),
                {"table": f"{case.schema}.{table}"},
            ).scalar_one()
            for table in case.manifest.tables
        }
    engine.dispose()
    assert not any(platform_reads.values())


def test_online_role_cannot_read_or_write_across_tenants(
    migrated_module: tuple[ModuleCase, str, str],
) -> None:
    case, admin_url, app_url = migrated_module
    tenant_a, tenant_b = _seed_tenants(admin_url)
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        connection.execute(
            text(case.seed_sql),
            {"id": uuid.uuid4(), "tenant": tenant_a, **case.seed_values},
        )
    admin.dispose()

    with _tenant_session(app_url, tenant_b) as session:
        count = session.scalar(
            text(
                f"SELECT count(*) FROM {case.schema}.{case.probe_table}"  # noqa: S608
            )
        )
        assert count == 0
        with pytest.raises(DBAPIError, match="row-level security"):
            session.execute(
                text(case.seed_sql),
                {"id": uuid.uuid4(), "tenant": tenant_a, **case.seed_values},
            )
