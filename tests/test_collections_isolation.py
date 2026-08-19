"""PostgreSQL RLS, immutability and concurrency proofs for Collections."""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_collections.contracts import (
    AssessCollectionExposureV1,
    TriggerProvenanceV1,
)
from dotmac_collections.manifest import module
from dotmac_collections.models import TENANT_TABLES, CollectionCase
from dotmac_collections.policies import (
    PolicyPublicationV1,
    PolicyStepDraftV1,
    PolicyVersionDraftV1,
)
from dotmac_collections.receivables import (
    FakeReceivablesReader,
    PositionReadOk,
    ReceivablePositionV1,
)
from dotmac_collections.service import (
    CaseAssessed,
    CollectionCaseService,
    CollectionPolicyService,
    CreateCollectionPolicyV1,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.money import Currency, Money
from dotmac_kernel.namespaces import NamespaceRegistry
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
COLLECTIONS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-collections/src/dotmac_collections/migrations/versions"
)
NOW = datetime(2026, 8, 19, 12, tzinfo=UTC)
NGN = Currency("NGN", 2)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — Collections proofs need PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture(scope="module")
def scratch() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"collections_{uuid.uuid4().hex[:12]}"
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
    old_migration_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {COLLECTIONS_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        if old_migration_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = old_migration_url
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
    first, second = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as connection:
            for tenant_id in (first, second):
                connection.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {
                        "id": tenant_id,
                        "slug": f"collections-{tenant_id.hex[:8]}",
                        "name": f"Collections {tenant_id.hex[:8]}",
                    },
                )
    finally:
        engine.dispose()
    return first, second


def _tenant_engine(url: str, tenant_id: uuid.UUID) -> Engine:
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _set_tenant(dbapi_connection, _record) -> None:
        with dbapi_connection.cursor() as cursor:
            cursor.execute(
                "SELECT set_config('app.current_tenant', %s, false)",
                (str(tenant_id),),
            )

    return engine


@contextmanager
def _tenant_session(url: str, tenant_id: uuid.UUID) -> Iterator[Session]:
    engine = _tenant_engine(url, tenant_id)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def _publish_policy(db: Session, scope: TenantScope) -> uuid.UUID:
    policy_id = uuid.uuid4()
    code = f"arrears_{policy_id.hex}"
    CollectionPolicyService.create(
        db,
        CreateCollectionPolicyV1(
            policy_id=policy_id,
            scope=scope,
            policy_code=code,
            description="Concurrent Collections canary",
        ),
    )
    version_id = uuid.uuid4()
    CollectionPolicyService.publish(
        db,
        scope=scope,
        draft=PolicyVersionDraftV1(
            policy_code=code,
            reason_code="invoice_overdue",
            collection_timing="arrears",
            grace=None,
            steps=(
                PolicyStepDraftV1(
                    code="notice",
                    ordinal=1,
                    offset=timedelta(0),
                    offset_anchor="exposure_at",
                    request_kind="notice",
                    action_code=None,
                    receipt_required=True,
                ),
            ),
        ),
        publication=PolicyPublicationV1(
            policy_version_id=version_id,
            version=1,
            effective_from=NOW,
            actor_ref="service:collections-canary",
            reason="PostgreSQL proof",
            published_at=NOW,
        ),
    )
    return version_id


def _position(
    scope: TenantScope,
    *,
    source_version: int = 1,
    amount: str = "100.00",
    resolution: str = "open",
) -> ReceivablePositionV1:
    return ReceivablePositionV1(
        scope=scope,
        source_owner="billing.receivables",
        exposure_ref="invoice:concurrency",
        source_version=source_version,
        state_fingerprint=f"sha256:position-{source_version}-{amount}",
        subject_ref="subscriber:concurrency",
        service_ref="service:concurrency",
        collection_timing="arrears",
        reason_code="invoice_overdue",
        collectible_receivable=Money.of(amount, NGN),
        available_credit=Money.zero(NGN),
        funding_available=Money.zero(NGN),
        due_at=NOW - timedelta(days=1),
        coverage_start_at=None,
        resolution=resolution,  # type: ignore[arg-type]
        authority="authoritative",
        completeness="complete",
        observed_at=NOW,
    )


def _reader(position: ReceivablePositionV1) -> FakeReceivablesReader:
    reader = FakeReceivablesReader()
    reader.set_result(
        scope=position.scope,
        source_owner=position.source_owner,
        exposure_ref=position.exposure_ref,
        result=PositionReadOk(position),
    )
    return reader


def _command(scope: TenantScope, ordinal: int) -> AssessCollectionExposureV1:
    command_id = uuid.uuid4()
    return AssessCollectionExposureV1(
        command_id=command_id,
        idempotency_key=f"assessment:{ordinal}:{command_id}",
        correlation_id=uuid.uuid4(),
        causal_event_id=f"billing:{ordinal}:{command_id}",
        scope=scope,
        source_owner="billing.receivables",
        exposure_ref="invoice:concurrency",
        subject_ref="subscriber:concurrency",
        service_ref="service:concurrency",
        collection_timing="arrears",
        reason_code="invoice_overdue",
        trigger=TriggerProvenanceV1(
            kind="receivable_changed",
            trigger_id=f"trigger:{ordinal}:{command_id}",
            triggered_at=NOW,
        ),
    )


def test_catalog_contract_has_force_rls_and_exact_declared_tables(
    scratch: tuple[str, str],
) -> None:
    admin_url, _ = scratch
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            assert (
                audit_live_schemas(
                    connection, NamespaceRegistry.from_manifests([module])
                )
                == ()
            )
            rows = connection.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'mod_coll' AND c.relkind = 'r'"
                )
            )
            actual = {
                row.relname: (row.relrowsecurity, row.relforcerowsecurity)
                for row in rows
            }
            assert set(actual) == set(TENANT_TABLES)
            assert all(enabled and forced for enabled, forced in actual.values())
    finally:
        engine.dispose()


def test_rls_hides_and_refuses_cross_tenant_policy_rows(
    scratch: tuple[str, str],
) -> None:
    admin_url, app_url = scratch
    first, second = _seed_tenants(admin_url)
    admin = create_engine(admin_url)
    row_ids = (uuid.uuid4(), uuid.uuid4())
    try:
        with admin.begin() as connection:
            for row_id, tenant_id in zip(row_ids, (first, second), strict=True):
                connection.execute(
                    text(
                        "INSERT INTO mod_coll.collection_policies "
                        "(id, tenant_id, policy_code, description) "
                        "VALUES (:id, :tenant, :code, 'RLS proof')"
                    ),
                    {"id": row_id, "tenant": tenant_id, "code": f"p-{row_id}"},
                )
    finally:
        admin.dispose()
    with _tenant_session(app_url, first) as session:
        visible = session.scalars(
            text("SELECT tenant_id FROM mod_coll.collection_policies")
        ).all()
        assert visible == [first]
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "INSERT INTO mod_coll.collection_policies "
                    "(id, tenant_id, policy_code, description) "
                    "VALUES (:id, :tenant, 'cross-tenant', 'refused')"
                ),
                {"id": uuid.uuid4(), "tenant": second},
            )


def test_concurrent_first_assessments_create_one_live_case(
    scratch: tuple[str, str],
) -> None:
    admin_url, app_url = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        policy_version_id = _publish_policy(session, scope)
        session.commit()
    barrier = threading.Barrier(2)

    def assess(ordinal: int) -> CaseAssessed:
        with _tenant_session(app_url, tenant_id) as session:
            barrier.wait(timeout=10)
            result = CollectionCaseService.assess(
                session,
                command=_command(scope, ordinal),
                policy_version_id=policy_version_id,
                reader=_reader(_position(scope)),
                assessed_at=NOW,
            )
            session.commit()
            assert isinstance(result, CaseAssessed)
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(assess, (1, 2)))
    assert len({result.case_id for result in results}) == 1
    assert sorted(result.opened for result in results) == [False, True]
    with _tenant_session(app_url, tenant_id) as session:
        assert len(session.scalars(select(CollectionCase)).all()) == 1


def test_published_policy_is_immutable_and_resolved_case_can_reopen_fresh(
    scratch: tuple[str, str],
) -> None:
    admin_url, app_url = scratch
    tenant_id, _ = _seed_tenants(admin_url)
    scope = TenantScope(tenant_id)
    with _tenant_session(app_url, tenant_id) as session:
        policy_version_id = _publish_policy(session, scope)
        first = CollectionCaseService.assess(
            session,
            command=_command(scope, 1),
            policy_version_id=policy_version_id,
            reader=_reader(_position(scope)),
            assessed_at=NOW,
        )
        assert isinstance(first, CaseAssessed)
        closed = CollectionCaseService.assess(
            session,
            command=_command(scope, 2),
            policy_version_id=policy_version_id,
            reader=_reader(
                _position(scope, source_version=2, amount="0.00", resolution="resolved")
            ),
            assessed_at=NOW + timedelta(hours=1),
        )
        assert isinstance(closed, CaseAssessed)
        reopened = CollectionCaseService.assess(
            session,
            command=_command(scope, 3),
            policy_version_id=policy_version_id,
            reader=_reader(_position(scope, source_version=3, amount="50.00")),
            assessed_at=NOW + timedelta(hours=2),
        )
        assert isinstance(reopened, CaseAssessed)
        assert reopened.case_id != first.case_id
        session.commit()

        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "UPDATE mod_coll.collection_policy_versions "
                    "SET publication_reason = 'rewritten' WHERE id = :id"
                ),
                {"id": policy_version_id},
            )
