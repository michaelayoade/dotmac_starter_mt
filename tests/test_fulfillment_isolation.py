"""PostgreSQL tenancy and append-only canaries for dotmac-fulfillment."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from queue import Queue
from threading import Event, Thread

import pytest
from dotmac_fulfillment import (
    AttemptRequest,
    FulfillmentConflict,
    ParticipantRegistry,
    request_attempt,
)
from dotmac_kernel.modules import ModuleManifest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
FULFILLMENT_VERSIONS = (
    REPO_ROOT / "packages/dotmac-fulfillment/src/dotmac_fulfillment/migrations/versions"
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — fulfillment isolation needs Postgres")
    return url


def _url_for(base_url: str, database: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{database}"


@pytest.fixture
def fulfillment_database() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"fulfillment_{uuid.uuid4().hex[:12]}"
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
    previous_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {FULFILLMENT_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        if previous_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = previous_url
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


def _seed(admin_url: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    run_a, run_b = uuid.uuid4(), uuid.uuid4()
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
        for tenant_id, run_id, suffix in (
            (tenant_a, run_a, "a"),
            (tenant_b, run_b, "b"),
        ):
            connection.execute(
                text(
                    "INSERT INTO mod_fulfillment.fulfillment_runs "
                    "(id, tenant_id, intent_ref, idempotency_key, request_fingerprint, "
                    "correlation_id, created_at) VALUES "
                    "(:id, :tenant, :intent, :key, :fingerprint, "
                    ":correlation, :created)"
                ),
                {
                    "id": run_id,
                    "tenant": tenant_id,
                    "intent": f"intent-{suffix}",
                    "key": f"create-{suffix}",
                    "fingerprint": suffix * 64,
                    "correlation": f"correlation-{suffix}",
                    "created": datetime(2026, 8, 19, tzinfo=UTC),
                },
            )
    engine.dispose()
    return tenant_a, tenant_b, run_a, run_b


def test_a_tenant_sees_only_its_fulfillment_runs(
    fulfillment_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = fulfillment_database
    tenant_a, tenant_b, _, _ = _seed(admin_url)
    engine = create_engine(app_user_url)
    try:
        with engine.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            assert connection.execute(
                text("SELECT tenant_id FROM mod_fulfillment.fulfillment_runs")
            ).scalars().all() == [tenant_a]
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_b)},
            )
            assert connection.execute(
                text("SELECT tenant_id FROM mod_fulfillment.fulfillment_runs")
            ).scalars().all() == [tenant_b]
    finally:
        engine.dispose()


def test_a_cross_tenant_step_reference_is_impossible(
    fulfillment_database: tuple[str, str],
) -> None:
    admin_url, _ = fulfillment_database
    _, tenant_b, run_a, _ = _seed(admin_url)
    engine = create_engine(admin_url)
    try:
        with engine.begin() as connection, pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO mod_fulfillment.fulfillment_steps "
                    "(id, tenant_id, run_id, step_id, sequence, participant_code, "
                    "command_type, spec, spec_fingerprint) VALUES "
                    "(:id, :tenant, :run, 'cross-tenant', 1, 'probe', "
                    "'probe.execute.v1', '{}'::jsonb, :fingerprint)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_b,
                    "run": run_a,
                    "fingerprint": "a" * 64,
                },
            )
    finally:
        engine.dispose()


def test_attempt_evidence_cannot_be_updated_or_deleted_by_the_online_role(
    fulfillment_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = fulfillment_database
    tenant_a, _, run_a, _ = _seed(admin_url)
    step_id, attempt_id = uuid.uuid4(), uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mod_fulfillment.fulfillment_steps "
                "(id, tenant_id, run_id, step_id, sequence, participant_code, "
                "command_type, spec, spec_fingerprint) VALUES "
                "(:id, :tenant, :run, 'line-1', 1, 'probe', 'probe.execute.v1', "
                "'{}'::jsonb, :fingerprint)"
            ),
            {"id": step_id, "tenant": tenant_a, "run": run_a, "fingerprint": "b" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO mod_fulfillment.fulfillment_attempts "
                "(id, tenant_id, run_id, step_id, sequence, command_id, operation_id, "
                "idempotency_key, correlation_id, requested_at) VALUES "
                "(:id, :tenant, :run, :step, 1, 'command-1', 'operation-1', "
                "'attempt-1', 'corr-1', :requested)"
            ),
            {
                "id": attempt_id,
                "tenant": tenant_a,
                "run": run_a,
                "step": step_id,
                "requested": datetime(2026, 8, 19, tzinfo=UTC),
            },
        )
    admin.dispose()

    online = create_engine(app_user_url)
    try:
        with online.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        "UPDATE mod_fulfillment.fulfillment_attempts "
                        "SET operation_id = 'rewritten' WHERE id = :id"
                    ),
                    {"id": attempt_id},
                )
            connection.rollback()
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            with pytest.raises(DBAPIError):
                connection.execute(
                    text(
                        "DELETE FROM mod_fulfillment.fulfillment_attempts "
                        "WHERE id = :id"
                    ),
                    {"id": attempt_id},
                )
    finally:
        online.dispose()


def test_two_workers_cannot_both_dispatch_the_same_step(
    fulfillment_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = fulfillment_database
    tenant_id, _, run_id, _ = _seed(admin_url)
    step_record_id = uuid.uuid4()
    admin = create_engine(admin_url)
    with admin.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mod_fulfillment.fulfillment_steps "
                "(id, tenant_id, run_id, step_id, sequence, participant_code, "
                "command_type, spec, spec_fingerprint) VALUES "
                "(:id, :tenant, :run, 'line-1', 1, 'participant.concurrent', "
                "'service.converge.v1', '{}'::jsonb, :fingerprint)"
            ),
            {
                "id": step_record_id,
                "tenant": tenant_id,
                "run": run_id,
                "fingerprint": "c" * 64,
            },
        )

    participants = ParticipantRegistry.from_manifests(
        [
            ModuleManifest(
                code="concurrency_probe",
                version="1.0.0",
                provisioning_participants=("participant.concurrent",),
            )
        ]
    )
    online = create_engine(app_user_url)
    first_inside_publish = Event()
    second_entering_service = Event()
    release_first = Event()
    published: Queue[int] = Queue()
    results: Queue[tuple[int, Exception | None]] = Queue()

    def worker(index: int) -> None:
        try:
            with Session(online) as db, db.begin():
                db.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant_id)},
                )
                if index == 2:
                    second_entering_service.set()

                def publish(_db: Session, _command: object) -> None:
                    published.put(index)
                    if index == 1:
                        first_inside_publish.set()
                        if not release_first.wait(timeout=10):
                            raise RuntimeError("timed out holding aggregate lock")

                request_attempt(
                    db,
                    tenant_id=tenant_id,
                    run_id=run_id,
                    request=AttemptRequest(
                        step_id="line-1",
                        command_id=f"command-concurrent-{index}",
                        operation_id=f"operation-concurrent-{index}",
                        idempotency_key=f"attempt-concurrent-{index}",
                        correlation_id="concurrency-canary",
                        requested_at=datetime(2026, 8, 19, 9, tzinfo=UTC),
                        reobserve_at=datetime(2026, 8, 19, 9, 30, tzinfo=UTC),
                    ),
                    participants=participants,
                    publish=publish,
                    schedule_reobservation=lambda *_: None,
                )
        except Exception as exc:
            results.put((index, exc))
        else:
            results.put((index, None))

    first = Thread(target=worker, args=(1,), daemon=True)
    second = Thread(target=worker, args=(2,), daemon=True)
    try:
        first.start()
        assert first_inside_publish.wait(timeout=10)
        second.start()
        assert second_entering_service.wait(timeout=10)
        release_first.set()
        first.join(timeout=15)
        second.join(timeout=15)
        assert not first.is_alive() and not second.is_alive()

        outcomes = dict(results.get_nowait() for _ in range(2))
        assert outcomes[1] is None
        assert isinstance(outcomes[2], FulfillmentConflict)
        assert published.get_nowait() == 1
        assert published.empty()
        with admin.connect() as connection:
            count = connection.execute(
                text(
                    "SELECT count(*) FROM mod_fulfillment.fulfillment_attempts "
                    "WHERE tenant_id = :tenant AND run_id = :run AND step_id = :step"
                ),
                {"tenant": tenant_id, "run": run_id, "step": step_record_id},
            ).scalar_one()
        assert count == 1
    finally:
        release_first.set()
        online.dispose()
        admin.dispose()


def test_rls_canary_is_sensitive_to_a_disabled_guard(
    fulfillment_database: tuple[str, str],
) -> None:
    admin_url, app_user_url = fulfillment_database
    tenant_a, _, _, _ = _seed(admin_url)
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    online = create_engine(app_user_url)
    try:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "ALTER TABLE mod_fulfillment.fulfillment_runs "
                    "DISABLE ROW LEVEL SECURITY"
                )
            )
        with online.connect() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, false)"),
                {"tenant": str(tenant_a)},
            )
            visible = (
                connection.execute(
                    text("SELECT tenant_id FROM mod_fulfillment.fulfillment_runs")
                )
                .scalars()
                .all()
            )
        assert len(visible) == 2 and tenant_a in visible
    finally:
        with admin.connect() as connection:
            connection.execute(
                text(
                    "ALTER TABLE mod_fulfillment.fulfillment_runs "
                    "ENABLE ROW LEVEL SECURITY"
                )
            )
            connection.execute(
                text(
                    "ALTER TABLE mod_fulfillment.fulfillment_runs "
                    "FORCE ROW LEVEL SECURITY"
                )
            )
        online.dispose()
        admin.dispose()
