"""PostgreSQL proofs for the ``dotmac-durable-timers`` job runner.

Run on the hosted integration job (needs ``TEST_DATABASE_URL``). They reuse the
scratch-database fixture of ``test_durable_timers_isolation`` and drive the real
kernel relay worker with the real ``JobDispatchTransport``:

1. a due job fires once and arms its next generation in the same transaction;
2. two runners racing for one generation produce one execution;
3. a crash after the handler but before the commit retries with no double
   effect, under one idempotency key (the generation);
4. RLS isolation across tenants, and the dispatcher credential never reads
   tenant tables;
5. retry, backoff and the dead-letter path (audit row, health observation,
   reconciler re-arming);
6. DST and timezone-aware cron;
7. pause stops scheduling without losing the generation, resume re-arms;
8. run-now supersedes the live generation;
9. the built-in purge jobs.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

import dotmac_durable_timers.jobs as jobs_module
import dotmac_kernel.audit_actions as audit_actions_module
import dotmac_kernel.outbox_event_types as outbox_types_module
import dotmac_kernel.source_applications as source_applications_module
import pytest
from dotmac_durable_timers import admin
from dotmac_durable_timers.builtin import purge_published_outbox, purge_timer_history
from dotmac_durable_timers.dispatch import JobDispatchTransport
from dotmac_durable_timers.health import JobDeadLetter
from dotmac_durable_timers.jobs import (
    JobCatalogue,
    JobEntry,
    JobOutcome,
    JobRunContext,
    arm,
    reconcile_tenant,
)
from dotmac_durable_timers.manifest import module as timers_module
from dotmac_durable_timers.models import JobState, Timer, TimerRejection
from dotmac_durable_timers.rolecheck import RoleSplitError, verify_role_split
from dotmac_durable_timers.runner import JobRunner
from dotmac_kernel.audit import AuditEvent, write_audit_event
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.delivery import DeliveryTerminal
from dotmac_kernel.exceptions import ConflictError
from dotmac_kernel.jobs import JobSpec
from dotmac_kernel.messaging.relay import ClaimedEvent, RelayPolicy, claim_batch
from dotmac_kernel.outbox_event_types import (
    OutboxEventTypeRegistry,
    install_outbox_event_types,
)
from dotmac_kernel.session_runtime import DatabaseRuntime
from dotmac_kernel.source_applications import install_host_application
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.test_durable_timers_isolation import (  # noqa: F401
    _make_tenant,
    _superuser_url,
    _url_for,
    scratch,
)

PAST = datetime(2020, 1, 1, tzinfo=UTC)
NOW = datetime(2029, 1, 2, tzinfo=UTC)
MODULE = "tests_jobs"
EFFECT_ACTION = "durable_timers.job.run_now"  # a declared action reused as the effect


# ── handlers (addressed by import path from the JobSpecs below) ─────────────


@dataclass
class _Probe:
    calls: list[JobRunContext] = field(default_factory=list)
    visible_tenants: list[set[UUID]] = field(default_factory=list)
    fail: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


PROBE = _Probe()


def _effect(session: Session, scope: TenantScope, context: JobRunContext) -> None:
    """A durable effect inside the run's transaction, keyed by the generation."""
    write_audit_event(
        session,
        tenant_id=scope.tenant_id,
        actor_type="system",
        actor_id="tests",
        action=EFFECT_ACTION,
        entity_type="effect",
        entity_id=context.idempotency_key,
    )


def handler_ok(
    session: Session, scope: TenantScope, run_context: JobRunContext
) -> JobOutcome:
    with PROBE.lock:
        PROBE.calls.append(run_context)
    _effect(session, scope, run_context)
    states = set(session.scalars(select(JobState.tenant_id)))
    PROBE.visible_tenants.append(states | {scope.tenant_id})
    return JobOutcome(detail="done")


def handler_flaky(
    session: Session, scope: TenantScope, run_context: JobRunContext
) -> JobOutcome:
    _effect(session, scope, run_context)
    if PROBE.fail:
        raise RuntimeError("boom with a secret-looking detail")
    return JobOutcome()


def _spec(code: str, handler: str, **kwargs: object) -> JobSpec:
    kwargs.setdefault("interval_seconds", 3600)
    return JobSpec(
        code=code,
        handler=f"tests.test_durable_timers_runner_pg:{handler}",
        plane="tenant",
        **kwargs,  # type: ignore[arg-type]
    )


class _Sink:
    def __init__(self) -> None:
        self.dead: list[JobDeadLetter] = []

    def job_dead_lettered(self, dead_letter: JobDeadLetter) -> None:
        self.dead.append(dead_letter)


@pytest.fixture(autouse=True)
def _process_vocabularies() -> Iterator[None]:
    # Process-wide registries: put back whatever was installed, or every later
    # test in the session would see only this module's vocabulary.
    previous = (
        audit_actions_module._active_registry,
        outbox_types_module._active_registry,
        source_applications_module._host_application,
    )
    install_outbox_event_types(OutboxEventTypeRegistry.from_manifests([timers_module]))
    install_audit_actions(AuditActionRegistry.from_manifests([timers_module]))
    install_host_application("dotmac_starter_mt")
    PROBE.calls.clear()
    PROBE.visible_tenants.clear()
    PROBE.fail = False
    yield
    audit_actions_module._active_registry = previous[0]
    outbox_types_module._active_registry = previous[1]
    source_applications_module._host_application = previous[2]


@pytest.fixture
def env(scratch) -> Iterator[SimpleNamespace]:  # noqa: F811
    tenant = DatabaseRuntime.from_urls(database_url=scratch["tenant"])
    dispatcher = DatabaseRuntime.from_urls(database_url=scratch["dispatcher"])
    admin_engine = create_engine(scratch["admin"], isolation_level="AUTOCOMMIT")
    ok = JobEntry(MODULE, _spec("ok", "handler_ok"))
    flaky = JobEntry(
        MODULE,
        _spec("flaky", "handler_flaky", max_attempts=2, backoff_seconds=(0,)),
    )
    cron = JobEntry(
        MODULE, _spec("nightly", "handler_ok", interval_seconds=None, cron="30 2 * * *")
    )
    catalogue = JobCatalogue([ok, flaky])
    sink = _Sink()
    transport = JobDispatchTransport(
        catalogue=catalogue, health=sink, clock=lambda: NOW
    )
    yield SimpleNamespace(
        scratch=scratch,
        tenant=tenant,
        dispatcher=dispatcher,
        admin=admin_engine,
        ok=ok,
        flaky=flaky,
        cron=cron,
        catalogue=catalogue,
        sink=sink,
        transport=transport,
    )
    for runtime in (tenant, dispatcher):
        runtime.engine.dispose()
        runtime.platform_engine.dispose()
    admin_engine.dispose()


def _arm(
    env: SimpleNamespace, tenant_id: UUID, entry: JobEntry, **kwargs: object
) -> None:
    kwargs.setdefault("due_at", PAST)
    with env.tenant.tenant_session(tenant_id) as db:
        arm(db, tenant_id=tenant_id, entry=entry, now=NOW, **kwargs)  # type: ignore[arg-type]


def _drain(env: SimpleNamespace, policy: RelayPolicy | None = None) -> int:
    runner = JobRunner(
        catalogue=env.catalogue,
        transport=env.transport,
        dispatcher_session_factory=env.dispatcher.session_factory,
        tenant_runtime=env.tenant,
        worker_id="runner-1",
        clock=lambda: NOW,
        policy=policy or RelayPolicy(batch_size=10),
    )
    return runner.drain_once()


def _timers(env: SimpleNamespace, tenant_id: UUID, entry: JobEntry) -> list[Timer]:
    with env.tenant.tenant_session(tenant_id) as db:
        rows = list(
            db.scalars(
                select(Timer)
                .where(Timer.entity_id == entry.spec.code)
                .order_by(Timer.generation)
            )
        )
        db.expunge_all()
    return rows


def _effects(env: SimpleNamespace, tenant_id: UUID) -> list[str]:
    with env.tenant.tenant_session(tenant_id) as db:
        return list(
            db.scalars(
                select(AuditEvent.entity_id).where(AuditEvent.entity_type == "effect")
            )
        )


def _state(env: SimpleNamespace, tenant_id: UUID, entry: JobEntry) -> JobState | None:
    with env.tenant.tenant_session(tenant_id) as db:
        state = db.scalar(select(JobState).where(JobState.job_code == entry.spec.code))
        if state is not None:
            db.expunge(state)
        return state


def _outbox_statuses(env: SimpleNamespace) -> list[str]:
    with env.admin.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT status FROM public.outbox_events "
                    "WHERE event_type = 'durable_timers.job_due.v1' ORDER BY created_at"
                )
            ).scalars()
        )


def _make_due_again(env: SimpleNamespace) -> None:
    with env.admin.connect() as connection:
        connection.execute(
            text(
                "UPDATE public.outbox_events SET available_at = :past "
                "WHERE status = 'pending'"
            ),
            {"past": PAST},
        )


# ── 1. due fire ─────────────────────────────────────────────────────────────


def test_a_due_job_fires_once_and_schedules_the_next_generation(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok)

    assert _drain(env) == 1

    assert [c.generation for c in PROBE.calls] == [1]
    assert PROBE.calls[0].idempotency_key == f"{tenant_id}:{MODULE}:ok:1"
    rows = _timers(env, tenant_id, env.ok)
    assert [(r.generation, r.status) for r in rows] == [(1, "fired"), (2, "scheduled")]
    assert rows[1].due_at == NOW + timedelta(seconds=3600)
    state = _state(env, tenant_id, env.ok)
    assert state is not None and state.last_outcome == "ok"
    assert state.last_generation == 1 and state.consecutive_failures == 0
    assert _outbox_statuses(env)[0] == "sent"
    # Nothing else is due: the next generation is in the future.
    assert _drain(env) == 0
    assert len(PROBE.calls) == 1


# ── 2. racing runners ───────────────────────────────────────────────────────


def test_two_runners_racing_one_generation_execute_it_once(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok)
    dispatcher_db = env.dispatcher.session_factory()
    try:
        (claimed,) = claim_batch(dispatcher_db, worker_id="runner-1")
        dispatcher_db.commit()
    finally:
        dispatcher_db.close()

    barrier = threading.Barrier(2)

    def deliver() -> None:
        # Two runners holding the same event (a reclaimed stale lease).
        db = env.tenant.session_factory()
        try:
            db.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(tenant_id)},
            )
            barrier.wait()
            env.transport.deliver(claimed, db)
            db.commit()
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in [pool.submit(deliver), pool.submit(deliver)]:
            future.result()

    assert len(PROBE.calls) == 1
    assert _effects(env, tenant_id) == [f"{tenant_id}:{MODULE}:ok:1"]
    rows = _timers(env, tenant_id, env.ok)
    assert [(r.generation, r.status) for r in rows] == [(1, "fired"), (2, "scheduled")]
    with env.tenant.tenant_session(tenant_id) as db:
        stale = db.scalar(select(func.count()).select_from(TimerRejection))
    assert stale == 1  # the loser was recorded as stale, ran nothing


# ── 3. crash before commit ──────────────────────────────────────────────────


def test_a_crash_after_the_handler_before_commit_retries_with_no_double_effect(
    env,
) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok)
    dispatcher_db = env.dispatcher.session_factory()
    try:
        (claimed,) = claim_batch(dispatcher_db, worker_id="runner-1")
        dispatcher_db.commit()
    finally:
        dispatcher_db.close()

    def attempt(*, commit: bool) -> None:
        db = env.tenant.session_factory()
        try:
            db.execute(
                text("SELECT set_config('app.current_tenant', :t, true)"),
                {"t": str(tenant_id)},
            )
            env.transport.deliver(claimed, db)
            if commit:
                db.commit()
            else:
                db.rollback()  # the process died before the commit
        finally:
            db.close()

    attempt(commit=False)
    assert len(PROBE.calls) == 1  # the handler did run ...
    assert _effects(env, tenant_id) == []  # ... but nothing of it survived
    assert [r.status for r in _timers(env, tenant_id, env.ok)] == ["scheduled"]

    attempt(commit=True)  # the relay's redelivery
    attempt(commit=True)  # and a further duplicate delivery
    assert len(PROBE.calls) == 2
    assert {c.idempotency_key for c in PROBE.calls} == {f"{tenant_id}:{MODULE}:ok:1"}
    assert _effects(env, tenant_id) == [f"{tenant_id}:{MODULE}:ok:1"]
    assert [r.status for r in _timers(env, tenant_id, env.ok)] == [
        "fired",
        "scheduled",
    ]


# ── 4. RLS isolation ────────────────────────────────────────────────────────


def test_each_tenant_run_sees_only_its_own_rows_and_dispatcher_reads_none(
    env,
) -> None:
    first = _make_tenant(env.scratch["admin"])
    second = _make_tenant(env.scratch["admin"])
    for tenant_id in (first, second):
        _arm(env, tenant_id, env.ok)
        with env.tenant.tenant_session(tenant_id) as db:
            admin.pause(db, tenant_id=tenant_id, entry=env.flaky, now=NOW, actor="t")

    assert _drain(env) == 2
    assert {c.tenant_id for c in PROBE.calls} == {first, second}
    for run_context, visible in zip(PROBE.calls, PROBE.visible_tenants, strict=True):
        assert visible == {run_context.tenant_id}
    assert sorted(_effects(env, first)) == [f"{first}:{MODULE}:ok:1"]
    assert sorted(_effects(env, second)) == [f"{second}:{MODULE}:ok:1"]

    dispatcher_db = env.dispatcher.session_factory()
    try:
        with pytest.raises(DBAPIError):
            dispatcher_db.execute(text("SELECT count(*) FROM mod_timers.timers"))
    finally:
        dispatcher_db.rollback()
        dispatcher_db.close()


# ── 5. retry, backoff, dead letter ──────────────────────────────────────────


def test_failures_back_off_then_dead_letter_with_audit_and_health(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    PROBE.fail = True
    _arm(env, tenant_id, env.flaky)

    assert _drain(env) == 1  # attempt 1 fails -> held for backoff, not charged
    assert _outbox_statuses(env) == ["pending"]
    assert _effects(env, tenant_id) == []  # the failed run's writes were undone
    assert [r.status for r in _timers(env, tenant_id, env.flaky)] == ["scheduled"]
    state = _state(env, tenant_id, env.flaky)
    assert state is not None
    assert (state.last_outcome, state.consecutive_failures) == ("failed", 1)
    assert state.last_detail == "RuntimeError"  # type name only, never the message
    assert env.sink.dead == []

    assert _drain(env) == 0  # not yet due again
    _make_due_again(env)
    assert _drain(env) == 1  # attempt 2 = max_attempts -> dead letter

    assert _outbox_statuses(env) == ["dead"]
    state = _state(env, tenant_id, env.flaky)
    assert state is not None
    assert state.last_outcome == "dead_letter" and state.dead_lettered_at is not None
    assert state.dead_letter_generation == 1
    with env.tenant.tenant_session(tenant_id) as db:
        audit_rows = list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "durable_timers.job.dead_letter"
                )
            )
        )
        assert len(audit_rows) == 1
        assert audit_rows[0].entity_id == f"{MODULE}:flaky"
        assert audit_rows[0].is_success is False
    assert len(env.sink.dead) == 1
    assert env.sink.dead[0].tenant_id == tenant_id
    assert env.sink.dead[0].attempts == 2 and env.sink.dead[0].generation == 1

    # No staff queue: the reconciler re-arms the dead generation by itself.
    with env.tenant.tenant_session(tenant_id) as db:
        assert (
            reconcile_tenant(db, tenant_id=tenant_id, catalogue=env.catalogue, now=NOW)
            >= 1
        )
    rows = _timers(env, tenant_id, env.flaky)
    assert [(r.generation, r.status) for r in rows] == [
        (1, "superseded"),
        (2, "scheduled"),
    ]
    # It is idempotent: a second pass arms nothing.
    with env.tenant.tenant_session(tenant_id) as db:
        reconcile_tenant(db, tenant_id=tenant_id, catalogue=env.catalogue, now=NOW)
    assert len(_timers(env, tenant_id, env.flaky)) == 2


# ── 6. DST / timezone cron ──────────────────────────────────────────────────


def test_cron_is_evaluated_in_the_tenant_timezone_across_dst(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    new_york = ZoneInfo("America/New_York")
    monkeypatch.setattr(jobs_module, "tenant_timezone", lambda db, tenant: new_york)

    def due_after(after: datetime) -> datetime:
        with env.tenant.tenant_session(tenant_id) as db:
            return arm(
                db, tenant_id=tenant_id, entry=env.cron, now=after, after=after
            ).due_at

    # Spring forward (2029-03-11): 02:30 does not exist; it runs once, shifted.
    assert due_after(datetime(2029, 3, 11, 6, 0, tzinfo=UTC)) == datetime(
        2029, 3, 11, 7, 30, tzinfo=UTC
    )
    # Ordinary winter day: 02:30 EST is 07:30 UTC.
    assert due_after(datetime(2029, 1, 10, 0, 0, tzinfo=UTC)) == datetime(
        2029, 1, 10, 7, 30, tzinfo=UTC
    )
    # A summer day: 02:30 EDT is 06:30 UTC.
    assert due_after(datetime(2029, 7, 10, 0, 0, tzinfo=UTC)) == datetime(
        2029, 7, 10, 6, 30, tzinfo=UTC
    )
    # Fall back (2029-11-04): 01:30 happens twice; a 01:30 job runs once, at the
    # first pass, and the repeated hour does not fire it again.
    cron_once = JobEntry(
        MODULE, _spec("late", "handler_ok", interval_seconds=None, cron="30 1 * * *")
    )
    with env.tenant.tenant_session(tenant_id) as db:
        first = arm(
            db,
            tenant_id=tenant_id,
            entry=cron_once,
            now=datetime(2029, 11, 4, 4, 0, tzinfo=UTC),
        ).due_at
    assert first == datetime(2029, 11, 4, 5, 30, tzinfo=UTC)
    with env.tenant.tenant_session(tenant_id) as db:
        second = arm(db, tenant_id=tenant_id, entry=cron_once, now=first).due_at
    assert second == datetime(2029, 11, 5, 6, 30, tzinfo=UTC)


# ── 7. pause ────────────────────────────────────────────────────────────────


def test_pause_stops_scheduling_without_losing_the_generation(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok)
    with env.tenant.tenant_session(tenant_id) as db:
        assert admin.pause(db, tenant_id=tenant_id, entry=env.ok, now=NOW, actor="u")
        assert not admin.pause(
            db, tenant_id=tenant_id, entry=env.ok, now=NOW, actor="u"
        )
        with pytest.raises(ConflictError):
            admin.run_now(db, tenant_id=tenant_id, entry=env.ok, now=NOW)

    assert _drain(env) == 1
    assert PROBE.calls == []  # the handler never ran
    rows = _timers(env, tenant_id, env.ok)
    assert [(r.generation, r.status) for r in rows] == [(1, "scheduled")]
    state = _state(env, tenant_id, env.ok)
    assert state is not None
    assert state.paused and state.last_outcome == "skipped_paused"

    with env.tenant.tenant_session(tenant_id) as db:
        assert (
            reconcile_tenant(db, tenant_id=tenant_id, catalogue=env.catalogue, now=NOW)
            == 1  # only the flaky job; the paused one is left alone
        )
    assert len(_timers(env, tenant_id, env.ok)) == 1

    with env.tenant.tenant_session(tenant_id) as db:
        assert admin.resume(db, tenant_id=tenant_id, entry=env.ok, now=NOW)
    rows = _timers(env, tenant_id, env.ok)
    assert [(r.generation, r.status) for r in rows] == [
        (1, "superseded"),
        (2, "scheduled"),
    ]
    state = _state(env, tenant_id, env.ok)
    assert state is not None and not state.paused


# ── 8. run now ──────────────────────────────────────────────────────────────


def test_run_now_supersedes_the_live_generation_and_runs_it(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok, due_at=NOW + timedelta(days=1))
    assert _drain(env) == 0

    with env.tenant.tenant_session(tenant_id) as db:
        scheduled = admin.run_now(db, tenant_id=tenant_id, entry=env.ok, now=PAST)
    assert scheduled.generation == 2

    assert _drain(env) == 1
    assert [c.generation for c in PROBE.calls] == [2]
    rows = _timers(env, tenant_id, env.ok)
    assert [(r.generation, r.status) for r in rows] == [
        (1, "superseded"),
        (2, "fired"),
        (3, "scheduled"),
    ]


# ── 9. built-in purge jobs ──────────────────────────────────────────────────


def test_builtin_purges_delete_only_expired_terminal_rows(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok)
    assert _drain(env) == 1  # leaves one `sent` outbox row and one fired generation
    with env.admin.connect() as connection:
        connection.execute(
            text(
                "UPDATE public.outbox_events SET sent_at = :past WHERE status = 'sent'"
            ),
            {"past": PAST},
        )
    context = JobRunContext(
        tenant_id=tenant_id,
        module_code="durable_timers",
        job_code="purge",
        generation=1,
        timer_id=UUID(int=0),
        idempotency_key="k",
        attempt=1,
        scheduled_for=NOW,
        started_at=NOW + timedelta(days=60),
    )
    scope = TenantScope(tenant_id)
    with env.tenant.tenant_session(tenant_id) as db:
        assert purge_published_outbox(db, scope, context).counters["deleted"] == 1
        assert purge_timer_history(db, scope, context).counters["deleted"] == 1
    rows = _timers(env, tenant_id, env.ok)
    assert [(r.generation, r.status) for r in rows] == [(2, "scheduled")]


# ── R2 follow-ups ───────────────────────────────────────────────────────────


class _RaisingSink:
    def job_dead_lettered(self, dead_letter: JobDeadLetter) -> None:
        raise ConnectionError("alerting channel is down")


def _fail_twice_to_dead(env: SimpleNamespace, tenant_id: UUID) -> None:
    PROBE.fail = True
    _arm(env, tenant_id, env.flaky)
    assert _drain(env) == 1
    _make_due_again(env)
    assert _drain(env) == 1


def test_a_failing_health_sink_does_not_keep_the_dead_event_claimed(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    env.transport = JobDispatchTransport(
        catalogue=env.catalogue, health=_RaisingSink(), clock=lambda: NOW
    )
    _fail_twice_to_dead(env, tenant_id)

    # The sink error was contained: the row is settled dead, not left claimed to
    # be reclaimed and re-run every lease period.
    assert _outbox_statuses(env) == ["dead"]
    state = _state(env, tenant_id, env.flaky)
    assert state is not None and state.last_outcome == "dead_letter"
    with env.tenant.tenant_session(tenant_id) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "durable_timers.job.dead_letter")
            )
            == 1
        )


def test_an_already_dead_lettered_event_is_never_run_again(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _fail_twice_to_dead(env, tenant_id)
    state = _state(env, tenant_id, env.flaky)
    assert state is not None and state.dead_letter_event_id is not None
    with env.admin.connect() as connection:
        payload = connection.execute(
            text("SELECT payload FROM public.outbox_events WHERE id = :id"),
            {"id": state.dead_letter_event_id},
        ).scalar_one()
    event = ClaimedEvent(
        id=state.dead_letter_event_id,
        tenant_id=tenant_id,
        event_type="durable_timers.job_due.v1",
        payload=payload,
        attempts=0,
        correlation_id=None,
        last_error=None,
    )
    PROBE.fail = False
    db = env.tenant.session_factory()
    try:
        db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(tenant_id)},
        )
        with pytest.raises(DeliveryTerminal):
            env.transport.deliver(event, db)
    finally:
        db.close()
    assert _effects(env, tenant_id) == []  # the handler did not run again


def test_a_purged_outbox_row_means_the_job_is_armed_again(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok, due_at=NOW + timedelta(days=1))
    with env.admin.connect() as connection:
        connection.execute(text("DELETE FROM public.outbox_events"))
    with env.tenant.tenant_session(tenant_id) as db:
        assert (
            reconcile_tenant(db, tenant_id=tenant_id, catalogue=env.catalogue, now=NOW)
            >= 1
        )
    rows = _timers(env, tenant_id, env.ok)
    assert [(r.generation, r.status) for r in rows] == [
        (1, "superseded"),
        (2, "scheduled"),
    ]


def test_the_database_role_split_is_read_back_not_assumed(env) -> None:
    tenant, dispatcher = verify_role_split(
        tenant_factory=env.tenant.session_factory,
        dispatcher_factory=env.dispatcher.session_factory,
    )
    assert (tenant.name, dispatcher.name) == ("app_user", "outbox_dispatcher")
    assert not tenant.superuser and not tenant.bypass_rls

    with pytest.raises(RoleSplitError, match="must differ"):
        verify_role_split(
            tenant_factory=env.tenant.session_factory,
            dispatcher_factory=env.tenant.session_factory,
        )
    privileged = DatabaseRuntime.from_urls(database_url=env.scratch["admin"])
    try:
        with pytest.raises(RoleSplitError, match=r"BYPASSRLS|superuser"):
            verify_role_split(
                tenant_factory=privileged.session_factory,
                dispatcher_factory=env.dispatcher.session_factory,
            )
    finally:
        privileged.engine.dispose()
        privileged.platform_engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT count(*) FROM mod_timers.timers",
        "SELECT count(*) FROM mod_timers.job_states",
        "SELECT count(*) FROM public.tenants",
        "SELECT count(*) FROM public.outbox_events",
        "SELECT count(*) FROM public.audit_events",
    ],
)
def test_the_dispatcher_credential_reads_no_tenant_or_module_table(
    env, statement: str
) -> None:
    db = env.dispatcher.session_factory()
    try:
        with pytest.raises(DBAPIError):
            db.execute(text(statement))
    finally:
        db.close()


def test_an_inactive_tenant_runs_nothing_and_arms_nothing(env) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok)
    with env.admin.connect() as connection:
        connection.execute(
            text("UPDATE public.tenants SET is_active = false WHERE id = :id"),
            {"id": tenant_id},
        )
    assert _drain(env) == 1
    assert PROBE.calls == []
    rows = _timers(env, tenant_id, env.ok)
    assert [(r.generation, r.status) for r in rows] == [(1, "scheduled")]
    assert _outbox_statuses(env) == ["sent"]


# ── typed claim: foreign events are never claimed ───────────────────────────

FOREIGN_EVENT = "tests.foreign_module.thing_due.v1"


def test_a_foreign_outbox_event_is_never_claimed_and_is_left_for_its_own_relay(
    env,
) -> None:
    from dotmac_kernel.messaging.outbox import enqueue_event

    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok)
    with env.tenant.tenant_session(tenant_id) as db:
        pending = enqueue_event(
            db, tenant_id=tenant_id, event_type=FOREIGN_EVENT, payload={"k": 1}
        )
        pending.available_at = PAST
        stale = enqueue_event(
            db, tenant_id=tenant_id, event_type=FOREIGN_EVENT, payload={"k": 2}
        )
        stale.available_at = PAST
        stale_id, pending_id = stale.id, pending.id
    with env.admin.connect() as connection:
        # A foreign row whose lease went stale: the reclaim arm must not take it.
        connection.execute(
            text(
                "UPDATE public.outbox_events SET status = 'claimed', "
                "leased_by = 'other-relay', leased_at = :old WHERE id = :id"
            ),
            {"old": PAST, "id": stale_id},
        )

    assert _drain(env) == 1  # only the job event
    assert [c.generation for c in PROBE.calls] == [1]
    assert _drain(env) == 0  # nothing else is claimable by this runner

    with env.admin.connect() as connection:
        rows = {
            row.id: row
            for row in connection.execute(
                text(
                    "SELECT id, status, attempts, leased_by, last_error "
                    "FROM public.outbox_events WHERE event_type = :t"
                ),
                {"t": FOREIGN_EVENT},
            )
        }
    assert (rows[pending_id].status, rows[pending_id].attempts) == ("pending", 0)
    assert rows[pending_id].leased_by is None and rows[pending_id].last_error is None
    assert (rows[stale_id].status, rows[stale_id].leased_by) == (
        "claimed",
        "other-relay",
    )
    with env.tenant.tenant_session(tenant_id) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "durable_timers.job.dead_letter")
            )
            == 0
        )


# ── R2 r2 fixes: N1 purge scope, N3 positive role identity, N5 exhausted ────


def _purge_context(tenant_id: UUID) -> JobRunContext:
    return JobRunContext(
        tenant_id=tenant_id,
        module_code="durable_timers",
        job_code="purge",
        generation=1,
        timer_id=UUID(int=0),
        idempotency_key="k",
        attempt=1,
        scheduled_for=NOW,
        started_at=NOW + timedelta(days=60),
    )


def test_the_outbox_purge_never_deletes_another_modules_sent_rows(env) -> None:
    from dotmac_kernel.messaging.outbox import enqueue_event

    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.ok)
    assert _drain(env) == 1  # one settled job_due row of our own
    with env.tenant.tenant_session(tenant_id) as db:
        foreign = enqueue_event(
            db, tenant_id=tenant_id, event_type=FOREIGN_EVENT, payload={"k": 1}
        )
        foreign_id = foreign.id
    with env.admin.connect() as connection:
        connection.execute(
            text(
                "UPDATE public.outbox_events SET status = 'sent', sent_at = :past "
                "WHERE status = 'sent' OR id = :foreign"
            ),
            {"past": PAST, "foreign": foreign_id},
        )
    with env.tenant.tenant_session(tenant_id) as db:
        outcome = purge_published_outbox(
            db, TenantScope(tenant_id), _purge_context(tenant_id)
        )
    assert outcome.counters["deleted"] == 1
    with env.admin.connect() as connection:
        remaining = list(
            connection.execute(
                text("SELECT event_type, status FROM public.outbox_events")
            )
        )
    assert [(row.event_type, row.status) for row in remaining] == [
        (FOREIGN_EVENT, "sent")
    ]


@pytest.fixture
def login_roles(env) -> Iterator[object]:
    """Create separate LOGIN roles (cluster-level) and drop them afterwards."""
    server = create_engine(_superuser_url(), isolation_level="AUTOCOMMIT")
    dbname = env.scratch["admin"].rpartition("/")[2]
    created: list[str] = []

    def make(*member_of: str) -> str:
        import uuid

        name = f"jr_login_{uuid.uuid4().hex[:10]}"
        with server.connect() as connection:
            connection.execute(
                text(f'CREATE ROLE "{name}" LOGIN IN ROLE ' + ", ".join(member_of))
            )
            connection.execute(
                text(f'GRANT CONNECT ON DATABASE "{dbname}" TO "{name}"')
            )
        created.append(name)
        return _url_for(_superuser_url(), dbname, user=name)

    try:
        yield make
    finally:
        with server.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE usename = ANY(:names)"
                ),
                {"names": created},
            )
            for name in created:
                connection.execute(
                    text(f'REVOKE CONNECT ON DATABASE "{dbname}" FROM "{name}"')
                )
                connection.execute(text(f'DROP ROLE IF EXISTS "{name}"'))
        server.dispose()


def _runtime(url: str) -> DatabaseRuntime:
    return DatabaseRuntime.from_urls(database_url=url)


def test_a_separate_dispatcher_login_in_the_dispatcher_group_boots_and_claims(
    env, login_roles
) -> None:
    runtime = _runtime(login_roles("outbox_dispatcher"))
    try:
        tenant, dispatcher = verify_role_split(
            tenant_factory=env.tenant.session_factory,
            dispatcher_factory=runtime.session_factory,
        )
        assert tenant.name == "app_user" and dispatcher.name.startswith("jr_login_")
        tenant_id = _make_tenant(env.scratch["admin"])
        _arm(env, tenant_id, env.ok)
        runner = JobRunner(
            catalogue=env.catalogue,
            transport=env.transport,
            dispatcher_session_factory=runtime.session_factory,
            tenant_runtime=env.tenant,
            worker_id="runner-login",
            clock=lambda: NOW,
            policy=RelayPolicy(batch_size=1),
        )
        assert runner.drain_once() == 1
    finally:
        runtime.engine.dispose()
        runtime.platform_engine.dispose()


def test_a_tenant_login_in_the_dispatcher_group_is_refused(env, login_roles) -> None:
    runtime = _runtime(login_roles("app_user", "outbox_dispatcher"))
    try:
        with pytest.raises(RoleSplitError, match="member of 'outbox_dispatcher'"):
            verify_role_split(
                tenant_factory=runtime.session_factory,
                dispatcher_factory=env.dispatcher.session_factory,
            )
    finally:
        runtime.engine.dispose()
        runtime.platform_engine.dispose()


def test_a_tenant_login_in_the_admin_group_is_refused(env, login_roles) -> None:
    runtime = _runtime(login_roles("app_user", "app_admin"))
    try:
        with pytest.raises(RoleSplitError, match="member of 'app_admin'"):
            verify_role_split(
                tenant_factory=runtime.session_factory,
                dispatcher_factory=env.dispatcher.session_factory,
            )
    finally:
        runtime.engine.dispose()
        runtime.platform_engine.dispose()


def test_a_dispatcher_login_without_the_typed_claim_grant_is_refused(
    env, login_roles
) -> None:
    runtime = _runtime(login_roles("app_user"))  # connects, but no EXECUTE
    try:
        with pytest.raises(RoleSplitError, match="cannot EXECUTE the typed"):
            verify_role_split(
                tenant_factory=env.tenant.session_factory,
                dispatcher_factory=runtime.session_factory,
            )
    finally:
        runtime.engine.dispose()
        runtime.platform_engine.dispose()


def test_an_owning_tenant_role_is_refused(env) -> None:
    owner = _runtime(env.scratch["admin"])  # app_admin owns the tables
    try:
        with pytest.raises(RoleSplitError, match=r"owns \d+ relation"):
            verify_role_split(
                tenant_factory=owner.session_factory,
                dispatcher_factory=env.dispatcher.session_factory,
            )
    finally:
        owner.engine.dispose()
        owner.platform_engine.dispose()


def test_a_failing_audit_in_the_dead_letter_hook_never_reruns_the_handler(
    env, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant_id = _make_tenant(env.scratch["admin"])
    _arm(env, tenant_id, env.flaky)
    PROBE.fail = True
    _drain(env)  # attempt 1: deferred
    _make_due_again(env)

    def broken_audit(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("audit down")

    monkeypatch.setattr(JobDispatchTransport, "_audit", staticmethod(broken_audit))
    _drain(env)  # attempt 2 (== max): fails, exhausted recorded, hook raises
    state = _state(env, tenant_id, env.flaky)
    assert state is not None and state.exhausted_event_id is not None
    failures_after_final = state.consecutive_failures
    assert failures_after_final == 2
    for _ in range(2):  # the lease goes stale and the relay re-delivers
        with env.admin.connect() as connection:
            connection.execute(
                text(
                    "UPDATE public.outbox_events SET leased_at = :past "
                    "WHERE status = 'claimed'"
                ),
                {"past": PAST},
            )
        _drain(env)
    state = _state(env, tenant_id, env.flaky)
    # Every handler run records a failure; none more was recorded.
    assert state is not None and state.consecutive_failures == failures_after_final
