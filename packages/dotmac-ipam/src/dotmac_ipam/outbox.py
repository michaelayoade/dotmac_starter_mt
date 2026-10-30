"""IPAM events onto the Kernel's transactional outbox.

Every vocabulary event the module writes to ``ipam_events`` (append-only
evidence) is also enqueued on ``public.outbox_events`` in the SAME transaction,
so a state change and its delivery record can never diverge. Mirrors
``dotmac_approvals.outbox``: flush-only (``add``/``flush``, never commit), and the
Kernel import is function-local because ``dotmac_kernel.messaging`` reaches
``dotmac_kernel.db``, which builds an engine from settings at import time.

The module declares the routing codes in its manifest (``outbox_event_types``)
and requires ``outbox_relay.v1``; ``ip_0003_outbox_relay`` proves the relay at
deploy time. Delivery, retry and any consequence stay with the relay and the
consuming product.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session


def publish(
    db: Session,
    *,
    tenant_id: UUID,
    routing_code: str,
    event_type: str,
    aggregate_ref: str,
    payload: dict[str, Any],
    occurred_at: datetime,
    correlation_id: str | None = None,
) -> None:
    from dotmac_kernel.messaging.outbox import enqueue_event

    enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=routing_code,
        payload={
            "event": event_type,
            "aggregate_ref": aggregate_ref,
            "occurred_at": occurred_at.isoformat(),
            "data": payload,
        },
        correlation_id=correlation_id,
    )


__all__ = ["publish"]
