"""Liveness files for the deploy health probe.

The runner writes one small JSON file atomically (temp file then rename) in a
writable directory such as ``/tmp``.  Two timestamps matter to a probe:

* ``alive_at`` is refreshed by a daemon thread every few seconds, so a handler
  that runs for minutes does not look like a dead process (the ``worker-ping``);
* ``tick_at`` is set when a reconcile pass finishes, so a runner that is alive
  but no longer scheduling shows up as stale (the ``scheduler-tick``).

All instants are supplied by the caller; this module reads no clock.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = "/tmp/dotmac-durable-timers.heartbeat"  # noqa: S108 - probe file, no secret
BEAT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class HeartbeatState:
    pid: int
    worker_id: str
    alive_at: float
    tick_at: float | None


def read_heartbeat(path: str | os.PathLike[str]) -> HeartbeatState:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tick = data.get("tick_at")
    return HeartbeatState(
        pid=int(data["pid"]),
        worker_id=str(data["worker_id"]),
        alive_at=float(data["alive_at"]),
        tick_at=None if tick is None else float(tick),
    )


class Heartbeat:
    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        worker_id: str,
        clock: Callable[[], datetime],
        beat_seconds: float = BEAT_SECONDS,
    ) -> None:
        self._path = Path(path)
        self._worker_id = worker_id
        self._clock = clock
        self._beat = beat_seconds
        self._tick_at: float | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _write(self) -> None:
        with self._lock:
            payload = {
                "pid": os.getpid(),
                "worker_id": self._worker_id,
                "alive_at": self._clock().timestamp(),
                "tick_at": self._tick_at,
            }
            temp = self._path.with_name(self._path.name + f".{os.getpid()}.tmp")
            temp.write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(temp, 0o600)
            os.replace(temp, self._path)

    def tick(self) -> None:
        """Record that a reconcile pass finished."""
        self._tick_at = self._clock().timestamp()
        self._write()

    def start(self, stop: threading.Event) -> None:
        self._write()

        def beat() -> None:
            while not stop.wait(self._beat):
                try:
                    self._write()
                except OSError:
                    pass  # a full /tmp must not kill the runner; the probe will notice

        self._thread = threading.Thread(target=beat, name="heartbeat", daemon=True)
        self._thread.start()

    def remove(self) -> None:
        self._path.unlink(missing_ok=True)


__all__ = [
    "BEAT_SECONDS",
    "DEFAULT_PATH",
    "Heartbeat",
    "HeartbeatState",
    "read_heartbeat",
]
