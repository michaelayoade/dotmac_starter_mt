"""Platform outbox relay worker (WS3 platform relay) — the separate polling process.

The platform peer of `messaging.worker`. It drains `platform_outbox_events` with the
same **strict separation of connections**, adapted to the platform plane:

- the **dispatcher** connection (role `platform_outbox_dispatcher`) only ever calls
  the claim/settle functions — it has EXECUTE on those two functions and NOTHING
  else, so it can never read a business table;
- **delivery** uses a SEPARATELY created **`platform_api`** session — the same
  identity a `process_once_platform` consumer runs under. There is NO tenant
  context to restore (platform events are tenant-free), so unlike the tenant
  worker this never sets `app.current_tenant`.

Delivery goes through a `PlatformDeliveryTransport` (a Protocol — the concrete
transport is product-supplied); `LoggingPlatformTransport` is a trivial reference.
Guarantees **at-least-once** with one active claim per lease; consumers dedupe via
`process_once_platform` keyed on the event id.

Transaction-authority contract: this module NEVER constructs an engine or a
sessionmaker — it RECEIVES session factories (a dispatcher factory + a platform_api
factory) and a transport. The runnable entrypoint (`scripts/run_platform_relay.py`)
builds the engines and installs signal handlers.

Submodule-only: `from dotmac_kernel.messaging.platform_worker import ...`.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Protocol

from sqlalchemy.orm import Session

from dotmac_kernel.messaging.platform_relay import (
    ClaimedPlatformEvent,
    claim_platform_batch,
    record_failure,
    record_success,
)
from dotmac_kernel.messaging.relay import RelayPolicy

logger = logging.getLogger("dotmac_kernel.messaging.platform_worker")

SessionFactory = Callable[[], Session]


class PlatformDeliveryTransport(Protocol):
    """Delivers one platform outbox event to its destination. Raises on failure
    (the worker then backs off / dead-letters). `platform_db` is a `platform_api`
    session — a consumer typically runs `process_once_platform` on it to apply the
    event idempotently."""

    def deliver(self, event: ClaimedPlatformEvent, platform_db: Session) -> None: ...


class LoggingPlatformTransport:
    """A reference transport: logs the event and succeeds. For the lab/tests; real
    deployments supply their own (in-process consumer, HTTP webhook, bus, …)."""

    def deliver(self, event: ClaimedPlatformEvent, platform_db: Session) -> None:
        logger.info("platform relay delivered %s (%s)", event.id, event.event_type)


def _deliver_one(
    *,
    dispatcher_db: Session,
    platform_session_factory: SessionFactory,
    transport: PlatformDeliveryTransport,
    event: ClaimedPlatformEvent,
    worker_id: str,
    policy: RelayPolicy,
) -> None:
    # A FRESH platform_api connection for the consumer — never the dispatcher
    # connection (which has no table privilege at all).
    platform_db = platform_session_factory()
    delivered = False
    try:
        transport.deliver(event, platform_db)
        platform_db.commit()
        delivered = True
    except Exception as exc:  # any delivery failure retries / dead-letters
        platform_db.rollback()
        logger.warning("platform relay delivery failed for %s: %r", event.id, exc)
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
        platform_db.close()

    if delivered:
        record_success(dispatcher_db, event_id=event.id, worker_id=worker_id)
        dispatcher_db.commit()


def run_once(
    *,
    dispatcher_db: Session,
    platform_session_factory: SessionFactory,
    transport: PlatformDeliveryTransport,
    worker_id: str,
    policy: RelayPolicy = RelayPolicy(),
) -> int:
    """Claim one batch and deliver each event. Returns the number claimed."""
    claimed = claim_platform_batch(dispatcher_db, worker_id=worker_id, policy=policy)
    dispatcher_db.commit()  # make the lease durable before delivering
    for event in claimed:
        _deliver_one(
            dispatcher_db=dispatcher_db,
            platform_session_factory=platform_session_factory,
            transport=transport,
            event=event,
            worker_id=worker_id,
            policy=policy,
        )
    return len(claimed)


def run_forever(
    *,
    dispatcher_session_factory: SessionFactory,
    platform_session_factory: SessionFactory,
    transport: PlatformDeliveryTransport,
    worker_id: str,
    stop: threading.Event,
    policy: RelayPolicy = RelayPolicy(),
    poll_interval: float = 1.0,
) -> None:
    """Poll until `stop` is set (clean shutdown). Each iteration uses a fresh
    dispatcher session; an idle poll (nothing claimed) sleeps interruptibly."""
    logger.info("platform relay worker %s starting", worker_id)
    while not stop.is_set():
        dispatcher_db = dispatcher_session_factory()
        try:
            claimed = run_once(
                dispatcher_db=dispatcher_db,
                platform_session_factory=platform_session_factory,
                transport=transport,
                worker_id=worker_id,
                policy=policy,
            )
        finally:
            dispatcher_db.close()
        if claimed == 0:
            stop.wait(poll_interval)  # interruptible idle sleep
    logger.info("platform relay worker %s stopped", worker_id)


__all__ = [
    "PlatformDeliveryTransport",
    "LoggingPlatformTransport",
    "SessionFactory",
    "run_once",
    "run_forever",
]
