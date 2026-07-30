"""Transactional outbox write side (kernel WS3).

`enqueue_event` inserts an `OutboxEvent` into the CALLER's session/transaction,
so the event is persisted if and only if that transaction commits — the atomic
guarantee that a state change and its "please deliver this" record can never
diverge. Delivery is a separate concern: a relay (WS3 slice 2) drains `pending`
rows out-of-band, which is the named reconciler for the side effect.

Follows the kernel transaction-authority rule: RECEIVES a `Session`, never builds
one; does `add`/`flush`, never `commit`.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from dotmac_kernel.messaging.models import OutboxEvent, OutboxStatus


def enqueue_event(
    db: Session,
    *,
    tenant_id: UUID,
    event_type: str,
    payload: Mapping[str, object] | None = None,
    correlation_id: str | None = None,
) -> OutboxEvent:
    """Enqueue a `pending` outbox event in the caller's transaction. The event
    commits atomically with whatever state change the caller is making, and is
    delivered later by the relay. Returns the flushed `OutboxEvent`."""
    event = OutboxEvent(
        tenant_id=tenant_id,
        event_type=event_type,
        payload=dict(payload or {}),
        status=OutboxStatus.PENDING.value,
        attempts=0,
        correlation_id=correlation_id,
    )
    db.add(event)
    db.flush()
    return event


__all__ = ["enqueue_event"]
