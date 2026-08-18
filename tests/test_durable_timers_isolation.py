"""The ten PostgreSQL-first proofs for ``dotmac-durable-timers``.

The source timer suite runs on SQLite, where its locks disappear.  These tests
therefore are the module's primary concurrency and isolation evidence.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_durable_timers.models import (
    PlatformTimer,
    PlatformTimerAcceptance,
    Timer,
    TimerAcceptance,
)
from dotmac_durable_timers.service import (
    AcceptanceOutcome,
    CancelOutcome,
    TimerIdentity,
    TimerOutput,
    TimerTrigger,
    accept_trigger,
    cancel_timer,
    purge_history,
    schedule_timer,
)
from dotmac_kernel.cache import PlatformScope, TenantScope
from dotmac_kernel.messaging.platform_relay import (
    claim_platform_batch,
)
from dotmac_kernel.messaging.platform_relay import (
    record_success as record_platform_success,
)
from dotmac_kernel.messaging.relay import (
    RelayPolicy,
    claim_batch,
    record_failure,
    record_success,
)
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
TIMER_VERSIONS = (
    REPO_ROOT
    / "packages/dotmac-durable-timers/src/dotmac_durable_timers/migrations/versions"
)

PAST = datetime(2020, 1, 1, tzinfo=UTC)
NOW = datetime(2029, 1, 2, tzinfo=UTC)
FUTURE = datetime(2030, 2, 1, tzinfo=UTC)
OUTPUT = TimerOutput("tests.timer_due")


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — these proofs need PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def scratch() -> Iterator[dict[str, str]]:
    superuser = _superuser_url()
    name = f"timers_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as connection:
        connection.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        connection.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        for role in (
            "app_user",
            "platform_api",
            "outbox_dispatcher",
            "platform_outbox_dispatcher",
        ):
            connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO {role}'))
        connection.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    try:
        from alembic import command
        from alembic.config import Config

        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        config.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {TIMER_VERSIONS}",
        )
        config.attributes["module_plane_selections"] = (
            ModulePlaneSelection(
                module="durable_timers",
                planes=(ModulePlane.TENANT, ModulePlane.PLATFORM),
            ),
        )
        admin_url = _url_for(superuser, name, user="app_admin")
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(config, "heads")
        yield {
            "admin": admin_url,
            "tenant": _url_for(superuser, name, user="app_user"),
            "platform": _url_for(superuser, name, user="platform_api"),
            "dispatcher": _url_for(superuser, name, user="outbox_dispatcher"),
            "platform_dispatcher": _url_for(
                superuser, name, user="platform_outbox_dispatcher"
            ),
        }
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


def _make_tenant(admin_url: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(
            text(
                "INSERT INTO public.tenants (id, name, slug, is_active) "
                "VALUES (:id, :name, :slug, true)"
            ),
            {
                "id": tenant_id,
                "name": f"tenant-{tenant_id.hex[:8]}",
                "slug": tenant_id.hex[:8],
            },
        )
    engine.dispose()
    return tenant_id


def _tenant_engine(url: str, tenant_id: uuid.UUID):
    engine = create_engine(url)

    @event.listens_for(engine, "connect")
    def _set_tenant(dbapi_connection, _record):
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


def _identity(number: int = 1) -> TimerIdentity:
    return TimerIdentity(
        owner="tests.owner",
        entity_kind="tests.entity",
        entity_id=f"entity-{number}",
        purpose="tests.deadline",
    )


def _schedule_tenant(
    url: str,
    tenant_id: uuid.UUID,
    identity: TimerIdentity,
    *,
    due_at: datetime = PAST,
):
    with _tenant_session(url, tenant_id) as session:
        result = schedule_timer(
            session,
            scope=TenantScope(tenant_id),
            identity=identity,
            due_at=due_at,
            output=OUTPUT,
            recorded_at=NOW,
        )
        session.commit()
        return result


def _schedule_platform(url: str, identity: TimerIdentity):
    engine = create_engine(url)
    try:
        with Session(engine) as session:
            result = schedule_timer(
                session,
                scope=PlatformScope(),
                identity=identity,
                due_at=PAST,
                output=OUTPUT,
                recorded_at=NOW,
            )
            session.commit()
            return result
    finally:
        engine.dispose()


def test_proof_1_concurrent_first_schedules_allocate_gapless_generations(
    scratch,
) -> None:
    tenant_id = _make_tenant(scratch["admin"])
    identity = _identity()

    for workers in (2, 8):
        if workers == 8:
            identity = _identity(8)
        barrier = threading.Barrier(workers)

        def run(
            index: int,
            *,
            race_barrier: threading.Barrier = barrier,
            race_identity: TimerIdentity = identity,
        ):
            race_barrier.wait()
            return _schedule_tenant(
                scratch["tenant"],
                tenant_id,
                race_identity,
                due_at=PAST + timedelta(minutes=index),
            )

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run, index) for index in range(workers)]
            results = [future.result() for future in futures]
        assert {result.generation for result in results} == set(range(1, workers + 1))

        with _tenant_session(scratch["tenant"], tenant_id) as session:
            rows = session.scalars(
                select(Timer)
                .where(Timer.entity_id == identity.entity_id)
                .order_by(Timer.generation)
            ).all()
        assert [row.generation for row in rows] == list(range(1, workers + 1))
        assert [row.status for row in rows].count("scheduled") == 1
        assert rows[-1].status == "scheduled"


def test_proof_2_concurrent_reschedules_serialize_over_an_existing_timer(
    scratch,
) -> None:
    tenant_id = _make_tenant(scratch["admin"])
    identity = _identity(2)
    first = _schedule_tenant(scratch["tenant"], tenant_id, identity)
    assert first.generation == 1
    barrier = threading.Barrier(2)

    def run(offset: int):
        barrier.wait()
        return _schedule_tenant(
            scratch["tenant"],
            tenant_id,
            identity,
            due_at=PAST + timedelta(hours=offset),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run, 1), pool.submit(run, 2)]
        results = [future.result() for future in futures]
    assert {result.generation for result in results} == {2, 3}
    with _tenant_session(scratch["tenant"], tenant_id) as session:
        rows = session.scalars(
            select(Timer)
            .where(Timer.entity_id == identity.entity_id)
            .order_by(Timer.generation)
        ).all()
    assert [(row.generation, row.status) for row in rows] == [
        (1, "superseded"),
        (2, "superseded"),
        (3, "scheduled"),
    ]


def test_proof_3_kernel_relay_claims_are_exclusive_and_cover_every_due_timer(
    scratch,
) -> None:
    tenant_id = _make_tenant(scratch["admin"])
    for number in range(12):
        _schedule_tenant(scratch["tenant"], tenant_id, _identity(100 + number))

    first_engine = create_engine(scratch["dispatcher"])
    second_engine = create_engine(scratch["dispatcher"])
    try:
        with Session(first_engine) as first, Session(second_engine) as second:
            assert (
                first.execute(text("SHOW transaction_isolation")).scalar()
                == "read committed"
            )
            claimed_first = claim_batch(
                first, worker_id="proof-3-a", policy=RelayPolicy(batch_size=5)
            )
            claimed_second = claim_batch(
                second, worker_id="proof-3-b", policy=RelayPolicy(batch_size=20)
            )
            first.commit()
            second.commit()
    finally:
        first_engine.dispose()
        second_engine.dispose()

    first_ids = {event.id for event in claimed_first}
    second_ids = {event.id for event in claimed_second}
    assert len(first_ids) == 5
    assert first_ids.isdisjoint(second_ids)
    assert len(first_ids | second_ids) == 12

    platform_schedules = [
        _schedule_platform(scratch["platform"], _identity(300 + number))
        for number in range(4)
    ]
    platform_dispatcher = create_engine(scratch["platform_dispatcher"])
    try:
        with Session(platform_dispatcher) as session:
            platform_claims = claim_platform_batch(
                session,
                worker_id="proof-3-platform",
                policy=RelayPolicy(batch_size=10),
            )
            session.commit()
        assert {row.id for row in platform_claims} == {
            scheduled.outbox_event_id for scheduled in platform_schedules
        }
        for event_row in platform_claims:
            with Session(platform_dispatcher) as session:
                assert record_platform_success(
                    session,
                    event_id=event_row.id,
                    worker_id="proof-3-platform",
                )
                session.commit()
    finally:
        platform_dispatcher.dispose()


def test_proof_4_stale_lease_recovery_accepts_one_timer_effect(scratch) -> None:
    tenant_id = _make_tenant(scratch["admin"])
    scheduled = _schedule_tenant(scratch["tenant"], tenant_id, _identity(4))
    dispatcher = create_engine(scratch["dispatcher"])
    with Session(dispatcher) as session:
        [first] = claim_batch(
            session, worker_id="proof-4-w1", policy=RelayPolicy(batch_size=1)
        )
        session.commit()
    admin = create_engine(scratch["admin"])
    with admin.begin() as connection:
        connection.execute(
            text(
                "UPDATE public.outbox_events "
                "SET leased_at = now() - interval '1 hour' "
                "WHERE id = :id"
            ),
            {"id": first.id},
        )
    with Session(dispatcher) as session:
        [reclaimed] = claim_batch(
            session,
            worker_id="proof-4-w2",
            policy=RelayPolicy(batch_size=1, stale_lease_seconds=60),
        )
        session.commit()
    assert reclaimed.id == scheduled.outbox_event_id
    with _tenant_session(scratch["tenant"], tenant_id) as session:
        trigger = TimerTrigger.from_payload(reclaimed.payload)
        result = accept_trigger(
            session, scope=TenantScope(tenant_id), trigger=trigger, accepted_at=NOW
        )
        assert result.outcome is AcceptanceOutcome.CURRENT
        replay = accept_trigger(
            session, scope=TenantScope(tenant_id), trigger=trigger, accepted_at=NOW
        )
        assert replay.outcome is AcceptanceOutcome.CURRENT and replay.replayed
        count = session.scalar(
            select(func.count())
            .select_from(TimerAcceptance)
            .where(TimerAcceptance.timer_id == scheduled.timer_id)
        )
        assert count == 1
        session.commit()
    with Session(dispatcher) as session:
        assert record_success(session, event_id=reclaimed.id, worker_id="proof-4-w2")
        session.commit()
    admin.dispose()
    dispatcher.dispose()


def test_proof_5_cancel_and_accept_have_distinct_race_outcomes(scratch) -> None:
    tenant_id = _make_tenant(scratch["admin"])
    identity = _identity(5)
    scheduled = _schedule_tenant(scratch["tenant"], tenant_id, identity)
    trigger = TimerTrigger.for_scheduled(scheduled)
    barrier = threading.Barrier(2)

    def run_accept():
        with _tenant_session(scratch["tenant"], tenant_id) as session:
            barrier.wait()
            result = accept_trigger(
                session,
                scope=TenantScope(tenant_id),
                trigger=trigger,
                accepted_at=NOW,
            )
            session.commit()
            return result.outcome

    def run_cancel():
        with _tenant_session(scratch["tenant"], tenant_id) as session:
            barrier.wait()
            result = cancel_timer(
                session,
                scope=TenantScope(tenant_id),
                identity=identity,
                recorded_at=NOW,
            )
            session.commit()
            return result.outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        accept_future = pool.submit(run_accept)
        cancel_future = pool.submit(run_cancel)
        outcomes = (accept_future.result(), cancel_future.result())
    assert outcomes in {
        (AcceptanceOutcome.CURRENT, CancelOutcome.ALREADY_FIRED),
        (AcceptanceOutcome.CANCELED, CancelOutcome.CANCELED),
    }

    canceled = _schedule_tenant(scratch["tenant"], tenant_id, _identity(50))
    with _tenant_session(scratch["tenant"], tenant_id) as session:
        result = cancel_timer(
            session,
            scope=TenantScope(tenant_id),
            identity=_identity(50),
            recorded_at=NOW,
        )
        assert result.outcome is CancelOutcome.CANCELED
        session.commit()
        refused = accept_trigger(
            session,
            scope=TenantScope(tenant_id),
            trigger=TimerTrigger.for_scheduled(canceled),
            accepted_at=NOW,
        )
        assert refused.outcome is AcceptanceOutcome.CANCELED
        missing = cancel_timer(
            session,
            scope=TenantScope(tenant_id),
            identity=_identity(500),
            recorded_at=NOW,
        )
        assert missing.outcome is CancelOutcome.NOTHING_SCHEDULED


def test_proof_6_old_generation_is_rejected_before_the_consumer_effect(scratch) -> None:
    tenant_id = _make_tenant(scratch["admin"])
    identity = _identity(6)
    old = _schedule_tenant(scratch["tenant"], tenant_id, identity)
    current = _schedule_tenant(scratch["tenant"], tenant_id, identity, due_at=FUTURE)
    effects = 0
    with _tenant_session(scratch["tenant"], tenant_id) as session:
        stale = accept_trigger(
            session,
            scope=TenantScope(tenant_id),
            trigger=TimerTrigger.for_scheduled(old),
            accepted_at=NOW,
        )
        if stale.outcome is AcceptanceOutcome.CURRENT and not stale.replayed:
            effects += 1
        assert stale.outcome is AcceptanceOutcome.STALE
        assert stale.observed_generation == 1
        assert stale.current_generation == 2
        accepted = accept_trigger(
            session,
            scope=TenantScope(tenant_id),
            trigger=TimerTrigger.for_scheduled(current),
            accepted_at=NOW,
        )
        if accepted.outcome is AcceptanceOutcome.CURRENT and not accepted.replayed:
            effects += 1
        session.commit()
    assert effects == 1


def test_proof_7_one_poison_delivery_does_not_block_or_replay_the_other_nineteen(
    scratch,
) -> None:
    tenant_id = _make_tenant(scratch["admin"])
    for number in range(20):
        _schedule_tenant(scratch["tenant"], tenant_id, _identity(700 + number))
    dispatcher = create_engine(scratch["dispatcher"])
    policy = RelayPolicy(batch_size=20, max_attempts=3, base_backoff_seconds=1)
    with Session(dispatcher) as dispatch:
        claimed = claim_batch(dispatch, worker_id="proof-7", policy=policy)
        dispatch.commit()
    poison = claimed[0]
    for event_row in claimed[1:]:
        with _tenant_session(scratch["tenant"], tenant_id) as session:
            accept_trigger(
                session,
                scope=TenantScope(tenant_id),
                trigger=TimerTrigger.from_payload(event_row.payload),
                accepted_at=NOW,
            )
            session.commit()
        with Session(dispatcher) as dispatch:
            record_success(dispatch, event_id=event_row.id, worker_id="proof-7")
            dispatch.commit()
    with Session(dispatcher) as dispatch:
        record_failure(
            dispatch,
            event_id=poison.id,
            worker_id="proof-7",
            attempts=poison.attempts,
            error="poison",
            policy=policy,
        )
        dispatch.commit()

    admin = create_engine(scratch["admin"])
    for attempt in (1, 2):
        with admin.begin() as connection:
            connection.execute(
                text(
                    "UPDATE public.outbox_events "
                    "SET available_at = now() - interval '1 second' "
                    "WHERE id = :id"
                ),
                {"id": poison.id},
            )
        with Session(dispatcher) as dispatch:
            [retry] = claim_batch(dispatch, worker_id="proof-7", policy=policy)
            dispatch.commit()
        with Session(dispatcher) as dispatch:
            outcome = record_failure(
                dispatch,
                event_id=retry.id,
                worker_id="proof-7",
                attempts=retry.attempts,
                error="poison",
                policy=policy,
            )
            dispatch.commit()
        if attempt == 2:
            assert outcome.dead_lettered

    with admin.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM mod_timers.timers WHERE status = 'fired'")
            ).scalar_one()
            == 19
        )
        dead = connection.execute(
            text(
                "SELECT status, attempts, last_error "
                "FROM public.outbox_events WHERE id = :id"
            ),
            {"id": poison.id},
        ).one()
        assert dead == ("dead", 3, "poison")
    admin.dispose()
    dispatcher.dispose()


def test_proof_8_tenant_rls_and_composite_current_identity(scratch) -> None:
    left = _make_tenant(scratch["admin"])
    right = _make_tenant(scratch["admin"])
    identity = _identity(8)
    left_timer = _schedule_tenant(scratch["tenant"], left, identity)
    right_timer = _schedule_tenant(scratch["tenant"], right, identity)
    with _tenant_session(scratch["tenant"], left) as session:
        accept_trigger(
            session,
            scope=TenantScope(left),
            trigger=TimerTrigger.for_scheduled(left_timer),
            accepted_at=NOW,
        )
        session.commit()
        assert session.execute(text("SELECT current_user")).scalar_one() == "app_user"
        count = session.scalar(
            select(func.count())
            .select_from(Timer)
            .where(Timer.entity_id == identity.entity_id)
        )
        assert count == 1
        acceptance_count = session.scalar(
            select(func.count()).select_from(TimerAcceptance)
        )
        assert acceptance_count == 1
    with _tenant_session(scratch["tenant"], right) as session:
        accept_trigger(
            session,
            scope=TenantScope(right),
            trigger=TimerTrigger.for_scheduled(right_timer),
            accepted_at=NOW,
        )
        session.commit()
        count = session.scalar(
            select(func.count())
            .select_from(Timer)
            .where(Timer.entity_id == identity.entity_id)
        )
        assert count == 1
        acceptance_count = session.scalar(
            select(func.count()).select_from(TimerAcceptance)
        )
        assert acceptance_count == 1

    admin = create_engine(scratch["admin"])
    with admin.connect() as connection:
        rls_rows = connection.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE oid IN "
                "('mod_timers.timers'::regclass, "
                "'mod_timers.timer_acceptances'::regclass) ORDER BY relname"
            )
        ).all()
        assert rls_rows == [
            ("timer_acceptances", True, True),
            ("timers", True, True),
        ]
        policy_count = connection.execute(
            text(
                "SELECT count(*) FROM pg_policies "
                "WHERE schemaname = 'mod_timers' "
                "AND tablename IN ('timers', 'timer_acceptances')"
            )
        ).scalar_one()
        assert policy_count == 2
    admin.dispose()


def _assert_sqlstate(
    url: str,
    statement: str,
    code: str,
    parameters: Mapping[str, object] | None = None,
) -> None:
    engine = create_engine(url)
    try:
        with engine.connect() as connection, pytest.raises(DBAPIError) as captured:
            connection.execute(text(statement), parameters or {})
        assert getattr(captured.value.orig, "sqlstate", None) == code
    finally:
        engine.dispose()


def test_proof_9_platform_tables_are_revoked_from_tenants_and_reachable_online(
    scratch,
) -> None:
    operations = (
        "SELECT * FROM mod_timers.platform_timers",
        "INSERT INTO mod_timers.platform_timers (id) VALUES (gen_random_uuid())",
        "UPDATE mod_timers.platform_timers SET id = id",
        "DELETE FROM mod_timers.platform_timers",
        "SELECT * FROM mod_timers.platform_timer_acceptances",
        "INSERT INTO mod_timers.platform_timer_acceptances (id) "
        "VALUES (gen_random_uuid())",
        "UPDATE mod_timers.platform_timer_acceptances SET id = id",
        "DELETE FROM mod_timers.platform_timer_acceptances",
    )
    for operation in operations:
        _assert_sqlstate(scratch["tenant"], operation, "42501")

    admin = create_engine(scratch["admin"])
    with admin.connect() as connection:
        table_grants = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.table_privileges "
                "WHERE grantee = 'app_user' AND table_schema = 'mod_timers' "
                "AND table_name IN "
                "('platform_timers', 'platform_timer_acceptances')"
            )
        ).scalar_one()
        column_grants = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.column_privileges "
                "WHERE grantee = 'app_user' AND table_schema = 'mod_timers' "
                "AND table_name IN "
                "('platform_timers', 'platform_timer_acceptances')"
            )
        ).scalar_one()
        assert table_grants == column_grants == 0
        rls_rows = connection.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE oid IN "
                "('mod_timers.platform_timers'::regclass, "
                "'mod_timers.platform_timer_acceptances'::regclass) "
                "ORDER BY relname"
            )
        ).all()
        assert rls_rows == [
            ("platform_timer_acceptances", False, False),
            ("platform_timers", False, False),
        ]
    admin.dispose()
    platform = create_engine(scratch["platform"])
    with Session(platform) as session:
        scheduled = schedule_timer(
            session,
            scope=PlatformScope(),
            identity=_identity(9),
            due_at=PAST,
            output=OUTPUT,
            recorded_at=NOW,
        )
        session.commit()
        accepted = accept_trigger(
            session,
            scope=PlatformScope(),
            trigger=TimerTrigger.for_scheduled(scheduled),
            accepted_at=NOW,
        )
        assert accepted.outcome is AcceptanceOutcome.CURRENT
        session.commit()
        count = session.scalar(
            select(func.count()).select_from(PlatformTimerAcceptance)
        )
        assert count == 1
    platform.dispose()


@pytest.mark.parametrize("platform", [False, True])
def test_proof_10_plane_parity_no_clock_and_append_only_terminal_history(
    scratch, platform: bool
) -> None:
    tenant_id = _make_tenant(scratch["admin"])
    if platform:
        scope = PlatformScope()
        engine = create_engine(scratch["platform"])
        model = PlatformTimer
        role_url = scratch["platform"]
        update_statement = (
            "UPDATE mod_timers.platform_timers SET fired_at = fired_at WHERE id = :id"
        )
        delete_statement = "DELETE FROM mod_timers.platform_timers WHERE id = :id"
    else:
        scope = TenantScope(tenant_id)
        engine = _tenant_engine(scratch["tenant"], tenant_id)
        model = Timer
        role_url = scratch["tenant"]
        update_statement = (
            "UPDATE mod_timers.timers SET fired_at = fired_at WHERE id = :id"
        )
        delete_statement = "DELETE FROM mod_timers.timers WHERE id = :id"
    try:
        with Session(engine) as session:
            scheduled = schedule_timer(
                session,
                scope=scope,
                identity=_identity(10),
                due_at=PAST,
                output=OUTPUT,
                recorded_at=NOW,
                expires_at=FUTURE,
            )
            session.commit()
            accepted = accept_trigger(
                session,
                scope=scope,
                trigger=TimerTrigger.for_scheduled(scheduled),
                accepted_at=NOW,
            )
            assert accepted.outcome is AcceptanceOutcome.CURRENT
            session.commit()
            row = session.scalar(select(model).where(model.id == scheduled.timer_id))
            assert row is not None and row.status == "fired"

        _assert_sqlstate(
            role_url,
            update_statement,
            "42501",
            {"id": scheduled.timer_id},
        )
        _assert_sqlstate(
            role_url,
            delete_statement,
            "42501",
            {"id": scheduled.timer_id},
        )
        with Session(engine) as session:
            assert (
                purge_history(
                    session, scope=scope, before=FUTURE + timedelta(days=1), limit=10
                )
                == 1
            )
            session.commit()
    finally:
        engine.dispose()

    admin = create_engine(scratch["admin"])
    with admin.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM pg_type WHERE typname = 'timerstatus'")
            ).scalar_one()
            == 0
        )
    admin.dispose()
