#!/usr/bin/env python
"""Runnable entrypoint for the PLATFORM outbox relay worker (WS3 platform relay).

The platform peer of `run_relay.py`. Builds the TWO separate connections the
platform worker requires and runs the polling loop with clean SIGTERM/SIGINT
shutdown:

- **dispatcher** connection — `PLATFORM_RELAY_DISPATCHER_DATABASE_URL`, authenticated
  as the least-privilege `platform_outbox_dispatcher` role (claim/settle only);
- **platform** connection — `PLATFORM_DATABASE_URL`, the `platform_api` role, on
  which consumers run `process_once_platform`. There is NO tenant context to
  restore (platform events are tenant-free).

Engine construction lives HERE (an entrypoint script), not in the kernel package,
so the kernel's one-transaction-authority rule is preserved. The transport is
`LoggingPlatformTransport` by default; a real deployment wires its own consumer.

Usage (env vars):
  PLATFORM_RELAY_DISPATCHER_DATABASE_URL — DSN as `platform_outbox_dispatcher`
  PLATFORM_DATABASE_URL                  — DSN as `platform_api`
  then: poetry run python scripts/run_platform_relay.py
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading

from dotmac_kernel.messaging.platform_worker import (
    LoggingPlatformTransport,
    run_forever,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def main() -> int:
    dispatcher_url = os.getenv("PLATFORM_RELAY_DISPATCHER_DATABASE_URL")
    platform_url = os.getenv("PLATFORM_DATABASE_URL")
    if not dispatcher_url or not platform_url:
        print(
            "set PLATFORM_RELAY_DISPATCHER_DATABASE_URL (platform_outbox_dispatcher) "
            "and PLATFORM_DATABASE_URL (platform_api)",
            file=sys.stderr,
        )
        return 2

    dispatcher_engine = create_engine(dispatcher_url, pool_pre_ping=True)
    platform_engine = create_engine(platform_url, pool_pre_ping=True)
    dispatcher_sessions = sessionmaker(bind=dispatcher_engine)
    platform_sessions = sessionmaker(bind=platform_engine)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    try:
        run_forever(
            dispatcher_session_factory=dispatcher_sessions,
            platform_session_factory=platform_sessions,
            transport=LoggingPlatformTransport(),
            worker_id=worker_id,
            stop=stop,
            poll_interval=float(os.getenv("PLATFORM_RELAY_POLL_INTERVAL", "1.0")),
        )
    finally:
        dispatcher_engine.dispose()
        platform_engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
