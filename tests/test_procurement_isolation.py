"""Real-PostgreSQL RLS and immutability canaries for Procurement."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.money import Money, currency
from dotmac_kernel.namespaces import NamespaceRegistry
from dotmac_procurement.contracts import CreateRequisition, RequisitionLineInput
from dotmac_procurement.manifest import module
from dotmac_procurement.models import (
    ALL_MODELS,
    ProcurementEvidence,
    PurchaseRequisition,
    PurchaseRequisitionLine,
)
from dotmac_procurement.service import create_requisition, submit_requisition
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
PROCUREMENT_VERSIONS = (
    REPO_ROOT / "packages/dotmac-procurement/src/dotmac_procurement/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — procurement RLS needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_procurement() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"procurement_rls_{uuid.uuid4().hex[:12]}"
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
            "version_locations", f"{KERNEL_VERSIONS} {PROCUREMENT_VERSIONS}"
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


def _command(number: str) -> CreateRequisition:
    return CreateRequisition(
        requisition_number=number,
        requested_on=date(2026, 8, 18),
        requester_ref="people:employee:ada",
        created_by_ref="identity:user:ada",
        currency_code="NGN",
        lines=(
            RequisitionLineInput(
                description="Fibre cable",
                quantity=Decimal("100"),
                unit="m",
                estimated_unit_cost=Money.of("150", currency("NGN")),
            ),
        ),
    )


def _tenant_session(app_url: str, tenant_id: uuid.UUID) -> Session:
    db = Session(create_engine(app_url))
    db.execute(
        text("SELECT set_config('app.current_tenant', :tenant, false)"),
        {"tenant": str(tenant_id)},
    )
    return db


def test_live_catalog_proves_the_declared_forced_rls_contract(
    migrated_procurement: tuple[str, str],
) -> None:
    admin_url, _ = migrated_procurement
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert (
            audit_live_schemas(conn, NamespaceRegistry.from_manifests([module])) == ()
        )
        facts = list(
            conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='mod_procurement' AND c.relkind='r'"
                )
            )
        )
    engine.dispose()
    assert {row[0] for row in facts} == set(module.tables)
    assert all(row[1] and row[2] for row in facts)


def test_online_role_cannot_read_or_write_another_tenant(
    migrated_procurement: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_procurement
    tenant_a, tenant_b = _seed_tenants(admin_url)
    with _tenant_session(app_url, tenant_a) as db:
        created = create_requisition(
            db,
            scope=TenantScope(tenant_a),
            command=_command("REQ-001"),
            recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
        )
        assert created.tenant_id == tenant_a
        db.commit()
    with _tenant_session(app_url, tenant_b) as db:
        for model in ALL_MODELS:
            assert db.scalar(select(func.count()).select_from(model)) == 0
        with pytest.raises(DBAPIError):
            create_requisition(
                db,
                scope=TenantScope(tenant_a),
                command=_command("FORGED"),
                recorded_at=datetime(2026, 8, 19, tzinfo=UTC),
            )


def test_online_role_has_no_platform_plane_privileges(
    migrated_procurement: tuple[str, str],
) -> None:
    admin_url, _ = migrated_procurement
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema='mod_procurement'"
            )
        ) == len(module.tables)
        assert conn.scalar(
            text(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema='mod_procurement' "
                "AND column_name='tenant_id'"
            )
        ) == len(module.tables)
    engine.dispose()


def test_database_refuses_snapshot_and_evidence_mutation(
    migrated_procurement: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_procurement
    tenant_id, _ = _seed_tenants(admin_url)
    recorded_at = datetime(2026, 8, 19, tzinfo=UTC)
    with _tenant_session(app_url, tenant_id) as db:
        requisition = create_requisition(
            db,
            scope=TenantScope(tenant_id),
            command=_command("REQ-IMMUTABLE"),
            recorded_at=recorded_at,
        )
        submit_requisition(
            db,
            scope=TenantScope(tenant_id),
            requisition_id=requisition.id,
            submitted_at=recorded_at,
            submitted_by_ref="identity:user:ada",
        )
        requisition_id = requisition.id
        requisition_line_id = db.scalar(
            select(PurchaseRequisitionLine.id).where(
                PurchaseRequisitionLine.requisition_id == requisition.id
            )
        )
        assert requisition_line_id is not None
        draft_requisition = create_requisition(
            db,
            scope=TenantScope(tenant_id),
            command=_command("REQ-DRAFT-TARGET"),
            recorded_at=recorded_at,
        )
        draft_requisition_id = draft_requisition.id
        db.commit()

    with _tenant_session(app_url, tenant_id) as db:
        requisition = db.get(PurchaseRequisition, requisition_id)
        assert requisition is not None
        requisition.total_estimated_amount = Decimal("1")
        with pytest.raises(DBAPIError, match="submitted requisition content"):
            db.flush()

    with _tenant_session(app_url, tenant_id) as db:
        line = db.get(PurchaseRequisitionLine, requisition_line_id)
        assert line is not None
        line.requisition_id = draft_requisition_id
        with pytest.raises(DBAPIError, match="submitted requisition lines"):
            db.flush()

    with _tenant_session(app_url, tenant_id) as db:
        evidence = db.scalar(select(ProcurementEvidence))
        assert evidence is not None
        evidence.details_json = "{}"
        with pytest.raises(DBAPIError, match="procurement evidence is append-only"):
            db.flush()
