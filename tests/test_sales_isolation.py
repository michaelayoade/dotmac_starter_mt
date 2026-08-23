"""Postgres isolation, immutability and acceptance canaries for sales."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from dotmac_sales import (
    AcceptQuoteCommand,
    AuthorQuoteCommand,
    CreateLeadCommand,
    CreatePipelineCommand,
    CreateStageCommand,
    QuoteLineDraft,
    QuoteStatus,
    QuoteTaxRateV1,
    QuoteTermsSnapshotV1,
    QuoteTermValueV1,
    SalesActorRef,
    SalesActorSnapshot,
    SalesSubjectRef,
    SalesSubjectSnapshot,
    accept_quote,
    author_quote,
    create_lead,
    create_pipeline,
    create_stage,
)
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
ASSEMBLY_VERSIONS = ROOT / "alembic/versions"
SALES_VERSIONS = ROOT / "packages/dotmac-sales/src/dotmac_sales/migrations/versions"
TABLES = (
    "pipelines",
    "pipeline_stages",
    "leads",
    "lead_origins",
    "quotes",
    "quote_lines",
    "quote_discount_revisions",
)
ACTOR = SalesActorRef("staff", "integration-actor")


class Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ActorPort:
    def require_actor(
        self, *, tenant_id: uuid.UUID, actor: SalesActorRef
    ) -> SalesActorSnapshot:
        del tenant_id
        assert actor == ACTOR
        return SalesActorSnapshot(actor, "Integration Actor")


class SubjectPort:
    def require_subject(
        self, *, tenant_id: uuid.UUID, subject: SalesSubjectRef
    ) -> SalesSubjectSnapshot:
        del tenant_id
        return SalesSubjectSnapshot(subject, "Integration Prospect")


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — sales RLS needs Postgres")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture(scope="module")
def migrated_sales() -> Iterator[tuple[str, str, str]]:
    superuser = _superuser_url()
    name = f"sales_rls_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    previous = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {SALES_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield (
            admin_url,
            _url_for(superuser, name, user="app_user"),
            _url_for(superuser, name, user="platform_api"),
        )
    finally:
        if previous is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous
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


def _tenant(admin_url: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    engine = create_engine(admin_url)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.tenants (id, slug, name) VALUES (:id, :slug, :name)"
            ),
            {"id": tenant_id, "slug": f"sales-{tenant_id.hex[:12]}", "name": "Sales"},
        )
    engine.dispose()
    return tenant_id


def _set_tenant(session: Session, tenant_id: uuid.UUID) -> None:
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(tenant_id)},
    )


def _quote(app_url: str, tenant_id: uuid.UUID) -> uuid.UUID:
    subject = SalesSubjectRef("party", f"party-{uuid.uuid4()}", "1")
    engine = create_engine(app_url)
    with Session(engine) as db, db.begin():
        _set_tenant(db, tenant_id)
        pipeline = create_pipeline(
            db, CreatePipelineCommand(tenant_id, f"Pipeline {uuid.uuid4()}")
        )
        stage = create_stage(
            db, CreateStageCommand(tenant_id, pipeline.id, "Qualified", 10, 70)
        )
        lead = create_lead(
            db,
            CreateLeadCommand(
                tenant_id,
                subject,
                "Integration lead",
                pipeline.id,
                stage.id,
                "NGN",
            ),
            subject_port=SubjectPort(),
        )
        quote = author_quote(
            db,
            AuthorQuoteCommand(
                tenant_id=tenant_id,
                command_id=uuid.uuid4(),
                quote_id=uuid.uuid4(),
                actor=ACTOR,
                lead_id=lead.id,
                status=QuoteStatus.DRAFT,
                currency="NGN",
                currency_minor_units=2,
                lines=(
                    QuoteLineDraft(
                        description="Internet",
                        quantity=Decimal("1"),
                        unit_price=Decimal("100"),
                        catalogue_ref="offer:internet:v1",
                        price_version_ref="price:internet:v1",
                        terms_ref="terms:internet:v1",
                        terms_snapshot=QuoteTermsSnapshotV1(
                            version_ref="terms:internet:v1",
                            values=(QuoteTermValueV1("term_months", "12"),),
                        ),
                        specification_ref="spec:internet:v1",
                        taxes=(
                            QuoteTaxRateV1(
                                tax_code="vat",
                                source_version="ng-vat:2026-01",
                                rate=Decimal("7.5"),
                            ),
                        ),
                    ),
                ),
                fulfillment_eligibility_requirement_refs=("settlement:quote",),
            ),
            actor_port=ActorPort(),
            subject_port=SubjectPort(),
            clock=Clock(),
        )
        quote_id = quote.id
    engine.dispose()
    return quote_id


def test_all_tables_have_forced_rls_and_tenant_policy(
    migrated_sales: tuple[str, str, str],
) -> None:
    admin_url, _, _ = migrated_sales
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        for table in TABLES:
            enabled, forced = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid = CAST(:table AS regclass)"
                ),
                {"table": f"mod_sales.{table}"},
            ).one()
            assert enabled and forced, table
            assert (
                conn.execute(
                    text(
                        "SELECT count(*) FROM pg_policies WHERE schemaname='mod_sales' "
                        "AND tablename=:table AND policyname=:policy"
                    ),
                    {"table": table, "policy": f"{table}_tenant_isolation"},
                ).scalar_one()
                == 1
            )
    engine.dispose()


def test_platform_role_has_no_sales_schema_or_table_access(
    migrated_sales: tuple[str, str, str],
) -> None:
    admin_url, _, _ = migrated_sales
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert not conn.execute(
            text("SELECT has_schema_privilege('platform_api','mod_sales','USAGE')")
        ).scalar_one()
        for table in TABLES:
            for privilege in (
                "SELECT",
                "INSERT",
                "UPDATE",
                "DELETE",
                "TRUNCATE",
                "REFERENCES",
                "TRIGGER",
            ):
                assert not conn.execute(
                    text(
                        "SELECT has_table_privilege('platform_api', "
                        "CAST(:table AS text), CAST(:privilege AS text))"
                    ),
                    {"table": f"mod_sales.{table}", "privilege": privilege},
                ).scalar_one()
    engine.dispose()


def test_live_schema_satisfies_kernel_catalog_contract(
    migrated_sales: tuple[str, str, str],
) -> None:
    from dotmac_kernel.migrations.catalog import audit_live_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry
    from dotmac_sales.manifest import module

    admin_url, _, _ = migrated_sales
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert (
            audit_live_schemas(conn, NamespaceRegistry.from_manifests([module])) == ()
        )
    engine.dispose()


def test_cross_tenant_quote_is_invisible_and_unwritable(
    migrated_sales: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = migrated_sales
    tenant_a, tenant_b = _tenant(admin_url), _tenant(admin_url)
    quote_a, quote_b = _quote(app_url, tenant_a), _quote(app_url, tenant_b)
    engine = create_engine(app_url)
    with Session(engine) as db, db.begin():
        _set_tenant(db, tenant_a)
        visible = set(db.execute(text("SELECT id FROM mod_sales.quotes")).scalars())
        assert quote_a in visible and quote_b not in visible
        assert (
            db.execute(
                text("UPDATE mod_sales.quotes SET notes='crossed' WHERE id=:id"),
                {"id": quote_b},
            ).rowcount
            == 0
        )
    engine.dispose()


def test_acceptance_is_exactly_once_and_stages_one_owner_output(
    migrated_sales: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = migrated_sales
    tenant_id = _tenant(admin_url)
    quote_id = _quote(app_url, tenant_id)
    command = AcceptQuoteCommand(tenant_id, uuid.uuid4(), quote_id, ACTOR)
    engine = create_engine(app_url)
    with Session(engine) as db, db.begin():
        _set_tenant(db, tenant_id)
        first = accept_quote(
            db,
            command,
            actor_port=ActorPort(),
            subject_port=SubjectPort(),
            clock=Clock(),
        )
        second = accept_quote(
            db,
            command,
            actor_port=ActorPort(),
            subject_port=SubjectPort(),
            clock=Clock(),
        )
        assert first.event_id == second.event_id
        assert second.replayed
    with Session(engine) as db, db.begin():
        _set_tenant(db, tenant_id)
        count = db.execute(
            text(
                "SELECT count(*) FROM public.outbox_events "
                "WHERE event_type='sales.accepted-quote.v1' AND id=:id"
            ),
            {"id": first.event_id},
        ).scalar_one()
        assert count == 1
    engine.dispose()


def test_concurrent_acceptance_converges_on_one_event(
    migrated_sales: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = migrated_sales
    tenant_id = _tenant(admin_url)
    quote_id = _quote(app_url, tenant_id)
    command_id = uuid.uuid4()

    def accept() -> uuid.UUID:
        engine = create_engine(app_url)
        try:
            with Session(engine) as db, db.begin():
                _set_tenant(db, tenant_id)
                return accept_quote(
                    db,
                    AcceptQuoteCommand(tenant_id, command_id, quote_id, ACTOR),
                    actor_port=ActorPort(),
                    subject_port=SubjectPort(),
                    clock=Clock(),
                ).event_id
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        event_ids = list(pool.map(lambda _: accept(), range(2)))
    assert event_ids[0] == event_ids[1]
    engine = create_engine(app_url)
    with Session(engine) as db, db.begin():
        _set_tenant(db, tenant_id)
        assert (
            db.execute(
                text(
                    "SELECT count(*) FROM public.outbox_events "
                    "WHERE event_type='sales.accepted-quote.v1' AND id=:id"
                ),
                {"id": event_ids[0]},
            ).scalar_one()
            == 1
        )
    engine.dispose()


def test_raw_sql_cannot_mutate_accepted_quote_or_lines(
    migrated_sales: tuple[str, str, str],
) -> None:
    admin_url, app_url, _ = migrated_sales
    tenant_id = _tenant(admin_url)
    quote_id = _quote(app_url, tenant_id)
    engine = create_engine(app_url)
    with Session(engine) as db, db.begin():
        _set_tenant(db, tenant_id)
        accept_quote(
            db,
            AcceptQuoteCommand(tenant_id, uuid.uuid4(), quote_id, ACTOR),
            actor_port=ActorPort(),
            subject_port=SubjectPort(),
            clock=Clock(),
        )
    with engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        with pytest.raises(DBAPIError, match="immutable"):
            conn.execute(
                text("UPDATE mod_sales.quotes SET notes='tampered' WHERE id=:id"),
                {"id": quote_id},
            )
        transaction.rollback()
    with engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant_id)},
        )
        with pytest.raises(DBAPIError, match="immutable"):
            conn.execute(
                text("DELETE FROM mod_sales.quote_lines WHERE quote_id=:id"),
                {"id": quote_id},
            )
        transaction.rollback()
    engine.dispose()
