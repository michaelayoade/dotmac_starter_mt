"""The runner loop stamps the heartbeat honestly; the role check's database
comparison (no database: stub sessions)."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from dotmac_durable_timers.rolecheck import RoleSplitError, verify_role_split
from dotmac_durable_timers.runner import JobRunner


class _Beat:
    def __init__(self) -> None:
        self.ticks = 0
        self.drains = 0
        self.removed = False

    def start(self, stop: threading.Event) -> None:
        pass

    def tick(self) -> None:
        self.ticks += 1

    def drain_ok(self) -> None:
        self.drains += 1

    def remove(self) -> None:
        self.removed = True


def _runner(beat: _Beat) -> JobRunner:
    return JobRunner(
        catalogue=None,  # type: ignore[arg-type]
        transport=None,  # type: ignore[arg-type]
        dispatcher_session_factory=lambda: None,  # type: ignore[arg-type,return-value]
        tenant_runtime=None,  # type: ignore[arg-type]
        worker_id="w",
        clock=lambda: None,  # type: ignore[arg-type,return-value]
        poll_interval_seconds=0,
        reconcile_interval_seconds=0,
        heartbeat=beat,  # type: ignore[arg-type]
    )


def _run(runner: JobRunner, *, failures: int, drain_raises: bool) -> None:
    stop = threading.Event()
    calls = {"n": 0}

    def reconcile() -> int:
        runner.last_reconcile_failures = failures
        return 0

    def drain_once() -> int:
        calls["n"] += 1
        if calls["n"] >= 3:
            stop.set()
        if drain_raises:
            raise ConnectionError("dispatcher down")
        return 0

    runner.reconcile = reconcile  # type: ignore[method-assign]
    runner.drain_once = drain_once  # type: ignore[method-assign]
    runner.run_forever(stop)


def test_a_clean_loop_stamps_both_the_tick_and_the_drain() -> None:
    beat = _Beat()
    _run(_runner(beat), failures=0, drain_raises=False)
    assert beat.ticks >= 1 and beat.drains >= 1 and beat.removed


def test_a_tenant_failure_withholds_the_tick_but_not_the_drain() -> None:
    beat = _Beat()
    _run(_runner(beat), failures=1, drain_raises=False)
    assert beat.ticks == 0 and beat.drains >= 1


def test_a_failing_drain_never_stamps_the_drain() -> None:
    beat = _Beat()
    _run(_runner(beat), failures=0, drain_raises=True)
    assert beat.drains == 0 and beat.ticks >= 1


class _Session:
    def __init__(self, role: str, database: str) -> None:
        self._role = role
        self._database = database

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def execute(self, *_args: object) -> SimpleNamespace:
        row = (self._role, False, False)
        return SimpleNamespace(one=lambda: row)

    def scalar(self, statement: object, _params: object = None) -> object:
        sql = str(statement)
        if "has_function_privilege" in sql:
            return True
        if "current_database" in sql:
            return self._database
        if "count(*)" in sql:
            return 0
        return False  # no table read, no group membership


def test_two_dsns_naming_different_databases_are_refused() -> None:
    with pytest.raises(RoleSplitError, match="different databases"):
        verify_role_split(
            tenant_factory=lambda: _Session("app_user", "one"),  # type: ignore[arg-type,return-value]
            dispatcher_factory=lambda: _Session("outbox_dispatcher", "two"),  # type: ignore[arg-type,return-value]
        )
    tenant, dispatcher = verify_role_split(
        tenant_factory=lambda: _Session("app_user", "one"),  # type: ignore[arg-type,return-value]
        dispatcher_factory=lambda: _Session("outbox_dispatcher", "one"),  # type: ignore[arg-type,return-value]
    )
    assert (tenant.name, dispatcher.name) == ("app_user", "outbox_dispatcher")
