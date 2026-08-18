"""Real-Postgres canaries for campaign isolation and failure races.

The reference assembly builds but does not compose campaigns. This file creates
a disposable database, composes only the kernel and campaigns lineages, and
drives the online role. SQLite is intentionally not accepted as RLS,
concurrency, or outbox-loss evidence.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

import pytest
from dotmac_campaigns import (
    AudienceBatch,
    AudienceCandidate,
    CampaignKind,
    CreateCampaign,
    DeliveryState,
    SequenceBlocked,
    SequenceStep,
    accept_due_work,
    create_campaign,
    ingest_audience,
    repair_missing_publications,
    report_drift,
    schedule_campaign,
)
from dotmac_campaigns.contracts import DueWorkTrigger, TimerIdentity
from dotmac_campaigns.fakes import FakeRenderer, FakeSenderResolver, FakeTimerPort
from dotmac_campaigns.manifest import module
from dotmac_campaigns.models import ALL_MODELS, CampaignDeliveryIntent
from dotmac_kernel.consent import register_marketing_categories, suppress
from dotmac_kernel.migrations.catalog import audit_live_schemas
from dotmac_kernel.namespaces import NamespaceRegistry
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
CAMPAIGNS_VERSIONS = (
    REPO_ROOT / "packages/dotmac-campaigns/src/dotmac_campaigns/migrations/versions"
)
NOW = datetime(2026, 8, 18, 8, 0, tzinfo=UTC)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — campaigns RLS needs Postgres")
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
    name = f"campaigns_rls_{uuid.uuid4().hex[:12]}"
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
            "version_locations", f"{KERNEL_VERSIONS} {CAMPAIGNS_VERSIONS}"
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


def _command(code: str = "onboarding") -> CreateCampaign:
    return CreateCampaign(
        code=code,
        name="Onboarding",
        kind=CampaignKind.NURTURE,
        channel="email",
        timezone="Africa/Lagos",
        scheduled_at=NOW,
        send_window_start=time(8),
        send_window_end=time(18),
        sender_key="growth",
        steps=(
            SequenceStep(
                position=0,
                delay=timedelta(0),
                template_slug="welcome",
                template_channel="email",
            ),
            SequenceStep(
                position=1,
                delay=timedelta(days=1),
                template_slug="follow-up",
                template_channel="email",
                advance_on=frozenset({DeliveryState.DELIVERED}),
            ),
        ),
        evidence_expires_at=NOW + timedelta(days=365),
        pii_expires_at=NOW + timedelta(days=30),
    )


def _audience() -> AudienceBatch:
    return AudienceBatch(
        source_owner="test.cohort",
        source_version="v1",
        source_fingerprint="a" * 64,
        eligibility_reason="fixture",
        candidates=(
            AudienceCandidate(
                source_subject_id="person-1",
                channel="email",
                address="person@example.com",
                context={"first_name": "Ada"},
                eligibility_reason="fixture",
            ),
        ),
    )


def _tenant_session(app_url: str, tenant_id: uuid.UUID) -> Session:
    session = Session(create_engine(app_url))
    session.execute(
        text("SELECT set_config('app.current_tenant', :tenant, false)"),
        {"tenant": str(tenant_id)},
    )
    register_marketing_categories("campaign")
    return session


def _seed_campaign(db: Session, tenant_id: uuid.UUID):
    campaign = create_campaign(
        db,
        tenant_id=tenant_id,
        command=_command(),
        idempotency_key="create",
        idempotency_expires_at=NOW + timedelta(days=7),
        recorded_at=NOW,
    )
    ingest_audience(
        db,
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        batch=_audience(),
        idempotency_key="audience",
        idempotency_expires_at=NOW + timedelta(days=7),
        evaluated_at=NOW,
    )
    return campaign


def test_live_catalog_proves_forced_rls_and_composite_tenant_contract(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, _ = migrated_scratch
    registry = NamespaceRegistry.from_manifests([module])
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        assert audit_live_schemas(conn, registry) == ()
        facts = list(
            conn.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='mod_campaigns' AND c.relkind='r'"
                )
            )
        )
    engine.dispose()
    assert {row[0] for row in facts} == set(module.tables)
    assert all(row[1] and row[2] for row in facts)


def test_online_role_cannot_read_or_write_across_tenants(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    tenant_a, tenant_b = _seed_tenants(admin_url)
    with _tenant_session(app_url, tenant_a) as db:
        campaign = _seed_campaign(db, tenant_a)
        db.commit()
    with _tenant_session(app_url, tenant_b) as db:
        for model in ALL_MODELS:
            assert db.scalar(select(func.count()).select_from(model)) == 0
        with pytest.raises(DBAPIError):
            create_campaign(
                db,
                tenant_id=tenant_a,
                command=_command("forged"),
                idempotency_key="forged",
                idempotency_expires_at=NOW + timedelta(days=7),
                recorded_at=NOW,
            )
    assert campaign.tenant_id == tenant_a


def test_concurrent_schedule_materializes_one_recipient_step(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    tenant, _ = _seed_tenants(admin_url)
    with _tenant_session(app_url, tenant) as db:
        campaign = _seed_campaign(db, tenant)
        db.commit()
        campaign_id = campaign.id

    def schedule(key: str) -> str:
        with _tenant_session(app_url, tenant) as db:
            try:
                schedule_campaign(
                    db,
                    tenant_id=tenant,
                    campaign_id=campaign_id,
                    timers=FakeTimerPort(),
                    idempotency_key=key,
                    idempotency_expires_at=NOW + timedelta(days=7),
                    recorded_at=NOW,
                )
                db.commit()
                return "scheduled"
            except Exception as exc:  # one serialized loser is expected
                db.rollback()
                return type(exc).__name__

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(schedule, ("schedule-a", "schedule-b")))
    assert outcomes.count("scheduled") == 1
    engine = create_engine(admin_url)
    with engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM mod_campaigns.campaign_recipient_steps "
                "WHERE campaign_id=:campaign"
            ),
            {"campaign": campaign_id},
        ).scalar_one()
    engine.dispose()
    assert count == 1


def test_changed_replay_conflicts_on_postgres(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    tenant, _ = _seed_tenants(admin_url)
    with _tenant_session(app_url, tenant) as db:
        first = create_campaign(
            db,
            tenant_id=tenant,
            command=_command(),
            idempotency_key="same-key",
            idempotency_expires_at=NOW + timedelta(days=7),
            recorded_at=NOW,
        )
        db.commit()
        replay = create_campaign(
            db,
            tenant_id=tenant,
            command=_command(),
            idempotency_key="same-key",
            idempotency_expires_at=NOW + timedelta(days=7),
            recorded_at=NOW,
        )
        assert replay.id == first.id
        with pytest.raises(Exception, match="different request"):
            create_campaign(
                db,
                tenant_id=tenant,
                command=CreateCampaign(
                    **{**_command().as_dict(), "name": "different"}
                ),
                idempotency_key="same-key",
                idempotency_expires_at=NOW + timedelta(days=7),
                recorded_at=NOW,
            )


def test_suppression_race_prevents_an_already_scheduled_intent(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    tenant, _ = _seed_tenants(admin_url)
    timers = FakeTimerPort()
    with _tenant_session(app_url, tenant) as db:
        campaign = _seed_campaign(db, tenant)
        schedule_campaign(
            db,
            tenant_id=tenant,
            campaign_id=campaign.id,
            timers=timers,
            idempotency_key="schedule",
            idempotency_expires_at=NOW + timedelta(days=7),
            recorded_at=NOW,
        )
        db.commit()
    with _tenant_session(app_url, tenant) as db:
        suppress(
            db,
            tenant,
            channel="email",
            address="person@example.com",
            reason="complaint",
        )
        db.commit()
    with _tenant_session(app_url, tenant) as db:
        result = accept_due_work(
            db,
            tenant_id=tenant,
            trigger=timers.only_current_trigger(),
            timers=timers,
            renderer=FakeRenderer(),
            senders=FakeSenderResolver(),
            idempotency_key="due",
            idempotency_expires_at=NOW + timedelta(days=7),
            accepted_at=NOW,
        )
        db.commit()
        assert result.status == "suppressed"
        assert db.execute(
            text("SELECT count(*) FROM public.outbox_events")
        ).scalar_one() == 0


def test_a_forged_delayed_step_cannot_skip_its_unresolved_predecessor(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    tenant, _ = _seed_tenants(admin_url)
    timers = FakeTimerPort()
    with _tenant_session(app_url, tenant) as db:
        campaign = _seed_campaign(db, tenant)
        schedule_campaign(
            db,
            tenant_id=tenant,
            campaign_id=campaign.id,
            timers=timers,
            idempotency_key="schedule",
            idempotency_expires_at=NOW + timedelta(days=7),
            recorded_at=NOW,
        )
        db.commit()
        initial = timers.only_current_trigger()
        forged = DueWorkTrigger(
            timer_id=uuid.uuid4(),
            identity=TimerIdentity(
                owner="campaigns",
                entity_kind="recipient_step",
                entity_id=initial.identity.entity_id,
                purpose="delivery_due:1",
            ),
            generation=1,
            due_at=NOW + timedelta(days=1),
            output_event_type="campaigns.recipient_step_due.v1",
        )
        with pytest.raises(SequenceBlocked):
            accept_due_work(
                db,
                tenant_id=tenant,
                trigger=forged,
                timers=timers,
                renderer=FakeRenderer(),
                senders=FakeSenderResolver(),
                idempotency_key="forged-due",
                idempotency_expires_at=NOW + timedelta(days=7),
                accepted_at=NOW + timedelta(days=1),
            )


def test_missing_outbox_publication_is_reported_and_repaired_once(
    migrated_scratch: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_scratch
    tenant, _ = _seed_tenants(admin_url)
    timers = FakeTimerPort()
    with _tenant_session(app_url, tenant) as db:
        campaign = _seed_campaign(db, tenant)
        schedule_campaign(
            db,
            tenant_id=tenant,
            campaign_id=campaign.id,
            timers=timers,
            idempotency_key="schedule",
            idempotency_expires_at=NOW + timedelta(days=7),
            recorded_at=NOW,
        )
        accepted = accept_due_work(
            db,
            tenant_id=tenant,
            trigger=timers.only_current_trigger(),
            timers=timers,
            renderer=FakeRenderer(),
            senders=FakeSenderResolver(),
            idempotency_key="due",
            idempotency_expires_at=NOW + timedelta(days=7),
            accepted_at=NOW,
        )
        db.commit()
        campaign_id = campaign.id
        intent = db.get(CampaignDeliveryIntent, accepted.delivery_intent_id)
        assert intent is not None
        lost_event = intent.outbox_event_id

    admin = create_engine(admin_url)
    with admin.begin() as conn:
        conn.execute(
            text("DELETE FROM public.outbox_events WHERE id=:id"),
            {"id": lost_event},
        )
    admin.dispose()

    with _tenant_session(app_url, tenant) as db:
        drift = report_drift(db, tenant_id=tenant, campaign_id=campaign_id)
        assert drift.missing_publications == (accepted.delivery_intent_id,)
        repaired = repair_missing_publications(
            db,
            tenant_id=tenant,
            campaign_id=campaign_id,
            repaired_at=NOW + timedelta(minutes=1),
        )
        assert repaired == 1
        assert repair_missing_publications(
            db,
            tenant_id=tenant,
            campaign_id=campaign_id,
            repaired_at=NOW + timedelta(minutes=2),
        ) == 0
        db.commit()
        intent = db.get(CampaignDeliveryIntent, accepted.delivery_intent_id)
        assert intent is not None
        assert intent.dispatch_id == accepted.dispatch_id
        assert intent.outbox_event_id != lost_event
