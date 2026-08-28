"""Behaviour tests for the disposable rehearsal's non-HTTP role probes."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

APP = Path(__file__).parents[2] / "scripts" / "rehearsal" / "app.py"


@pytest.fixture
def rehearsal_app() -> ModuleType:
    spec = importlib.util.spec_from_file_location("deployment_rehearsal_app", APP)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_worker_ping_fails_after_the_running_role_is_made_unhealthy(
    rehearsal_app: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker = tmp_path / "worker"
    monkeypatch.setattr(rehearsal_app, "WORKER_MARKER", str(marker))
    monkeypatch.setattr(
        rehearsal_app.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert rehearsal_app.cmd_worker() == 0
    assert rehearsal_app.cmd_worker_ping() == 0
    assert rehearsal_app.cmd_worker_make_unhealthy() == 0
    assert rehearsal_app.cmd_worker_ping() == 1


def test_scheduler_probe_changes_from_fresh_to_stale_without_stopping_the_role(
    rehearsal_app: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tick = tmp_path / "tick"
    pause = tmp_path / "pause"
    monkeypatch.setattr(rehearsal_app, "SCHEDULER_TICK", str(tick))
    monkeypatch.setattr(rehearsal_app, "SCHEDULER_PAUSE", str(pause))
    monkeypatch.setattr(rehearsal_app.time, "time", lambda: 4_000)
    monkeypatch.setattr(
        rehearsal_app.time,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt),
    )

    assert rehearsal_app.cmd_scheduler() == 0
    assert tick.read_text(encoding="utf-8") == "4000"
    assert rehearsal_app.cmd_scheduler_last_tick() == 0
    assert rehearsal_app.cmd_scheduler_make_stale() == 0
    assert pause.is_file()
    assert tick.read_text(encoding="utf-8") == "400"
