"""Liveness files for the deploy health probe.

The runner writes one small JSON file atomically (temp file then rename) in a
writable directory such as ``/tmp``.  Two timestamps matter to a probe:

* ``alive_at`` is refreshed by a daemon thread every few seconds, so a handler
  that runs for minutes does not look like a dead process (the ``worker-ping``);
* ``tick_at`` is set when a reconcile pass finishes with NO tenant failure;
* ``drain_ok_at`` is set when a drain pass returns without raising (claiming
  nothing counts), so a runner whose dispatcher login lost its grant or
  password shows up as stale (the ``scheduler-tick`` checks both).

All instants are supplied by the caller; this module reads no clock.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_PATH = os.path.join(tempfile.gettempdir(), "dotmac-durable-timers.heartbeat")
BEAT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class HeartbeatState:
    pid: int
    worker_id: str
    alive_at: float
    tick_at: float | None
    drain_ok_at: float | None = None


def read_heartbeat(path: str | os.PathLike[str]) -> HeartbeatState:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    tick = data.get("tick_at")
    drain = data.get("drain_ok_at")
    return HeartbeatState(
        pid=int(data["pid"]),
        worker_id=str(data["worker_id"]),
        alive_at=float(data["alive_at"]),
        tick_at=None if tick is None else float(tick),
        drain_ok_at=None if drain is None else float(drain),
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
        self._drain_ok_at: float | None = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def _write(self) -> None:
        with self._lock:
            payload = {
                "pid": os.getpid(),
                "worker_id": self._worker_id,
                "alive_at": self._clock().timestamp(),
                "tick_at": self._tick_at,
                "drain_ok_at": self._drain_ok_at,
            }
            # mkstemp creates the file O_EXCL with mode 0600 and a random name,
            # so a planted symlink in a shared directory is never followed.
            fd, temp = tempfile.mkstemp(
                dir=self._path.parent, prefix=self._path.name + ".", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(payload))
                os.replace(temp, self._path)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise

    def drain_ok(self) -> None:
        """Record that a drain pass returned without raising."""
        previous = self._drain_ok_at
        self._drain_ok_at = self._clock().timestamp()
        # Idle polling is once a second, so this does not write every call: it
        # writes on the first success and again once a beat has elapsed since the
        # previous stamp. Between those writes the stamp is only held in memory
        # and the beat thread writes the live value every ``beat_seconds``.
        if previous is None or self._drain_ok_at - previous >= self._beat:
            self._write()

    def tick(self) -> None:
        """Record that a reconcile pass finished with no tenant failure."""
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
