"""Real-PostgreSQL isolation canary for dotmac-service-catalog."""

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
    / "packages/dotmac-service-catalog/src/dotmac_service_catalog/migrations/versions"
)
TABLES = (
    "plan_families",
    "plan_family_versions",
    "service_specifications",
    "service_specification_versions",
    "characteristic_definitions",
    "service_specification_characteristics",
    "eligibility_input_definitions",
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
    name = f"svc_catalog_{uuid.uuid4().hex[:12]}"
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


def test_every_catalogue_table_is_forced_and_filters_cross_tenant_rows(
    migrated: tuple[str, str],
) -> None:
    admin_url, app_url = migrated
    tenants = (uuid.uuid4(), uuid.uuid4())
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        for index, tenant in enumerate(tenants):
            family = uuid.uuid4()
            family_version = uuid.uuid4()
            specification = uuid.uuid4()
            specification_version = uuid.uuid4()
            definition = uuid.uuid4()
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :slug)"
                ),
                {"id": tenant, "slug": f"tenant-{index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO mod_svc_cat.plan_families "
                    "(id, tenant_id, code) VALUES (:id, :tenant, :code)"
                ),
                {"id": family, "tenant": tenant, "code": f"family-{index}"},
            )
            evidence = {
                "tenant": tenant,
                "source_id": uuid.uuid4(),
                "command_id": uuid.uuid4(),
            }
            connection.execute(
                text(
                    "INSERT INTO mod_svc_cat.plan_family_versions "
                    "(id, tenant_id, plan_family_id, version, name, state, "
                    "effective_from, source_code, source_id, source_version, "
                    "command_id, content_digest) VALUES "
                    "(:id, :tenant, :family, 1, :name, 'published', now(), "
                    "'isolation', :source_id, 1, :command_id, :digest)"
                ),
                {
                    **evidence,
                    "id": family_version,
                    "family": family,
                    "name": f"Family {index}",
                    "digest": f"{index:064d}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_svc_cat.service_specifications "
                    "(id, tenant_id, plan_family_id, code) "
                    "VALUES (:id, :tenant, :family, :code)"
                ),
                {
                    "id": specification,
                    "tenant": tenant,
                    "family": family,
                    "code": f"spec-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_svc_cat.service_specification_versions "
                    "(id, tenant_id, specification_id, plan_family_id, "
                    "plan_family_version_id, version, name, state, effective_from, "
                    "source_code, source_id, source_version, command_id, "
                    "content_digest) VALUES (:id, :tenant, :specification, "
                    ":family, :family_version, 1, :name, 'published', now(), "
                    "'isolation', :source_id, 1, :command_id, :digest)"
                ),
                {
                    **evidence,
                    "id": specification_version,
                    "specification": specification,
                    "family": family,
                    "family_version": family_version,
                    "name": f"Specification {index}",
                    "digest": f"{index + 10:064d}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_svc_cat.characteristic_definitions "
                    "(id, tenant_id, specification_id, code, name, kind) "
                    "VALUES (:id, :tenant, :specification, :code, :code, 'INTEGER')"
                ),
                {
                    "id": definition,
                    "tenant": tenant,
                    "specification": specification,
                    "code": f"speed-{index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_svc_cat.service_specification_characteristics "
                    "(id, tenant_id, specification_version_id, specification_id, "
                    "definition_id, integer_value) VALUES "
                    "(:id, :tenant, :version, :specification, :definition, 100)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "version": specification_version,
                    "specification": specification,
                    "definition": definition,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_svc_cat.eligibility_input_definitions "
                    "(id, tenant_id, specification_id, code, name) VALUES "
                    "(:id, :tenant, :specification, :code, :code)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "specification": specification,
                    "code": f"address-{index}",
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
                    assert set(
                        connection.execute(
                            text(
                                f"SELECT tenant_id FROM mod_svc_cat.{table}"  # noqa: S608
                            )
                        ).scalars()
                    ) == {tenant}
    finally:
        app.dispose()
