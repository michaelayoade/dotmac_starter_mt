"""Real-Postgres RLS canaries for Documents and Records.

The reference assembly builds but does not compose either optional owner. This
test composes their independent lineages into one disposable database because
that is the promised product surface; it does not create cross-module FKs.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_documents import CreateLibrary, create_library
from dotmac_documents.manifest import module as documents_module
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_records import (
    CutoffRule,
    DefineRetentionScheduleVersion,
    FinalAction,
    define_retention_schedule_version,
)
from dotmac_records.manifest import module as records_module
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
DOCUMENTS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-documents/src/dotmac_documents/migrations/versions"
)
RECORDS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-records/src/dotmac_records/migrations/versions"
)
NOW = datetime(2026, 8, 19, 11, 0, tzinfo=UTC)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the RLS canary needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_scratch(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"documents_records_rls_{uuid.uuid4().hex[:10]}"
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
            f"{KERNEL_VERSIONS} {DOCUMENTS_VERSIONS} {RECORDS_VERSIONS}",
        )
        monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:name AND pid <> pg_backend_pid()"
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
                    "VALUES (:id,:slug,:name)"
                ),
                {"id": tenant_id, "slug": slug, "name": slug.title()},
            )
    engine.dispose()
    return tenant_a, tenant_b


def _tenant_session(app_url: str, tenant_id: uuid.UUID) -> Session:
    db = Session(create_engine(app_url))
    db.execute(
        text("SELECT set_config('app.current_tenant', :tenant, false)"),
        {"tenant": str(tenant_id)},
    )
    return db


def test_live_catalog_proves_both_independent_owner_contracts(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    registry = NamespaceRegistry.from_manifests([documents_module, records_module])
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert audit_live_schemas(conn, registry) == ()
    engine.dispose()


def test_online_role_cannot_read_or_forge_rows_across_tenants(
    migrated_scratch: tuple[str, str],
) -> None:
    from dotmac_documents.models import DocumentLibrary
    from dotmac_records.models import RetentionScheduleVersion

    admin_url, app_url = migrated_scratch
    tenant_a, tenant_b = _seed_tenants(admin_url)
    with _tenant_session(app_url, tenant_a) as db:
        create_library(
            db,
            tenant_id=tenant_a,
            command=CreateLibrary(code="controlled", name="Controlled"),
            actor_id=uuid.uuid4(),
            recorded_at=NOW,
        )
        define_retention_schedule_version(
            db,
            tenant_id=tenant_a,
            command=DefineRetentionScheduleVersion(
                schedule_code="GENERAL",
                version=1,
                trigger_event_type="artifact.closed.v1",
                duration_days=365,
                permanent=False,
                cutoff_rule=CutoffRule.EXACT_DATE,
                final_action=FinalAction.ARCHIVAL_REVIEW,
                disposition_approval_policy="records.review.v1",
                review_cadence_days=30,
                authority="Corporate policy",
                accountable_owner="records.office",
            ),
            actor_id=uuid.uuid4(),
            recorded_at=NOW,
        )
        db.commit()

    with _tenant_session(app_url, tenant_b) as db:
        assert db.scalar(select(func.count()).select_from(DocumentLibrary)) == 0
        assert (
            db.scalar(select(func.count()).select_from(RetentionScheduleVersion)) == 0
        )
        with pytest.raises(DBAPIError):
            create_library(
                db,
                tenant_id=tenant_a,
                command=CreateLibrary(code="forged", name="Forged"),
                actor_id=uuid.uuid4(),
                recorded_at=NOW,
            )
