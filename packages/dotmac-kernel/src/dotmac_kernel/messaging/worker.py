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
from collections.abc import Callable, Sequence
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.orm import Session

from dotmac_kernel.delivery import DeliveryDeferred, DeliveryTerminal
from dotmac_kernel.messaging.relay import (
    ClaimedEvent,
    RelayPolicy,
    claim_batch,
    claim_batch_by_type,
    deferral_dead_letters,
    failure_dead_letters,
    record_dead_letter,
    record_deferral,
    record_failure,
    record_success,
)

logger = logging.getLogger("dotmac_kernel.messaging.worker")

SessionFactory = Callable[[], Session]


class DeliveryTransport(Protocol):
    """Delivers one outbox event to its destination. Raises on failure (the
    worker then backs off / dead-letters); raises `DeliveryDeferred` for a hold,
    which reschedules without charging an attempt; raises `DeliveryTerminal` to
    dead-letter at once with no retry (K-8). `tenant_db` is a tenant-scoped
    session already RLS-restored to `event.tenant_id` — use it for any product read the
    delivery needs; most transports deliver the payload and ignore it."""

    def deliver(self, event: ClaimedEvent, tenant_db: Session) -> None: ...


class DeadLetterAware(Protocol):
    """Optional second half of a transport (K-8): told, in a tenant-scoped
    session, that the event is about to be dead-lettered, so the product state
    the event stands for can be put in its terminal state too (a dispatch goes
    `dead`). Must be idempotent. A transport without it simply gets no call.

    The worker runs it and commits BEFORE settling the outbox row dead. If it
    fails, the outbox row is NOT settled (the lease is left to expire and the
    event is reclaimed), so the two can never disagree with the outbox row dead
    and the product state still live."""

    def dead_letter(self, event: ClaimedEvent, tenant_db: Session) -> None: ...


class LoggingTransport:
    """A reference transport: logs the event and succeeds. For the lab/tests; real
    deployments supply their own (HTTP webhook, bus, …)."""

    def deliver(self, event: ClaimedEvent, tenant_db: Session) -> None:
        logger.info("relay delivered %s (%s)", event.id, event.event_type)


def _mark_dead(
    *,
    tenant_session_factory: SessionFactory,
    transport: DeliveryTransport,
    event: ClaimedEvent,
) -> bool:
    """Run the transport's optional `dead_letter` in a fresh tenant-scoped
    session and commit. True when it is safe to settle the outbox row dead."""
    hook = getattr(transport, "dead_letter", None)
    if hook is None:
        return True
    tenant_db = tenant_session_factory()
    try:
        tenant_db.execute(
            text("SELECT set_config('app.current_tenant', :t, true)"),
            {"t": str(event.tenant_id)},
        )
        hook(event, tenant_db)
        tenant_db.commit()
        return True
    except Exception as exc:  # leave the lease; the event is reclaimed
        tenant_db.rollback()
        logger.error(
            "relay could not mark %s dead (%s); leaving the lease to expire",
            event.id,
            type(exc).__name__,
        )
        return False
    finally:
        tenant_db.close()


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
    except DeliveryDeferred as hold:
        # A hold (e.g. a mail gate), not a failure: reschedule without charging
        # a relay attempt, bounded by `policy.max_deferrals` (K-4).
        tenant_db.rollback()
        logger.info("relay delivery deferred for %s", event.id)
        if deferral_dead_letters(event.deferrals, policy) and not _mark_dead(
            tenant_session_factory=tenant_session_factory,
            transport=transport,
            event=event,
        ):
            return
        record_deferral(
            dispatcher_db,
            event_id=event.id,
            worker_id=worker_id,
            attempts=event.attempts,
            deferrals=event.deferrals,
            reason=hold.reason,
            retry_after=hold.retry_after,
            policy=policy,
        )
        dispatcher_db.commit()
    except DeliveryTerminal as verdict:
        # The transport says this must NOT be retried (K-8: e.g. an unknown
        # provider outcome that a retry could double-send). Dead-letter now.
        tenant_db.rollback()
        logger.warning("relay delivery terminal for %s", event.id)
        if not _mark_dead(
            tenant_session_factory=tenant_session_factory,
            transport=transport,
            event=event,
        ):
            return
        record_dead_letter(
            dispatcher_db,
            event_id=event.id,
            worker_id=worker_id,
            attempts=event.attempts,
            error=f"terminal: {verdict.reason}",
        )
        dispatcher_db.commit()
    except Exception as exc:  # any delivery failure retries / dead-letters
        tenant_db.rollback()
        logger.warning("relay delivery failed for %s: %r", event.id, exc)
        if failure_dead_letters(event.attempts, policy) and not _mark_dead(
            tenant_session_factory=tenant_session_factory,
            transport=transport,
            event=event,
        ):
            return
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
    event_types: Sequence[str] | None = None,
) -> int:
    """Claim one batch and deliver each event. Returns the number claimed.

    `event_types=None` is the historical claim of every type. A non-empty
    sequence claims only those types (`claim_batch_by_type`, prerequisite
    `outbox_typed_claim.v1`); an empty one is refused, never read as no filter."""
    if event_types is None:
        claimed = claim_batch(dispatcher_db, worker_id=worker_id, policy=policy)
    else:
        claimed = claim_batch_by_type(
            dispatcher_db,
            worker_id=worker_id,
            event_types=event_types,
            policy=policy,
        )
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
    event_types: Sequence[str] | None = None,
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
                event_types=event_types,
            )
        finally:
            dispatcher_db.close()
        if claimed == 0:
            stop.wait(poll_interval)  # interruptible idle sleep
    logger.info("relay worker %s stopped", worker_id)


__all__ = [
    "DeadLetterAware",
    "DeliveryTransport",
    "LoggingTransport",
    "SessionFactory",
    "run_forever",
    "run_once",
]
