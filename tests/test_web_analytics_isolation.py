"""PostgreSQL canaries for tenancy, property scope and append-only history.

The reference assembly builds but does not install this optional module, so the
fixture composes its lineage into a disposable database. SQLite is deliberately
not accepted as evidence for RLS or database-role grants.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_web_analytics import (
    CORE_EVENT_DECLARATIONS,
    PAGE_VIEW_EVENT_CODE,
    CollectionAdmissionEvidence,
    CollectionDecision,
    ConsentState,
    EventDeclarationRegistry,
    OpaqueVisitorToken,
    PageEvidence,
    PrivacyPolicyEvidence,
    PropertyRegistration,
    RecordEventCommand,
    StreamRegistration,
    TransportKind,
    TransportProvenance,
)
from dotmac_web_analytics.models import EventObservation
from dotmac_web_analytics.service import (
    record_event,
    register_property,
    register_stream,
)
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
ASSEMBLY_VERSIONS = ROOT / "alembic/versions"
ANALYTICS_VERSIONS = (
    ROOT / "packages/dotmac-web-analytics/src/dotmac_web_analytics/migrations/versions"
)
NOW = datetime(2026, 8, 18, 12, tzinfo=UTC)
REGISTRY = EventDeclarationRegistry(CORE_EVENT_DECLARATIONS)


class _Pseudonymizer:
    key_version = 1

    def digest(
        self,
        *,
        tenant_id: uuid.UUID,
        property_id: uuid.UUID,
        token: OpaqueVisitorToken,
    ) -> str:
        value = f"{tenant_id}:{property_id}:{token.reveal_for_pseudonymization()}"
        return "sha256:" + hashlib.sha256(value.encode()).hexdigest()


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — web analytics RLS needs Postgres")
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
    name = f"webanalytics_rls_{uuid.uuid4().hex[:10]}"
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

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {ANALYTICS_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
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
        for tenant, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
            connection.execute(
                text(
                    "INSERT INTO public.tenants (id, slug, name) "
                    "VALUES (:id, :slug, :name)"
                ),
                {"id": tenant, "slug": slug, "name": slug.title()},
            )
    engine.dispose()
    return tenant_a, tenant_b


def _command(
    tenant_id: uuid.UUID, property_code: str, event_id: str
) -> RecordEventCommand:
    origin = f"https://{property_code}.invalid"
    return RecordEventCommand(
        tenant_id=tenant_id,
        property_code=property_code,
        stream_code="browser",
        protocol_version=1,
        event_id=event_id,
        event_code=PAGE_VIEW_EVENT_CODE,
        event_schema_version=1,
        occurred_at=NOW,
        visitor_token=OpaqueVisitorToken("opaque-visitor-token-0001"),
        privacy=PrivacyPolicyEvidence(
            "privacy-1",
            ConsentState.GRANTED,
            CollectionDecision.ALLOW,
            False,
            False,
            NOW,
        ),
        admission=CollectionAdmissionEvidence("web.collect", origin, NOW, True, True),
        provenance=TransportProvenance(TransportKind.LOCAL, "local.website", event_id),
        page=PageEvidence(f"{origin}/path"),
    )


def _create_property_and_event(
    url: str, tenant_id: uuid.UUID, property_code: str, event_id: str
) -> uuid.UUID:
    engine = create_engine(url)
    with Session(engine) as db, db.begin():
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        prop = register_property(
            db,
            PropertyRegistration(
                tenant_id,
                property_code,
                property_code,
                (f"https://{property_code}.invalid",),
                "Africa/Lagos",
                30,
                60,
            ),
        )
        register_stream(
            db,
            StreamRegistration(tenant_id, property_code, "browser", (1,)),
        )
        result = record_event(
            db,
            registry=REGISTRY,
            pseudonymizer=_Pseudonymizer(),
            command=_command(tenant_id, property_code, event_id),
            received_at=NOW,
        )
        assert result.observation_id is not None
        return prop.id


def test_live_catalog_contract_proves_every_declared_tenant_table(
    migrated_scratch: tuple[str, str],
) -> None:
    from dotmac_kernel.migrations.catalog import audit_live_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry
    from dotmac_web_analytics.manifest import module

    admin_url, _ = migrated_scratch
    engine = create_engine(admin_url)
    with engine.connect() as connection:
        assert (
            audit_live_schemas(connection, NamespaceRegistry.from_manifests([module]))
            == ()
        )
    engine.dispose()


def test_online_role_sees_only_current_tenant_and_requested_property(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant_a, tenant_b = _seed_tenants(admin_url)
    property_a = _create_property_and_event(app_user_url, tenant_a, "site.a", "a-1")
    _create_property_and_event(app_user_url, tenant_b, "site.b", "b-1")

    engine = create_engine(app_user_url)
    with Session(engine) as db, db.begin():
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_a)},
        )
        rows = db.scalars(select(EventObservation)).all()
        assert len(rows) == 1
        assert rows[0].tenant_id == tenant_a
        assert rows[0].property_id == property_a
    engine.dispose()


def test_online_role_cannot_update_or_delete_observation_history(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant, _ = _seed_tenants(admin_url)
    _create_property_and_event(app_user_url, tenant, "site.a", "a-1")
    engine = create_engine(app_user_url)

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE mod_webanalytics.event_observations "
                    "SET canonical_path = '/rewritten'"
                )
            )
        transaction.rollback()

    with engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        with pytest.raises(DBAPIError):
            connection.execute(text("DELETE FROM mod_webanalytics.event_observations"))
        transaction.rollback()
    engine.dispose()


def test_event_identity_uniqueness_is_property_and_stream_scoped(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_scratch
    tenant, _ = _seed_tenants(admin_url)
    _create_property_and_event(app_user_url, tenant, "site.a", "shared")
    _create_property_and_event(app_user_url, tenant, "site.b", "shared")

    engine = create_engine(app_user_url)
    with Session(engine) as db, db.begin():
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        rows = db.scalars(
            select(EventObservation).where(EventObservation.event_id == "shared")
        ).all()
        assert len(rows) == 2
        assert len({row.property_id for row in rows}) == 2
    engine.dispose()
