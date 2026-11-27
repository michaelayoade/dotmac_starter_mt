"""Health probe commands for the deploy health check (no network, no database).

    python -m dotmac_durable_timers.probe ping --max-age 45
    python -m dotmac_durable_timers.probe tick --max-age 300

``ping`` (worker-ping) passes while the runner process is alive: the heartbeat
file's ``alive_at`` is recent and its pid still exists.  ``tick``
(scheduler-tick) passes while reconcile passes keep finishing without a tenant
failure (``tick_at`` recent) AND drain passes keep returning without raising
(``drain_ok_at`` recent).  Exit 0 healthy, 1 unhealthy (the reason goes to
stderr). The probe is
the one module that reads the clock for this purpose.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime

from dotmac_durable_timers.heartbeat import DEFAULT_PATH, read_heartbeat

EXIT_UNHEALTHY = 1


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def check(command: str, path: str, max_age: float, *, now: float) -> str | None:
    """None when healthy, otherwise the reason it is not."""
    try:
        state = read_heartbeat(path)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return f"no readable heartbeat at {path!r}: {type(exc).__name__}"
    if command == "ping":
        if now - state.alive_at > max_age:
            return f"runner not alive: last beat {now - state.alive_at:.0f}s ago"
        if not _pid_alive(state.pid):
            return f"runner pid {state.pid} no longer exists"
        return None
    if state.tick_at is None:
        return "no reconcile pass has finished yet"
    if now - state.tick_at > max_age:
        age = now - state.tick_at
        return f"scheduler stalled: last clean reconcile {age:.0f}s ago"
    if state.drain_ok_at is None:
        return "no drain pass has succeeded yet"
    if now - state.drain_ok_at > max_age:
        age = now - state.drain_ok_at
        return f"drain failing: last clean drain {age:.0f}s ago"
    return None


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m dotmac_durable_timers.probe")
    parser.add_argument("command", choices=("ping", "tick"))
    parser.add_argument("--heartbeat-file", default=DEFAULT_PATH)
    parser.add_argument("--max-age", type=float, default=None)
    args = parser.parse_args(argv)
    max_age = (
        args.max_age
        if args.max_age is not None
        else (45.0 if args.command == "ping" else 300.0)
    )
    reason = check(
        args.command,
        args.heartbeat_file,
        max_age,
        now=datetime.now(UTC).timestamp(),
    )
    if reason is None:
        return 0
    print(f"unhealthy: {reason}", file=sys.stderr)
    return EXIT_UNHEALTHY


if __name__ == "__main__":
    raise SystemExit(main())
