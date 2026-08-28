#!/usr/bin/env python3
"""The disposable product `scripts/deployment_rehearsal.sh` deploys.

This is not a reference application — it is the smallest thing that is still
GENUINE: a process that answers liveness without touching a dependency,
answers readiness only when told to, and can migrate a real database with a
real owner/online credential split. Every behaviour it has exists because one
of the fourteen ordered rehearsal steps in
`docs/inventories/deployment-foundation-rehearsal.md` needs it to be true
against real bytes, not asserted against a fake.

Modes are selected by argv (never by inspecting the environment — the
compose `command:` array in `scripts/rehearsal/product.toml` says explicitly
which one runs, the same way `deploy/product.toml` does for the real app):

- (no migration flag) — serve HTTP. `/health/live` is DB-free and always 200,
  because a liveness probe that touches a dependency restarts a process that
  was fine (see `spec.py`'s `HealthCheck` docstring — this is the ERP defect,
  inverted into a fixture). `/health/ready` answers 503 until `READY_MARKER`
  exists on disk, so the rehearsal script controls readiness deliberately
  rather than the process discovering it. `/metrics` exposes one gauge so the
  rehearsal's Prometheus step has something real to alert on.
- `--migrate` — connects to `MIGRATION_DATABASE_URL` (the owner credential;
  never `DATABASE_URL`, which is what the runtime role holds and which
  `spec.py` refuses to let hold the owner material) via `psql` and creates
  one durable table plus a `schema_version` row. Idempotent: safe to run
  twice.
- `--heads` — reads `schema_version` back and prints one revision per line,
  in the same "first whitespace token is the revision" shape
  `ComposeHostEffects._parse_heads` already tolerates from real Alembic
  output, so the rehearsal's head-comparison logic exercises the identical
  parsing path.
- `--worker` / `--worker-ping` — a long-lived custom worker plus the declared
  in-container ping that proves it is doing more than merely running.
- `--scheduler` / `--scheduler-last-tick` — a long-lived scheduler that writes
  a successful-tick timestamp plus the declared command that reads it. The
  `*-make-unhealthy`/`*-make-stale` modes are injection-only controls used to
  break those premises while leaving the role processes running.

No third-party dependency. `psql` (the `postgresql-client` package) is the
only thing this image needs beyond the stdlib, and it is a genuine runtime
tool for a role that speaks Postgres — not build tooling `image/audit.py`'s
`rule_no_build_tooling` would (correctly) refuse.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

READY_MARKER = os.environ.get("READY_MARKER", "/tmp/rehearsal-ready")  # noqa: S108
WORKER_MARKER = os.environ.get(
    "WORKER_MARKER",
    "/tmp/rehearsal-worker-healthy",  # noqa: S108
)
SCHEDULER_TICK = os.environ.get(
    "SCHEDULER_TICK",
    "/tmp/rehearsal-scheduler-tick",  # noqa: S108
)
SCHEDULER_PAUSE = os.environ.get(
    "SCHEDULER_PAUSE",
    "/tmp/rehearsal-scheduler-paused",  # noqa: S108
)
IDENTITY_MARKER = os.environ.get(
    "IDENTITY_MARKER",
    "/tmp/rehearsal-instance",  # noqa: S108
)
PORT = int(os.environ.get("PORT", "8000"))
SCHEMA_HEAD = os.environ.get("SCHEMA_HEAD", "0001")
LOCK_TIMEOUT = os.environ.get("MIGRATE_LOCK_TIMEOUT", "5s")


def instance_identity() -> str:
    try:
        with open(IDENTITY_MARKER, encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError:
        return "unknown"


class Handler(BaseHTTPRequestHandler):
    server_version = "RehearsalApp/1"

    def log_message(self, fmt: str, *args: object) -> None:
        # Compose's own `json-file` log driver already captures stdout; a
        # second copy of every request line on stderr is noise the rehearsal
        # does not need.
        return

    def _respond_json(self, status: int, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health/live":
            # DB-free, always 200. This is the whole point of the endpoint:
            # step 7 of the ordered rehearsal stops the database and expects
            # this to keep answering.
            self._respond_json(200, {"status": "live"})
            return
        if self.path == "/health/ready":
            if os.path.isfile(READY_MARKER):
                self._respond_json(200, {"status": "ready"})
            else:
                self._respond_json(503, {"status": "not-ready"})
            return
        if self.path == "/metrics":
            up = 1 if os.path.isfile(READY_MARKER) else 0
            body = (
                "# HELP rehearsal_up 1 when the readiness marker is present\n"
                "# TYPE rehearsal_up gauge\n"
                f"rehearsal_up {up}\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/identity":
            self._respond_json(200, {"identity": instance_identity()})
            return
        self._respond_json(404, {"error": "not found"})


def _database_url(*, owner: bool) -> str:
    name = "MIGRATION_DATABASE_URL" if owner else "DATABASE_URL"
    url = os.environ.get(name, "")
    if not url:
        print(f"{name} is not set", file=sys.stderr)
        raise SystemExit(2)
    return url


def cmd_migrate() -> int:
    """Create one durable table and record the schema head — idempotently.

    `lock_timeout` is set explicitly and short on purpose: the rehearsal's
    lock-contention injection case holds a conflicting lock from another
    session and needs this to fail FAST with a real
    `canceling statement due to lock timeout` — the exact marker
    `engine/run.py`'s `_is_lock_contention` already recognises — rather than
    hang for the default (unbounded) wait.
    """
    url = _database_url(owner=True)
    # LOCK_TIMEOUT and SCHEMA_HEAD are this process's own env-derived
    # constants, never request input, so the interpolation below is not the
    # injection shape S608 usually catches — there is no untrusted string on
    # this path.
    sql = (
        f"SET lock_timeout = '{LOCK_TIMEOUT}';"  # noqa: S608
        "CREATE TABLE IF NOT EXISTS rehearsal_ledger "
        "(id serial primary key, migrated_at timestamptz DEFAULT now());"
        "INSERT INTO rehearsal_ledger DEFAULT VALUES;"
        "CREATE TABLE IF NOT EXISTS schema_version (version text PRIMARY KEY);"
        f"INSERT INTO schema_version (version) VALUES ('{SCHEMA_HEAD}') "
        "ON CONFLICT DO NOTHING;"
    )
    result = subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-c", sql],
        check=False,
    )
    return result.returncode


def cmd_heads() -> int:
    url = _database_url(owner=True)
    result = subprocess.run(
        [
            "psql",
            url,
            "-v",
            "ON_ERROR_STOP=1",
            "-tAc",
            "SELECT version FROM schema_version ORDER BY version",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        return result.returncode
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped:
            print(stripped)
    return 0


def cmd_serve() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)  # noqa: S104 - the compose file binds the HOST side to loopback (see render/compose.py); binding all interfaces INSIDE the container is what lets the loopback publish reach it
    print(f"rehearsal app serving on 0.0.0.0:{PORT}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - interactive use only
        pass
    return 0


def _write_marker(path: str, value: str = "") -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(value)


def cmd_worker() -> int:
    """Stay alive while exposing a real in-container ping premise."""

    _write_marker(WORKER_MARKER)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:  # pragma: no cover - interactive use only
        return 0


def cmd_worker_ping() -> int:
    return 0 if os.path.isfile(WORKER_MARKER) else 1


def cmd_worker_make_unhealthy() -> int:
    try:
        os.unlink(WORKER_MARKER)
    except FileNotFoundError:
        pass
    return 0


def cmd_scheduler() -> int:
    """Record successful ticks while remaining a distinct long-lived role."""

    try:
        while True:
            if not os.path.isfile(SCHEDULER_PAUSE):
                _write_marker(SCHEDULER_TICK, str(int(time.time())))
            time.sleep(1)
    except KeyboardInterrupt:  # pragma: no cover - interactive use only
        return 0


def cmd_scheduler_last_tick() -> int:
    try:
        with open(SCHEDULER_TICK, encoding="utf-8") as handle:
            print(handle.read().strip())
    except FileNotFoundError:
        return 1
    return 0


def cmd_scheduler_make_stale() -> int:
    # Pause first so the long-lived scheduler cannot race this injected old
    # timestamp and make the case accidentally healthy again.
    _write_marker(SCHEDULER_PAUSE)
    _write_marker(SCHEDULER_TICK, str(int(time.time()) - 3600))
    return 0


def main(argv: list[str]) -> int:
    if "--migrate" in argv:
        return cmd_migrate()
    if "--heads" in argv:
        return cmd_heads()
    if "--worker-ping" in argv:
        return cmd_worker_ping()
    if "--worker-make-unhealthy" in argv:
        return cmd_worker_make_unhealthy()
    if "--worker" in argv:
        return cmd_worker()
    if "--scheduler-last-tick" in argv:
        return cmd_scheduler_last_tick()
    if "--scheduler-make-stale" in argv:
        return cmd_scheduler_make_stale()
    if "--scheduler" in argv:
        return cmd_scheduler()
    return cmd_serve()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
