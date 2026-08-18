"""Live PostgreSQL proofs for the uncomposed media-observations lineage.

The Starter assembly deliberately does not adopt this module. This file creates
one disposable database, composes the kernel + assembly + module lineages there,
and drives the online role for RLS. It also proves database append-only grants /
triggers, concurrent duplicate ingestion, exact money and period overlap on the
engine that enforces them.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from dotmac_media_observations import (
    CountValue,
    EntityDisposition,
    EntityObservation,
    ExactMoney,
    MetricDefinitionDeclaration,
    MetricObservation,
    MetricSemantic,
    MetricValueType,
    NodeTypeDeclaration,
    ObservationConflict,
    ObservationSource,
    declare_metric,
    declare_node_type,
    record_entity,
    record_metric,
)
from dotmac_media_observations.models import APPEND_ONLY_TABLES, TENANT_TABLES
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
ASSEMBLY_VERSIONS = ROOT / "alembic/versions"
MEDIA_VERSIONS = (
    ROOT
    / "packages/dotmac-media-observations/src/dotmac_media_observations"
    / "migrations/versions"
)
T0 = datetime(2026, 8, 18, 8, tzinfo=UTC)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — media RLS needs PostgreSQL")
    return url


def _url_for(base_url: str, database: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{database}"


@pytest.fixture(scope="module")
def migrated_media_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"mediaobs_{uuid.uuid4().hex[:12]}"
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
    previous = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {MEDIA_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        if previous is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous
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
            for tenant_id, slug in ((first, "media-a"), (second, "media-b")):
                connection.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {
                        "id": tenant_id,
                        "slug": f"{slug}-{tenant_id.hex[:6]}",
                        "name": slug,
                    },
                )
    finally:
        engine.dispose()
    return first, second


def _source(
    tenant_id: uuid.UUID,
    identity: str,
    *,
    receipt: str | None = None,
    observed_at: datetime = T0,
) -> ObservationSource:
    return ObservationSource(
        tenant_id=tenant_id,
        installation_ref="install-1",
        source_system="external-media",
        source_observation_id=identity,
        observed_at=observed_at,
        received_at=observed_at + timedelta(minutes=1),
        transport_receipt_ref=receipt or f"receipt-{identity}",
        normalization_version=1,
    )


def _declare_base(session: Session, tenant_id: uuid.UUID) -> None:
    declare_node_type(
        session,
        NodeTypeDeclaration(
            tenant_id=tenant_id,
            code="campaign",
            version=1,
            label="Campaign",
            traits={"aggregate": True},
            declared_by="postgres-canary",
            declared_at=T0,
        ),
    )
    declare_metric(
        session,
        MetricDefinitionDeclaration(
            tenant_id=tenant_id,
            code="reported_spend",
            version=1,
            label="Reported spend",
            value_type=MetricValueType.MONEY,
            unit="currency",
            semantic=MetricSemantic.SPEND,
            declared_by="postgres-canary",
            declared_at=T0,
        ),
    )


def _record_one(session: Session, tenant_id: uuid.UUID, identity: str) -> uuid.UUID:
    result = record_entity(
        session,
        EntityObservation(
            source=_source(tenant_id, identity),
            external_account_ref="account-1",
            entity_ref=f"entity-{identity}",
            node_code="campaign",
            node_version=1,
            name=identity,
            state="enabled",
            disposition=EntityDisposition.PRESENT,
        ),
    )
    return result.observation_id


def test_every_table_exists_with_forced_rls(
    migrated_media_database: tuple[str, str],
) -> None:
    admin_url, _ = migrated_media_database
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            found = set(
                connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname = 'mod_mediaobs'"
                    )
                ).scalars()
            )
            assert found == set(TENANT_TABLES)
            security = connection.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='mod_mediaobs' AND c.relkind='r'"
                )
            ).all()
            assert security
            assert all(enabled and forced for _, enabled, forced in security), security
    finally:
        engine.dispose()


def test_online_role_sees_only_its_tenant(
    migrated_media_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = migrated_media_database
    tenant_a, tenant_b = _seed_tenants(admin_url)
    with Session(create_engine(admin_url)) as session:
        for tenant_id in (tenant_a, tenant_b):
            _declare_base(session, tenant_id)
            _record_one(session, tenant_id, tenant_id.hex[:8])
        session.commit()

    engine = create_engine(app_user_url)
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            visible = (
                connection.execute(
                    text("SELECT tenant_id FROM mod_mediaobs.observations")
                )
                .scalars()
                .all()
            )
            assert visible == [tenant_a]

            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_b)},
            )
            visible = (
                connection.execute(
                    text("SELECT tenant_id FROM mod_mediaobs.observations")
                )
                .scalars()
                .all()
            )
            assert visible == [tenant_b]
    finally:
        engine.dispose()


def test_append_only_grants_and_trigger_refuse_mutation_for_admin(
    migrated_media_database: tuple[str, str],
) -> None:
    admin_url, _ = migrated_media_database
    tenant_id, _ = _seed_tenants(admin_url)
    with Session(create_engine(admin_url)) as session:
        _declare_base(session, tenant_id)
        observation_id = _record_one(session, tenant_id, "immutable")
        session.commit()

    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            triggers = set(
                connection.execute(
                    text(
                        "SELECT event_object_table FROM information_schema.triggers "
                        "WHERE trigger_schema='mod_mediaobs' "
                        "AND trigger_name LIKE '%_append_only'"
                    )
                ).scalars()
            )
            assert triggers == {table.name for table in APPEND_ONLY_TABLES}

            for table in APPEND_ONLY_TABLES:
                for role in ("app_user", "platform_api"):
                    assert connection.execute(
                        text(
                            "SELECT has_table_privilege(:role, :table, 'SELECT') "
                            "AND has_table_privilege(:role, :table, 'INSERT')"
                        ),
                        {"role": role, "table": table.fullname},
                    ).scalar_one()
                    assert not connection.execute(
                        text(
                            "SELECT has_table_privilege(:role, :table, 'UPDATE') "
                            "OR has_table_privilege(:role, :table, 'DELETE')"
                        ),
                        {"role": role, "table": table.fullname},
                    ).scalar_one()

        for statement in (
            "UPDATE mod_mediaobs.observations SET source_system='changed' "
            "WHERE id=:id",
            "DELETE FROM mod_mediaobs.observations WHERE id=:id",
        ):
            with engine.connect() as connection, pytest.raises(DBAPIError):
                connection.execute(text(statement), {"id": observation_id})
    finally:
        engine.dispose()


def test_concurrent_duplicate_ingest_returns_one_fact_and_two_receipts(
    migrated_media_database: tuple[str, str],
) -> None:
    admin_url, _ = migrated_media_database
    tenant_id, _ = _seed_tenants(admin_url)
    engine = create_engine(admin_url)
    with Session(engine) as session:
        _declare_base(session, tenant_id)
        session.commit()

    barrier = threading.Barrier(2)

    def ingest(receipt: str) -> uuid.UUID:
        with Session(engine) as session:
            command = EntityObservation(
                source=_source(
                    tenant_id,
                    "concurrent-identity",
                    receipt=receipt,
                ),
                external_account_ref="account-1",
                entity_ref="concurrent-entity",
                node_code="campaign",
                node_version=1,
                name="same content",
                state="enabled",
                disposition=EntityDisposition.PRESENT,
            )
            barrier.wait()
            result = record_entity(session, command)
            session.commit()
            return result.observation_id

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(ingest, ("concurrent-a", "concurrent-b")))
        assert len(set(results)) == 1
        with engine.connect() as connection:
            observation_count = connection.execute(
                text(
                    "SELECT count(*) FROM mod_mediaobs.observations "
                    "WHERE tenant_id=:tenant AND source_observation_id='concurrent-identity'"
                ),
                {"tenant": tenant_id},
            ).scalar_one()
            receipt_count = connection.execute(
                text(
                    "SELECT count(*) FROM mod_mediaobs.observation_receipts r "
                    "JOIN mod_mediaobs.observations o "
                    "ON o.tenant_id=r.tenant_id AND o.id=r.observation_id "
                    "WHERE o.tenant_id=:tenant "
                    "AND o.source_observation_id='concurrent-identity'"
                ),
                {"tenant": tenant_id},
            ).scalar_one()
        assert observation_count == 1
        assert receipt_count == 2
    finally:
        engine.dispose()


def test_exact_money_round_trips_with_minor_unit_provenance(
    migrated_media_database: tuple[str, str],
) -> None:
    admin_url, _ = migrated_media_database
    tenant_id, _ = _seed_tenants(admin_url)
    engine = create_engine(admin_url)
    with Session(engine) as session:
        _declare_base(session, tenant_id)
        _record_one(session, tenant_id, "money-entity")
        result = record_metric(
            session,
            MetricObservation(
                source=_source(tenant_id, "money-metric"),
                external_account_ref="account-1",
                entity_ref="entity-money-entity",
                metric_code="reported_spend",
                metric_version=1,
                period_start=T0,
                period_end=T0 + timedelta(days=1),
                value=ExactMoney(
                    amount=Decimal("50000.00"), currency="NGN", minor_unit=2
                ),
            ),
        )
        session.commit()
    try:
        with engine.connect() as connection:
            amount, minor_units, currency, minor_unit = connection.execute(
                text(
                    "SELECT money_amount, money_minor_units, money_currency, "
                    "money_minor_unit FROM mod_mediaobs.metric_observations "
                    "WHERE tenant_id=:tenant AND observation_id=:observation"
                ),
                {"tenant": tenant_id, "observation": result.observation_id},
            ).one()
        assert amount == Decimal("50000.000000000000000000")
        assert minor_units == 5_000_000
        assert currency == "NGN"
        assert minor_unit == 2

        invalid_observation = uuid.uuid4()
        with pytest.raises(DBAPIError), engine.begin() as connection:
            period_id = connection.execute(
                text(
                    "SELECT id FROM mod_mediaobs.metric_periods "
                    "WHERE tenant_id=:tenant LIMIT 1"
                ),
                {"tenant": tenant_id},
            ).scalar_one()
            connection.execute(
                text(
                    "INSERT INTO mod_mediaobs.observations ("
                    "id, tenant_id, installation_ref, source_system, "
                    "source_observation_id, kind, content_fingerprint, "
                    "source_observed_at, received_at, normalization_version, "
                    "restatement_depth) VALUES ("
                    ":id, :tenant, 'install-1', 'external-media', "
                    "'invalid-money', 'metric', :fingerprint, :observed, "
                    ":received, 1, 0)"
                ),
                {
                    "id": invalid_observation,
                    "tenant": tenant_id,
                    "fingerprint": "0" * 64,
                    "observed": T0,
                    "received": T0 + timedelta(minutes=1),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO mod_mediaobs.metric_observations ("
                    "id, tenant_id, observation_id, period_id, value_type, "
                    "money_amount, money_minor_units, money_currency, "
                    "money_minor_unit, claim_status) VALUES ("
                    ":id, :tenant, :observation, :period, 'money', "
                    "1.00, 99, 'NGN', 2, 'provider_reported')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_id,
                    "observation": invalid_observation,
                    "period": period_id,
                },
            )
    finally:
        engine.dispose()


def test_postgres_refuses_overlapping_periods_and_accepts_adjacency(
    migrated_media_database: tuple[str, str],
) -> None:
    admin_url, _ = migrated_media_database
    tenant_id, _ = _seed_tenants(admin_url)
    engine = create_engine(admin_url)
    with Session(engine) as session:
        _declare_base(session, tenant_id)
        _record_one(session, tenant_id, "period-entity")
        base = MetricObservation(
            source=_source(tenant_id, "period-1"),
            external_account_ref="account-1",
            entity_ref="entity-period-entity",
            metric_code="reported_spend",
            metric_version=1,
            period_start=T0,
            period_end=T0 + timedelta(days=1),
            value=ExactMoney(Decimal("1.00"), "NGN", 2),
        )
        record_metric(session, base)
        record_metric(
            session,
            MetricObservation(
                source=_source(
                    tenant_id, "period-2", observed_at=T0 + timedelta(days=1)
                ),
                external_account_ref="account-1",
                entity_ref="entity-period-entity",
                metric_code="reported_spend",
                metric_version=1,
                period_start=T0 + timedelta(days=1),
                period_end=T0 + timedelta(days=2),
                value=ExactMoney(Decimal("2.00"), "NGN", 2),
            ),
        )
        with pytest.raises(ObservationConflict, match="overlap"):
            record_metric(
                session,
                MetricObservation(
                    source=_source(tenant_id, "period-overlap"),
                    external_account_ref="account-1",
                    entity_ref="entity-period-entity",
                    metric_code="reported_spend",
                    metric_version=1,
                    period_start=T0 + timedelta(hours=12),
                    period_end=T0 + timedelta(days=1, hours=12),
                    value=ExactMoney(Decimal("3.00"), "NGN", 2),
                ),
            )
    engine.dispose()


def test_database_count_column_is_integral_not_float(
    migrated_media_database: tuple[str, str],
) -> None:
    admin_url, _ = migrated_media_database
    engine = create_engine(admin_url)
    try:
        with engine.connect() as connection:
            data_type = connection.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema='mod_mediaobs' "
                    "AND table_name='metric_observations' "
                    "AND column_name='count_value'"
                )
            ).scalar_one()
        assert data_type == "bigint"
        assert CountValue(1).value == 1
    finally:
        engine.dispose()
