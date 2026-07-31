"""Outbox relay worker (WS3 slice 2, PR 3) — the separate polling process.

Drains the outbox by repeatedly claiming a batch and delivering each event, with
**strict separation of connections** (Michael's caution): the *dispatcher*
connection only ever calls the claim/settle functions; any delivery-time product
read uses a **separately created tenant-scoped connection** whose RLS context is
restored to the event's own tenant. The dispatcher credential never performs a
tenant business read.

Delivery goes through a `DeliveryTransport` (a Protocol — the concrete transport
is product-supplied); `LoggingTransport` is a trivial reference. Guarantees
**at-least-once** with one active claim per lease.

Transaction-authority contract: this module NEVER constructs an engine or a
sessionmaker — it RECEIVES session factories (a dispatcher factory + a
tenant-scoped factory) and a transport. The runnable entrypoint
(`scripts/run_relay.py`) builds the engines and installs signal handlers.

Submodule-only: `from dotmac_kernel.messaging.worker import ...`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from dotmac_kernel.messaging.relay import (
    ClaimedEvent,
    RelayPolicy,
    claim_batch,
    record_failure,
    record_success,
)

logger = logging.getLogger("dotmac_kernel.messaging.worker")

SessionFactory = Callable[[], Session]


class DeliveryTransport(Protocol):
    """Delivers one outbox event to its destination. Raises on failure (the
    worker then backs off / dead-letters). `tenant_db` is a tenant-scoped session
    already RLS-restored to `event.tenant_id` — use it for any product read the
    delivery needs; most transports deliver the payload and ignore it."""

    def deliver(self, event: ClaimedEvent, tenant_db: Session) -> None: ...


class LoggingTransport:
    """A reference transport: logs the event and succeeds. For the lab/tests; real
    deployments supply their own (HTTP webhook, bus, …)."""

    def deliver(self, event: ClaimedEvent, tenant_db: Session) -> None:
        logger.info("relay delivered %s (%s)", event.id, event.event_type)


def _deliver_one(
    *,
    dispatcher_db: Session,
    tenant_session_factory: SessionFactory,
    transport: DeliveryTransport,
    event: ClaimedEvent,
    worker_id: str,
    policy: RelayPolicy,
) -> None:
    # A FRESH tenant-scoped connection, RLS context restored to THIS event's
    # tenant — never the dispatcher connection.
    tenant_db = tenant_session_factory()
    delivered = False
    try:
        tenant_db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(event.tenant_id)},
        )
        transport.deliver(event, tenant_db)
        tenant_db.commit()
        delivered = True
    except Exception as exc:  # any delivery failure retries / dead-letters
        tenant_db.rollback()
        logger.warning("relay delivery failed for %s: %r", event.id, exc)
        record_failure(
            dispatcher_db,
            event_id=event.id,
            worker_id=worker_id,
            attempts=event.attempts,
            error=repr(exc),
            policy=policy,
        )
        dispatcher_db.commit()
    finally:
        tenant_db.close()

    if delivered:
        record_success(dispatcher_db, event_id=event.id, worker_id=worker_id)
        dispatcher_db.commit()


def run_once(
    *,
    dispatcher_db: Session,
    tenant_session_factory: SessionFactory,
    transport: DeliveryTransport,
    worker_id: str,
    policy: RelayPolicy = RelayPolicy(),
) -> int:
    """Claim one batch and deliver each event. Returns the number claimed."""
    claimed = claim_batch(dispatcher_db, worker_id=worker_id, policy=policy)
    dispatcher_db.commit()  # make the lease durable before delivering
    for event in claimed:
        _deliver_one(
            dispatcher_db=dispatcher_db,
            tenant_session_factory=tenant_session_factory,
            transport=transport,
            event=event,
            worker_id=worker_id,
            policy=policy,
        )
    return len(claimed)


def run_forever(
    *,
    dispatcher_session_factory: SessionFactory,
    tenant_session_factory: SessionFactory,
    transport: DeliveryTransport,
    worker_id: str,
    stop: threading.Event,
    policy: RelayPolicy = RelayPolicy(),
    poll_interval: float = 1.0,
) -> None:
    """Poll until `stop` is set (clean shutdown). Each iteration uses a fresh
    dispatcher session; an idle poll (nothing claimed) sleeps interruptibly."""
    logger.info("relay worker %s starting", worker_id)
    while not stop.is_set():
        dispatcher_db = dispatcher_session_factory()
        try:
            claimed = run_once(
                dispatcher_db=dispatcher_db,
                tenant_session_factory=tenant_session_factory,
                transport=transport,
                worker_id=worker_id,
                policy=policy,
            )
        finally:
            dispatcher_db.close()
        if claimed == 0:
            stop.wait(poll_interval)  # interruptible idle sleep
    logger.info("relay worker %s stopped", worker_id)


__all__ = [
    "DeliveryTransport",
    "LoggingTransport",
    "SessionFactory",
    "run_once",
    "run_forever",
]
