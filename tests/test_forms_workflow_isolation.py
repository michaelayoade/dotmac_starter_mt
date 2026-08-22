"""Real-Postgres isolation canary for the ADR-0040 Backoffice runtime cohort."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_forms.manifest import module as forms_module
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_workflow_runtime.manifest import module as workflow_module
from sqlalchemy import create_engine, text

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
FORMS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-forms/src/dotmac_forms/migrations/versions"
)
WORKFLOW_VERSIONS = (
    REPO_ROOT
    / "packages/dotmac-workflow-runtime/src/dotmac_workflow_runtime/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — Forms/Workflow RLS needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def module_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"forms_workflow_{uuid.uuid4().hex[:10]}"
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

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {FORMS_VERSIONS} {WORKFLOW_VERSIONS}",
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


def test_forms_and_workflow_are_cross_tenant_isolated(
    module_database: tuple[str, str],
) -> None:
    admin_url, app_url = module_database
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as conn:
        for tenant_id, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            conn.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
        conn.execute(
            text(
                "INSERT INTO mod_forms.forms (id, tenant_id, name, form_type) "
                "VALUES (:id, :tenant, 'Application', 'recruitment')"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a},
        )
        conn.execute(
            text(
                "INSERT INTO mod_workflow.workflow_executions "
                "(id, tenant_id, definition_version_ref, definition_digest, "
                "subject_ref, source_owner, source_event_id, request_fingerprint, "
                "status, started_at) VALUES (:id, :tenant, 'workflow:v1', :digest, "
                "'expense:1', 'expenses', "
                "'event:1', :digest, 'pending', now())"
            ),
            {"id": uuid.uuid4(), "tenant": tenant_a, "digest": "a" * 64},
        )
        registry = NamespaceRegistry.from_manifests([forms_module, workflow_module])
        assert audit_live_schemas(conn, registry) == ()
    app = create_engine(app_url)
    with app.begin() as conn:
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, false)"),
            {"tenant": str(tenant_b)},
        )
        assert conn.scalar(text("SELECT count(*) FROM mod_forms.forms")) == 0
        assert (
            conn.scalar(text("SELECT count(*) FROM mod_workflow.workflow_executions"))
            == 0
        )
    app.dispose()
    admin.dispose()
