#!/usr/bin/env python
"""Runnable entrypoint for the outbox relay worker (WS3 slice 2).

Builds the TWO separate connections the worker requires and runs the polling loop
with clean SIGTERM/SIGINT shutdown:

- **dispatcher** connection — `RELAY_DISPATCHER_DATABASE_URL`, authenticated as the
  least-privilege `outbox_dispatcher` role (claim/settle only);
- **tenant-scoped** connection — `DATABASE_URL`, the RLS-enforced `app_user` role,
  whose context is restored per event for any delivery-time product read.

Engine construction lives HERE (an entrypoint script), not in the kernel package,
so the kernel's one-transaction-authority rule is preserved. The transport is
`LoggingTransport` by default; a real deployment wires its own.

Usage:
  RELAY_DISPATCHER_DATABASE_URL=postgresql+psycopg://outbox_dispatcher@host/db \
  DATABASE_URL=postgresql+psycopg://app_user:...@host/db \
  poetry run python scripts/run_relay.py
"""

from __future__ import annotations

import os
import signal
import socket
import sys
import threading

from dotmac_kernel.messaging.worker import LoggingTransport, run_forever
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def main() -> int:
    dispatcher_url = os.getenv("RELAY_DISPATCHER_DATABASE_URL")
    tenant_url = os.getenv("DATABASE_URL")
    if not dispatcher_url or not tenant_url:
        print(
            "set RELAY_DISPATCHER_DATABASE_URL (outbox_dispatcher) and DATABASE_URL "
            "(app_user)",
            file=sys.stderr,
        )
        return 2

    dispatcher_engine = create_engine(dispatcher_url, pool_pre_ping=True)
    tenant_engine = create_engine(tenant_url, pool_pre_ping=True)
    dispatcher_sessions = sessionmaker(bind=dispatcher_engine)
    tenant_sessions = sessionmaker(bind=tenant_engine)

    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    try:
        run_forever(
            dispatcher_session_factory=dispatcher_sessions,
            tenant_session_factory=tenant_sessions,
            transport=LoggingTransport(),
            worker_id=worker_id,
            stop=stop,
            poll_interval=float(os.getenv("RELAY_POLL_INTERVAL", "1.0")),
        )
    finally:
        dispatcher_engine.dispose()
        tenant_engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
