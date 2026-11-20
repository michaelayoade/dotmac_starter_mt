"""Audited requeue of a dead-lettered dispatch (ADR-0006 § 5g, K-5).

**Owner of:** *an operator, naming themselves and a reason, puts one dead
dispatch back in the queue.*

A dispatch goes `dead` when the relay gave up on it (attempts exhausted, or the
deferral bound of K-4 exceeded). Nothing re-enters the queue by itself; this is
the one door back in, and it is deliberately narrow:

- **Typed command, one dispatch.** `requeue_dead_dispatch` takes a tenant, a
  dispatch id, an actor and a reason. There is no "requeue everything" form.
- **Tenant-scoped.** The row is read and written only under the caller's tenant
  RLS context, by `(tenant_id, dispatch_id)`; another tenant's id is "not found".
- **Audited.** One `communication.dispatch.requeued` audit row in the same
  transaction names the operator (`actor_type="user"`, a literal, as the audit
  actor ratchet requires; `actor_id` is mandatory), the reason, the dispatch
  and the new outbox event. The audit details never carry the address,
  subject or body.
- **Idempotent.** The dead -> pending transition is taken under a row lock, so
  two concurrent or repeated calls produce exactly one requeue, one audit row
  and one outbox event; the others return `requeued=False`.
- **Does not bypass consent or K-2.** It refuses (does not clear) a dispatch
  whose address is suppressed, and a dispatch that already has a final
  (non-`failed`) receipt. `delivery_providers.send` re-checks both at delivery,
  so the delivery path remains the authority; this is a clearer early refusal.
- **Never resurrects redacted content.** A redacted dispatch is refused (the
  `communication_dispatches` guard trigger refuses it as well).

The dead outbox event is left as `dead` evidence; a NEW `delivery.dispatch.v1`
event with the id-only payload is enqueued, so the dispatcher role needs no new
privilege and attempt/deferral counters start fresh.

Flush-only: the caller owns the transaction (`dotmac_kernel.db`).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_kernel import consent
from dotmac_kernel.audit import (
    AuditEvent,
    resolve_audit_actor,
    resolve_event_attribution,
    write_audit_event,
)
from dotmac_kernel.audit_actions import active_audit_actions
from dotmac_kernel.delivery import DeliveryError
from dotmac_kernel.delivery_dispatch import (
    DISPATCH_EVENT_TYPE,
    DispatchNotFound,
)
from dotmac_kernel.delivery_models import (
    DELIVERY_FAILED,
    DISPATCH_DEAD,
    DISPATCH_PENDING,
    CommunicationDelivery,
    CommunicationDispatch,
)
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.messaging.outbox import enqueue_event

AUDIT_ACTION_REQUEUED = "communication.dispatch.requeued"
AUDIT_ENTITY_TYPE = "communication_dispatch"
#: Written by the K-8 relay transport (`delivery_relay`); a literal here so this
#: module does not import the transport.
AUDIT_ACTION_UNKNOWN = "communication.dispatch.outcome_unknown"
MAX_REASON_LENGTH = 500


class RequeueRefused(DeliveryError):
    """The dispatch may not be requeued. Nothing was changed."""


@dataclass(frozen=True, slots=True)
class RequeueResult:
    dispatch_id: UUID
    #: True when THIS call moved the dispatch dead -> pending. False for an
    #: idempotent repeat (already pending): no audit row, no new event.
    requeued: bool
    status: str
    outbox_event_id: UUID | None


def _unknown_outcome_pending(db: Session, tenant_id: UUID, dispatch_id: UUID) -> bool:
    """True when the newest of this dispatch's unknown-outcome and requeue audit
    rows is the unknown outcome (a later requeue clears an earlier one)."""
    newest = db.execute(
        select(AuditEvent.action)
        .where(
            AuditEvent.tenant_id == tenant_id,
            AuditEvent.entity_type == AUDIT_ENTITY_TYPE,
            AuditEvent.entity_id == str(dispatch_id),
            AuditEvent.action.in_((AUDIT_ACTION_UNKNOWN, AUDIT_ACTION_REQUEUED)),
        )
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if newest == AUDIT_ACTION_UNKNOWN:
        return True
    # Fail closed when the audit row was lost (the unknown path's last tiers
    # settle dead without one): the transport's own dead-letter marker on the
    # newest outbox event says the same thing.
    last_error = db.execute(
        select(OutboxEvent.last_error)
        .where(
            OutboxEvent.status == "dead",
            OutboxEvent.tenant_id == tenant_id,
            OutboxEvent.event_type == DISPATCH_EVENT_TYPE,
            OutboxEvent.payload["dispatch_id"].as_string() == str(dispatch_id),
        )
        .order_by(OutboxEvent.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    # NULL means "not the terminal marker"; the audit-row signal still applies.
    return last_error is not None and last_error.startswith("terminal:")


def requeue_dead_dispatch(
    db: Session,
    tenant_id: UUID,
    *,
    dispatch_id: UUID,
    actor_id: str,
    reason: str,
    source_application: str | None = None,
    acknowledge_possible_duplicate: bool = False,
) -> RequeueResult:
    """Requeue one dead dispatch. See the module docstring for the contract.

    Raises `DispatchNotFound` (unknown, or another tenant's), `RequeueRefused`
    (not dead, redacted, suppressed, or already finally receipted), and the audit
    errors for a missing/invalid actor or an undeclared action — all BEFORE any
    change is made.
    """
    # Validate the actor and the declared action up front, so a bad call changes
    # nothing (the audit writer would raise the same errors, but only after the
    # state change; the audit table is append-only so it cannot be amended).
    resolve_audit_actor(actor_type="user", actor_id=actor_id, actor_party_id=None)
    resolve_event_attribution(source_application)
    active_audit_actions().require(AUDIT_ACTION_REQUEUED)
    cleaned = (reason or "").strip()
    if not cleaned:
        raise RequeueRefused("a requeue needs a reason")
    if len(cleaned) > MAX_REASON_LENGTH:
        raise RequeueRefused(f"reason exceeds {MAX_REASON_LENGTH} characters")

    # The row lock serialises concurrent requeues of the same dispatch.
    row = db.execute(
        select(CommunicationDispatch)
        .where(
            CommunicationDispatch.tenant_id == tenant_id,
            CommunicationDispatch.dispatch_id == dispatch_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise DispatchNotFound(f"no dispatch {dispatch_id}")
    if row.status == DISPATCH_PENDING:
        return RequeueResult(dispatch_id, False, DISPATCH_PENDING, None)
    if row.status != DISPATCH_DEAD:
        raise RequeueRefused(
            f"dispatch {dispatch_id} is {row.status!r}; only a dead dispatch "
            "can be requeued"
        )
    if row.redacted_at is not None:
        raise RequeueRefused(f"dispatch {dispatch_id} bodies were redacted")

    # K-2: a final (non-failed) receipt means the provider already took it.
    final = db.execute(
        select(func.count())
        .select_from(CommunicationDelivery)
        .where(
            CommunicationDelivery.tenant_id == tenant_id,
            CommunicationDelivery.dispatch_id == dispatch_id,
            CommunicationDelivery.status != DELIVERY_FAILED,
        )
    ).scalar_one()
    if final:
        raise RequeueRefused(
            f"dispatch {dispatch_id} already has a final receipt; it would only "
            "replay"
        )

    # An unknown provider outcome (K-8): the message may already have been
    # delivered, so a requeue can double-send. One operator must say so, by name.
    acknowledged = acknowledge_possible_duplicate is True
    if not acknowledged and _unknown_outcome_pending(db, tenant_id, dispatch_id):
        raise RequeueRefused(
            f"dispatch {dispatch_id} ended with an UNKNOWN provider outcome: the "
            "message may already have been delivered. Check with the provider, "
            "then requeue with acknowledge_possible_duplicate=True "
            "(--accept-possible-duplicate) to accept a possible duplicate send"
        )

    blocked = consent.suppression_reason(
        db,
        tenant_id,
        channel=row.channel,
        address=row.address,
        category=row.category,
    )
    if blocked is not None:
        raise RequeueRefused(
            f"dispatch {dispatch_id} is suppressed ({blocked}); requeue does not "
            "clear consent"
        )

    row.status = DISPATCH_PENDING
    row.terminal_at = None
    db.flush()
    event = enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=DISPATCH_EVENT_TYPE,
        payload={"dispatch_id": str(dispatch_id)},
    )
    write_audit_event(
        db,
        tenant_id=tenant_id,
        actor_type="user",
        actor_id=actor_id,
        source_application=source_application,
        action=AUDIT_ACTION_REQUEUED,
        entity_type=AUDIT_ENTITY_TYPE,
        entity_id=str(dispatch_id),
        is_success=True,
        details={
            "reason": cleaned,
            "from_status": DISPATCH_DEAD,
            "outbox_event_id": str(event.id),
            "acknowledged_possible_duplicate": acknowledge_possible_duplicate is True,
        },
    )
    return RequeueResult(dispatch_id, True, DISPATCH_PENDING, event.id)


__all__ = [
    "AUDIT_ACTION_REQUEUED",
    "MAX_REASON_LENGTH",
    "RequeueRefused",
    "RequeueResult",
    "requeue_dead_dispatch",
]
