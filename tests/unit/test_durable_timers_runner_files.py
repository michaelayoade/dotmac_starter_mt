"""DSN files, heartbeat file and the health probe (no database)."""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from dotmac_durable_timers.dsnfile import DsnFileError, read_dsn_file, resolve_dsn
from dotmac_durable_timers.heartbeat import Heartbeat, read_heartbeat
from dotmac_durable_timers.probe import check, main
from dotmac_durable_timers.run import build_parser

SECRET = "postgresql+psycopg://user:s3cr3t-value@db.example/app"


def _secret_file(tmp_path: Path, mode: int = 0o400, text: str = SECRET) -> Path:
    path = tmp_path / "dsn"
    path.write_text(text + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_a_0400_regular_file_owned_by_us_is_read_and_stripped(tmp_path: Path) -> None:
    assert read_dsn_file(_secret_file(tmp_path)) == SECRET


@pytest.mark.parametrize("mode", [0o600, 0o440, 0o444, 0o404, 0o200])
def test_any_mode_other_than_0400_is_refused_without_echoing_the_value(
    tmp_path: Path, mode: int
) -> None:
    with pytest.raises(DsnFileError) as raised:
        read_dsn_file(_secret_file(tmp_path, mode))
    assert "s3cr3t" not in str(raised.value)
    assert "0400" in str(raised.value)


def test_a_symlink_is_refused_even_when_its_target_is_valid(tmp_path: Path) -> None:
    target = _secret_file(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(DsnFileError) as raised:
        read_dsn_file(link)
    assert "s3cr3t" not in str(raised.value)


def test_missing_empty_multiline_and_directory_inputs_are_refused(
    tmp_path: Path,
) -> None:
    with pytest.raises(DsnFileError):
        read_dsn_file(tmp_path / "absent")
    with pytest.raises(DsnFileError, match="empty"):
        read_dsn_file(_secret_file(tmp_path, text="   "))
    two = tmp_path / "two"
    two.write_text("a\nb\n", encoding="utf-8")
    two.chmod(0o400)
    with pytest.raises(DsnFileError, match="one line"):
        read_dsn_file(two)
    with pytest.raises(DsnFileError):
        read_dsn_file(tmp_path)


def test_the_path_comes_from_the_argument_or_a_file_variable_never_a_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _secret_file(tmp_path)
    monkeypatch.delenv("X_DSN_FILE", raising=False)
    monkeypatch.setenv("X_DSN", SECRET)  # a plain-value variable is ignored
    with pytest.raises(DsnFileError, match="no DSN file"):
        resolve_dsn(None, "X_DSN_FILE")
    assert resolve_dsn(str(path), "X_DSN_FILE") == SECRET
    monkeypatch.setenv("X_DSN_FILE", str(path))
    assert resolve_dsn(None, "X_DSN_FILE") == SECRET


def test_the_command_line_takes_file_paths_and_a_batch_of_one_only() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["--assembly", "a:b", "--dispatcher-dsn-file", "/x", "--app-dsn-file", "/y"]
    )
    assert (args.dispatcher_dsn_file, args.database_dsn_file) == ("/x", "/y")
    assert args.batch_size == 1
    with pytest.raises(SystemExit):
        parser.parse_args(["--assembly", "a:b", "--batch-size", "5"])


def _beat(tmp_path: Path, now: datetime) -> tuple[Heartbeat, Path]:
    path = tmp_path / "hb"
    return Heartbeat(path, worker_id="w1", clock=lambda: now), path


def test_the_heartbeat_file_carries_liveness_and_the_last_reconcile(
    tmp_path: Path,
) -> None:
    now = datetime(2029, 1, 2, 3, 4, 5, tzinfo=UTC)
    heartbeat, path = _beat(tmp_path, now)
    stop = threading.Event()
    heartbeat.start(stop)
    try:
        first = read_heartbeat(path)
        assert first.pid == os.getpid() and first.tick_at is None
        heartbeat.tick()
        assert read_heartbeat(path).tick_at == now.timestamp()
        assert oct(path.stat().st_mode & 0o777) == "0o600"
    finally:
        stop.set()
    heartbeat.remove()
    assert not path.exists()


def test_ping_and_tick_judge_alive_and_scheduling_separately(tmp_path: Path) -> None:
    now = datetime(2029, 1, 2, tzinfo=UTC)
    path = tmp_path / "hb"
    path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "worker_id": "w1",
                "alive_at": (now - timedelta(seconds=5)).timestamp(),
                "tick_at": (now - timedelta(seconds=900)).timestamp(),
            }
        ),
        encoding="utf-8",
    )
    stamp = now.timestamp()
    assert check("ping", str(path), 45, now=stamp) is None
    assert "stalled" in (check("tick", str(path), 300, now=stamp) or "")
    assert check("tick", str(path), 1000, now=stamp) is None
    assert "not alive" in (check("ping", str(path), 1, now=stamp + 60) or "")


def test_a_dead_pid_or_missing_file_or_no_tick_is_unhealthy(tmp_path: Path) -> None:
    stamp = datetime(2029, 1, 2, tzinfo=UTC).timestamp()
    assert "no readable heartbeat" in (
        check("ping", str(tmp_path / "absent"), 45, now=stamp) or ""
    )
    path = tmp_path / "hb"
    path.write_text(
        json.dumps(
            {"pid": 2**22 + 7, "worker_id": "w", "alive_at": stamp, "tick_at": None}
        ),
        encoding="utf-8",
    )
    assert "no longer exists" in (check("ping", str(path), 45, now=stamp) or "")
    assert "no reconcile" in (check("tick", str(path), 45, now=stamp) or "")


def test_the_probe_exit_code_is_one_when_unhealthy(tmp_path: Path) -> None:
    assert main(["ping", "--heartbeat-file", str(tmp_path / "absent")]) == 1
