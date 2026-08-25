"""Six distinct commands that were one ambiguous "escalate" button.

Sub's UI offers "Escalate to teammate" and the function behind it picks between
assigning, auto-assigning and queueing. Three different things wearing one name
means the trail cannot answer what actually happened, and neither can the agent
who pressed it. This module separates them, and the separation is structural
rather than conventional:

===============  =========================================================
Command          What it does
===============  =========================================================
`claim`          unassigned queued work becomes owned by the claiming agent
`transfer`       ownership moves immediately to another eligible agent
`request`        warm: the target accepts or declines; the original agent
                 stays responsible until they do
`requeue`        the holder releases the work back to a team queue
`escalate`       urgency rises and a lead is alerted — ownership is
                 untouched, and this module cannot express otherwise
===============  =========================================================

A ticket or work-order handoff is deliberately absent. ADR-0052 § 4 leaves
domain work with its own owner: creating a ticket is a product consequence of a
conversation, not a conversation lifecycle transition, and the two must be able
to end at different times.

Callers own authorization and transactions. Every command that moves ownership
requires an actor and a reason, and records both sides of the move.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from dotmac_kernel.messaging import enqueue_event
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_inbox_operations.contracts import (
    AcceptTransfer,
    AdmitToQueue,
    AssignmentStatus,
    CancelTransfer,
    ClaimConversation,
    Conflict,
    ConversationTransferred,
    DeclineTransfer,
    EscalateConversation,
    EscalationRequested,
    ExpireTransferRequests,
    QueueEntryStatus,
    RequestTransfer,
    RequeueConversation,
    TransferConversation,
    TransferKind,
    TransferStatus,
)
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxEscalationRequest,
    InboxQueueEntry,
    InboxTransferRequest,
    InboxWorkflowEvent,
)
from dotmac_inbox_operations.presence import available_agents, refuse_undispatchable
from dotmac_inbox_operations.service import (
    active_assignment,
    admit_to_queue,
    insert_assignment,
    require_queue,
)
from dotmac_inbox_operations.validation import (
    aware,
    eligible_references,
    required_text,
    tenant_of,
    utc_instant,
)

# Declared on this module's manifest; a producer may enqueue an event type only
# after the installed manifest set declares it, so these three names and the
# manifest tuple move together.
TRANSFER_REQUESTED_EVENT = "inbox_operations.transfer_requested.v1"
CONVERSATION_TRANSFERRED_EVENT = "inbox_operations.conversation_transferred.v1"
ESCALATION_REQUESTED_EVENT = "inbox_operations.escalation_requested.v1"


def _notify(
    db: Session,
    *,
    tenant_id: UUID,
    event_type: str,
    notify_reference: str | None,
    payload: dict[str, str | None],
) -> None:
    """Enqueue the alert this command PROMISES, in the same transaction.

    Storing a `notify_reference` and stopping there records who a future
    adapter ought to tell; it does not tell them. A warm transfer nobody is
    notified of is a request the target never sees, and an escalation nobody is
    alerted to is a row. The outbox event commits atomically with the state
    change, and Messaging/Integrator owns delivery and its outcomes.

    No target, no event: a caller that named nobody asked for no alert.
    """
    if notify_reference is None or not notify_reference.strip():
        return
    enqueue_event(
        db,
        tenant_id=tenant_id,
        event_type=event_type,
        payload={**payload, "notify_reference": notify_reference},
    )


def _held(
    db: Session, tenant_id: UUID, conversation_reference: str
) -> ConversationAssignment:
    row = active_assignment(db, tenant_id, conversation_reference, lock=True)
    if row is None:
        raise Conflict("conversation is not assigned, so nobody can move it")
    return row


def _open_request(
    db: Session, tenant_id: UUID, conversation_reference: str
) -> InboxTransferRequest | None:
    return db.scalar(
        select(InboxTransferRequest)
        .where(
            InboxTransferRequest.tenant_id == tenant_id,
            InboxTransferRequest.conversation_reference == conversation_reference,
            InboxTransferRequest.status == TransferStatus.REQUESTED,
        )
        .with_for_update()
    )


def _settle_open_request(
    request: InboxTransferRequest | None,
    *,
    settled_at: datetime,
    actor_reference: str,
    note: str,
) -> None:
    """A pending warm request cannot survive the conversation moving another way.

    Leaving it REQUESTED would let its target accept work whose owner already
    changed, which is the one race a warm transfer exists to prevent.
    """
    if request is None:
        return
    request.status = TransferStatus.CANCELLED
    request.settled_at = settled_at
    request.settled_by_reference = actor_reference
    request.settlement_reason = note


def _check_target_admissible(
    db: Session,
    *,
    tenant_id: UUID,
    target: str,
    holder: str,
    cross_queue: bool,
    eligible_agent_references: tuple[str, ...],
    presence_fresh_after: datetime,
    supervisor_override: bool,
) -> None:
    """Every exception to the routine path needs an override, not a shrug.

    Without `supervisor_override` a target must be in the queue's eligible
    cohort, must be dispatchable right now, and the conversation must stay in
    its own queue. An offline target, a full target and a cross-team target are
    each a supervisor's decision to make on the record.
    """
    if target == holder:
        raise Conflict("a transfer must name an agent other than the current holder")
    if supervisor_override:
        return
    if cross_queue:
        raise Conflict("a cross-team transfer requires a supervisor override")
    eligible = eligible_references(eligible_agent_references)
    if target not in eligible:
        raise Conflict("transfer target is not eligible for the queue")
    if target not in available_agents(
        db,
        tenant_id=tenant_id,
        eligible_agent_references=(target,),
        presence_fresh_after=presence_fresh_after,
        lock=True,
    ):
        refuse_undispatchable(
            db,
            tenant_id=tenant_id,
            agent_reference=target,
            presence_fresh_after=presence_fresh_after,
        )


def _require_holder_or_override(
    *, actor: str, holder: str, supervisor_override: bool, what: str
) -> None:
    """Only the holder moves their own work; anyone else is a supervisor act.

    The transfer permission proves the actor may transfer SOMETHING. It cannot
    say whose conversation, so without this an agent holding the ordinary
    `conversation.transfer` code could move a colleague's live conversation and
    the record would read as a routine peer hand-off.
    """
    if supervisor_override or actor == holder:
        return
    raise Conflict(f"only the current holder may {what} without a supervisor override")


def _override_reason(
    *, supervisor_override: bool, override_reason: str | None
) -> str | None:
    if not supervisor_override:
        return None
    if override_reason is None:
        raise ValueError("a supervisor override must state its own reason")
    return required_text(override_reason, "supervisor override reason")


def _move_ownership(
    db: Session,
    *,
    tenant_id: UUID,
    current: ConversationAssignment,
    to_agent_reference: str,
    to_queue_id: UUID,
    reason: str,
    occurred_at: datetime,
    actor_reference: str,
) -> ConversationAssignment:
    """End the holder's assignment and start the target's, in that order.

    The order is load-bearing: `uq_conversation_assignments_active_conversation`
    permits one ASSIGNED row per conversation, so the outgoing assignment must
    close before the incoming one exists. Both rows survive as history, and the
    outgoing one is TRANSFERRED rather than RELEASED so the trail distinguishes
    "handed over" from "finished".
    """
    if utc_instant(occurred_at) < utc_instant(current.assigned_at):
        raise Conflict("a transfer cannot precede the assignment it moves")
    current.status = AssignmentStatus.TRANSFERRED
    current.released_at = occurred_at
    db.add(
        InboxWorkflowEvent(
            tenant_id=tenant_id,
            assignment_id=current.id,
            event_type="TRANSFERRED_OUT",
            occurred_at=occurred_at,
            reason=reason,
            actor_reference=actor_reference,
        )
    )
    db.flush()
    return insert_assignment(
        db,
        tenant_id=tenant_id,
        conversation_reference=current.conversation_reference,
        queue_id=to_queue_id,
        agent_reference=to_agent_reference,
        assigned_at=occurred_at,
        reason=reason,
        event_type="TRANSFERRED_IN",
        actor_reference=actor_reference,
    )


def claim_conversation(
    db: Session, *, scope: TenantScope, command: ClaimConversation
) -> ConversationAssignment:
    """An agent pulls queued work to themselves under the dispatch rules.

    A claim is not exempt from availability: an agent who is on break or at
    capacity cannot take more work by reaching for it, only by choosing to be
    available first.
    """
    tenant_id = tenant_of(scope)
    conversation = required_text(
        command.conversation_reference, "conversation reference"
    )
    agent = required_text(command.agent_reference, "agent reference")
    if agent != required_text(command.actor_reference, "actor reference"):
        raise Conflict("a claim may only be made for the acting agent")
    claimed_at = aware(command.claimed_at, "claimed_at")
    if not command.eligible_queue_ids:
        raise Conflict("a claim must name the queues the agent may pull from")
    if active_assignment(db, tenant_id, conversation) is not None:
        raise Conflict("conversation is already assigned")
    entry = db.scalar(
        select(InboxQueueEntry)
        .where(
            InboxQueueEntry.tenant_id == tenant_id,
            InboxQueueEntry.conversation_reference == conversation,
            InboxQueueEntry.status == QueueEntryStatus.QUEUED,
        )
        .with_for_update()
    )
    if entry is None:
        raise Conflict("conversation is not waiting in a queue to be claimed")
    if entry.queue_id not in set(command.eligible_queue_ids):
        raise Conflict("conversation is queued outside the agent's eligible queues")
    queue = require_queue(db, tenant_id, entry.queue_id, lock=True)
    if agent not in available_agents(
        db,
        tenant_id=tenant_id,
        eligible_agent_references=(agent,),
        presence_fresh_after=command.presence_fresh_after,
        lock=True,
    ):
        refuse_undispatchable(
            db,
            tenant_id=tenant_id,
            agent_reference=agent,
            presence_fresh_after=command.presence_fresh_after,
        )
    assignment = insert_assignment(
        db,
        tenant_id=tenant_id,
        conversation_reference=conversation,
        queue_id=queue.id,
        agent_reference=agent,
        assigned_at=claimed_at,
        reason="agent claim",
        event_type="CLAIMED",
        actor_reference=agent,
    )
    entry.status = QueueEntryStatus.PROMOTED
    entry.settled_at = claimed_at
    db.flush()
    return assignment


def transfer_conversation(
    db: Session, *, scope: TenantScope, command: TransferConversation
) -> ConversationTransferred:
    """Cold transfer: one atomic move, with both sides recorded."""
    tenant_id = tenant_of(scope)
    conversation = required_text(
        command.conversation_reference, "conversation reference"
    )
    target = required_text(command.to_agent_reference, "transfer target reference")
    reason = required_text(command.reason, "transfer reason")
    actor = required_text(command.actor_reference, "transfer actor reference")
    transferred_at = aware(command.transferred_at, "transferred_at")
    override_reason = _override_reason(
        supervisor_override=command.supervisor_override,
        override_reason=command.override_reason,
    )
    current = _held(db, tenant_id, conversation)
    _require_holder_or_override(
        actor=actor,
        holder=current.agent_reference,
        supervisor_override=command.supervisor_override,
        what="transfer this conversation",
    )
    to_queue_id = command.to_queue_id or current.queue_id
    require_queue(db, tenant_id, to_queue_id)
    _check_target_admissible(
        db,
        tenant_id=tenant_id,
        target=target,
        holder=current.agent_reference,
        cross_queue=to_queue_id != current.queue_id,
        eligible_agent_references=command.eligible_agent_references,
        presence_fresh_after=command.presence_fresh_after,
        supervisor_override=command.supervisor_override,
    )
    _settle_open_request(
        _open_request(db, tenant_id, conversation),
        settled_at=transferred_at,
        actor_reference=actor,
        note="superseded by a cold transfer",
    )
    from_agent, from_queue_id, source_id = (
        current.agent_reference,
        current.queue_id,
        current.id,
    )
    moved = _move_ownership(
        db,
        tenant_id=tenant_id,
        current=current,
        to_agent_reference=target,
        to_queue_id=to_queue_id,
        reason=reason,
        occurred_at=transferred_at,
        actor_reference=actor,
    )
    request = InboxTransferRequest(
        tenant_id=tenant_id,
        conversation_reference=conversation,
        kind=TransferKind.COLD,
        status=TransferStatus.ACCEPTED,
        source_assignment_id=source_id,
        resulting_assignment_id=moved.id,
        from_agent_reference=from_agent,
        to_agent_reference=target,
        from_queue_id=from_queue_id,
        to_queue_id=to_queue_id,
        reason=reason,
        requested_by_reference=actor,
        requested_at=transferred_at,
        settled_at=transferred_at,
        settled_by_reference=actor,
        supervisor_override=command.supervisor_override,
        override_reason=override_reason,
        notify_reference=command.notify_reference,
    )
    db.add(request)
    db.flush()
    _notify(
        db,
        tenant_id=tenant_id,
        event_type=CONVERSATION_TRANSFERRED_EVENT,
        notify_reference=command.notify_reference,
        payload={
            "request_id": str(request.id),
            "conversation_reference": conversation,
            "from_agent_reference": from_agent,
            "to_agent_reference": target,
            "reason": reason,
        },
    )
    return ConversationTransferred(
        request_id=request.id,
        previous_assignment_id=source_id,
        new_assignment_id=moved.id,
        from_agent_reference=from_agent,
        to_agent_reference=target,
        from_queue_id=from_queue_id,
        to_queue_id=to_queue_id,
        notify_reference=command.notify_reference,
    )


def request_transfer(
    db: Session, *, scope: TenantScope, command: RequestTransfer
) -> InboxTransferRequest:
    """Warm transfer: ask, and keep the current owner responsible meanwhile.

    Nothing about the live assignment changes here. That is the difference from
    a cold transfer, and it is why the customer keeps getting answers while the
    two agents work out who takes it.
    """
    tenant_id = tenant_of(scope)
    conversation = required_text(
        command.conversation_reference, "conversation reference"
    )
    target = required_text(command.to_agent_reference, "transfer target reference")
    reason = required_text(command.reason, "transfer reason")
    actor = required_text(command.actor_reference, "transfer actor reference")
    requested_at = aware(command.requested_at, "requested_at")
    expires_at = aware(command.expires_at, "expires_at")
    if utc_instant(expires_at) <= utc_instant(requested_at):
        raise ValueError("a transfer acceptance window must end after it opens")
    override_reason = _override_reason(
        supervisor_override=command.supervisor_override,
        override_reason=command.override_reason,
    )
    current = _held(db, tenant_id, conversation)
    _require_holder_or_override(
        actor=actor,
        holder=current.agent_reference,
        supervisor_override=command.supervisor_override,
        what="ask to transfer this conversation",
    )
    to_queue_id = command.to_queue_id or current.queue_id
    require_queue(db, tenant_id, to_queue_id)
    _check_target_admissible(
        db,
        tenant_id=tenant_id,
        target=target,
        holder=current.agent_reference,
        cross_queue=to_queue_id != current.queue_id,
        eligible_agent_references=command.eligible_agent_references,
        presence_fresh_after=command.presence_fresh_after,
        supervisor_override=command.supervisor_override,
    )
    if _open_request(db, tenant_id, conversation) is not None:
        raise Conflict("a transfer request is already open for this conversation")
    row = InboxTransferRequest(
        tenant_id=tenant_id,
        conversation_reference=conversation,
        kind=TransferKind.WARM,
        status=TransferStatus.REQUESTED,
        source_assignment_id=current.id,
        from_agent_reference=current.agent_reference,
        to_agent_reference=target,
        from_queue_id=current.queue_id,
        to_queue_id=to_queue_id,
        reason=reason,
        requested_by_reference=actor,
        requested_at=requested_at,
        expires_at=expires_at,
        supervisor_override=command.supervisor_override,
        override_reason=override_reason,
        notify_reference=command.notify_reference,
    )
    db.add(row)
    db.flush()
    _notify(
        db,
        tenant_id=tenant_id,
        event_type=TRANSFER_REQUESTED_EVENT,
        notify_reference=command.notify_reference,
        payload={
            "request_id": str(row.id),
            "conversation_reference": conversation,
            "from_agent_reference": current.agent_reference,
            "to_agent_reference": target,
            "reason": reason,
            "expires_at": expires_at.isoformat(),
        },
    )
    return row


def _open_transfer(
    db: Session, tenant_id: UUID, request_id: UUID
) -> InboxTransferRequest:
    row = db.scalar(
        select(InboxTransferRequest)
        .where(
            InboxTransferRequest.tenant_id == tenant_id,
            InboxTransferRequest.id == request_id,
        )
        .with_for_update()
    )
    if row is None:
        raise Conflict("transfer request was not found in the tenant")
    if row.status != TransferStatus.REQUESTED:
        raise Conflict("only an open transfer request can be settled")
    return row


def accept_transfer(
    db: Session, *, scope: TenantScope, command: AcceptTransfer
) -> ConversationTransferred:
    """Only the named target, only in time, only if still able to take it."""
    tenant_id = tenant_of(scope)
    actor = required_text(command.actor_reference, "accepting actor reference")
    accepted_at = aware(command.accepted_at, "accepted_at")
    row = _open_transfer(db, tenant_id, command.request_id)
    if actor != row.to_agent_reference:
        raise Conflict("only the transfer target may accept the request")
    if row.expires_at is not None and utc_instant(accepted_at) > utc_instant(
        row.expires_at
    ):
        raise Conflict("the transfer acceptance window has closed")
    current = _held(db, tenant_id, row.conversation_reference)
    if current.id != row.source_assignment_id:
        raise Conflict("the conversation moved since the transfer was requested")
    # Re-checked at ACCEPT, not only at request: the target may have filled up
    # while deciding, and a warm transfer that pushes an agent past capacity
    # would defeat the capacity rule it was routed by.
    if not row.supervisor_override:
        _check_target_admissible(
            db,
            tenant_id=tenant_id,
            target=row.to_agent_reference,
            holder=current.agent_reference,
            cross_queue=False,
            eligible_agent_references=command.eligible_agent_references,
            presence_fresh_after=command.presence_fresh_after,
            supervisor_override=False,
        )
    from_agent, from_queue_id, source_id = (
        current.agent_reference,
        current.queue_id,
        current.id,
    )
    moved = _move_ownership(
        db,
        tenant_id=tenant_id,
        current=current,
        to_agent_reference=row.to_agent_reference,
        to_queue_id=row.to_queue_id,
        reason=row.reason,
        occurred_at=accepted_at,
        actor_reference=actor,
    )
    row.status = TransferStatus.ACCEPTED
    row.settled_at = accepted_at
    row.settled_by_reference = actor
    row.resulting_assignment_id = moved.id
    db.flush()
    return ConversationTransferred(
        request_id=row.id,
        previous_assignment_id=source_id,
        new_assignment_id=moved.id,
        from_agent_reference=from_agent,
        to_agent_reference=row.to_agent_reference,
        from_queue_id=from_queue_id,
        to_queue_id=row.to_queue_id,
        notify_reference=row.notify_reference,
    )


def decline_transfer(
    db: Session, *, scope: TenantScope, command: DeclineTransfer
) -> InboxTransferRequest:
    """The target says no. The conversation never moved, so nothing rolls back."""
    tenant_id = tenant_of(scope)
    actor = required_text(command.actor_reference, "declining actor reference")
    declined_at = aware(command.declined_at, "declined_at")
    reason = required_text(command.reason, "decline reason")
    row = _open_transfer(db, tenant_id, command.request_id)
    if actor != row.to_agent_reference:
        raise Conflict("only the transfer target may decline the request")
    row.status = TransferStatus.DECLINED
    row.settled_at = declined_at
    row.settled_by_reference = actor
    row.settlement_reason = reason
    db.flush()
    return row


def cancel_transfer(
    db: Session, *, scope: TenantScope, command: CancelTransfer
) -> InboxTransferRequest:
    """The requester or a supervisor withdraws an open request."""
    tenant_id = tenant_of(scope)
    actor = required_text(command.actor_reference, "cancelling actor reference")
    cancelled_at = aware(command.cancelled_at, "cancelled_at")
    reason = required_text(command.reason, "cancellation reason")
    override_reason = _override_reason(
        supervisor_override=command.supervisor_override,
        override_reason=command.override_reason,
    )
    row = _open_transfer(db, tenant_id, command.request_id)
    if not command.supervisor_override and actor not in {
        row.requested_by_reference,
        row.from_agent_reference,
    }:
        raise Conflict(
            "only the requester or current holder may cancel without an override"
        )
    if override_reason is not None:
        row.override_reason = override_reason
    row.status = TransferStatus.CANCELLED
    row.settled_at = cancelled_at
    row.settled_by_reference = actor
    row.settlement_reason = reason
    db.flush()
    return row


def expire_transfer_requests(
    db: Session, *, scope: TenantScope, command: ExpireTransferRequests
) -> tuple[UUID, ...]:
    """Enforce the acceptance SLA: an unanswered request must fail back.

    A request left open forever reads to everyone as "being handled" while the
    original agent quietly still owns it. Expiry is what turns silence into a
    fact the product can act on.
    """
    expired_at = aware(command.expired_at, "expired_at")
    if command.limit < 1:
        raise ValueError("limit must be positive")
    tenant_id = tenant_of(scope)
    rows = list(
        db.scalars(
            select(InboxTransferRequest)
            .where(
                InboxTransferRequest.tenant_id == tenant_id,
                InboxTransferRequest.status == TransferStatus.REQUESTED,
                InboxTransferRequest.expires_at <= expired_at,
            )
            .order_by(InboxTransferRequest.expires_at, InboxTransferRequest.id)
            .limit(command.limit)
            .with_for_update(skip_locked=True)
        )
    )
    for row in rows:
        row.status = TransferStatus.EXPIRED
        row.settled_at = expired_at
        row.settlement_reason = "transfer acceptance SLA elapsed"
    db.flush()
    return tuple(row.id for row in rows)


def requeue_conversation(
    db: Session, *, scope: TenantScope, command: RequeueConversation
) -> InboxQueueEntry:
    """Give the work back to a queue, owned by nobody, at the back of the line."""
    tenant_id = tenant_of(scope)
    conversation = required_text(
        command.conversation_reference, "conversation reference"
    )
    reason = required_text(command.reason, "requeue reason")
    actor = required_text(command.actor_reference, "requeue actor reference")
    requeued_at = aware(command.requeued_at, "requeued_at")
    current = _held(db, tenant_id, conversation)
    if (
        command.expected_assignment_id is not None
        and current.id != command.expected_assignment_id
    ):
        # The offline sweep decided about a specific assignment. If the
        # conversation has since been transferred, that decision is stale and
        # requeueing now would take live work off whoever holds it.
        raise Conflict("the conversation moved since this requeue was decided")
    _require_holder_or_override(
        actor=actor,
        holder=current.agent_reference,
        supervisor_override=command.supervisor_override,
        what="requeue this conversation",
    )
    _override_reason(
        supervisor_override=command.supervisor_override,
        override_reason=command.override_reason,
    )
    if utc_instant(requeued_at) < utc_instant(current.assigned_at):
        raise Conflict("a requeue cannot precede the assignment it ends")
    queue = require_queue(
        db, tenant_id, command.queue_id or current.queue_id, lock=True
    )
    _settle_open_request(
        _open_request(db, tenant_id, conversation),
        settled_at=requeued_at,
        actor_reference=actor,
        note="superseded by a requeue",
    )
    current.status = AssignmentStatus.REQUEUED
    current.released_at = requeued_at
    db.add(
        InboxWorkflowEvent(
            tenant_id=tenant_id,
            assignment_id=current.id,
            event_type="REQUEUED",
            occurred_at=requeued_at,
            reason=reason,
            actor_reference=actor,
        )
    )
    db.flush()
    return admit_to_queue(
        db,
        scope=scope,
        command=AdmitToQueue(
            queue_id=queue.id,
            conversation_reference=conversation,
            entered_at=requeued_at,
        ),
    )


def escalate_conversation(
    db: Session, *, scope: TenantScope, command: EscalateConversation
) -> EscalationRequested:
    """Record that an agent asked for an escalation. Ownership never moves.

    This does NOT raise an escalation — `dotmac-operational-escalations` owns
    whether one should exist, under which policy version, and who answers it,
    for tickets and outages and inboxes alike. Modules never import each other,
    so the returned `EscalationRequested` is what the assembly hands to that
    owner's `raise_escalation`, carrying the same `dedup_key` so a retry cannot
    become two escalations on either side.

    The severity-must-rise and cooldown rules deliberately live with that owner
    too: they need to know whether an escalation is still open, and this module
    no longer stores an answer to that.
    """
    tenant_id = tenant_of(scope)
    conversation = required_text(
        command.conversation_reference, "conversation reference"
    )
    severity = required_text(command.severity, "escalation severity")
    reason = required_text(command.reason, "escalation reason")
    actor = required_text(command.actor_reference, "escalating actor reference")
    notify = required_text(command.notify_reference, "escalation notify reference")
    dedup_key = required_text(command.dedup_key, "escalation dedup key")
    requested_at = aware(command.requested_at, "requested_at")
    due_at = aware(command.due_at, "due_at") if command.due_at is not None else None
    if due_at is not None and utc_instant(due_at) < utc_instant(requested_at):
        raise ValueError("an escalation cannot be due before it is requested")

    existing = db.scalar(
        select(InboxEscalationRequest).where(
            InboxEscalationRequest.tenant_id == tenant_id,
            InboxEscalationRequest.dedup_key == dedup_key,
        )
    )
    if existing is not None:
        if (
            existing.conversation_reference != conversation
            or existing.severity != severity
        ):
            raise Conflict("escalation dedup key was reused for different work")
        return _requested(existing)

    held = active_assignment(db, tenant_id, conversation)
    row = InboxEscalationRequest(
        tenant_id=tenant_id,
        conversation_reference=conversation,
        dedup_key=dedup_key,
        severity=severity,
        reason=reason,
        requested_by_reference=actor,
        notify_reference=notify,
        assignment_id=held.id if held is not None else None,
        requested_at=requested_at,
        due_at=due_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = db.scalar(
            select(InboxEscalationRequest).where(
                InboxEscalationRequest.tenant_id == tenant_id,
                InboxEscalationRequest.dedup_key == dedup_key,
            )
        )
        if winner is None:  # pragma: no cover - database invariant defense
            raise Conflict("escalation request conflicted outside the tenant") from exc
        return _requested(winner)

    if held is not None:
        db.add(
            InboxWorkflowEvent(
                tenant_id=tenant_id,
                assignment_id=held.id,
                event_type="ESCALATION_REQUESTED",
                occurred_at=requested_at,
                reason=reason,
                actor_reference=actor,
            )
        )
        db.flush()
    _notify(
        db,
        tenant_id=tenant_id,
        event_type=ESCALATION_REQUESTED_EVENT,
        notify_reference=notify,
        payload={
            "request_id": str(row.id),
            "conversation_reference": conversation,
            "severity": severity,
            "reason": reason,
            "dedup_key": dedup_key,
        },
    )
    return _requested(row)


def _requested(row: InboxEscalationRequest) -> EscalationRequested:
    return EscalationRequested(
        request_id=row.id,
        conversation_reference=row.conversation_reference,
        severity=row.severity,
        reason=row.reason,
        notify_reference=row.notify_reference,
        dedup_key=row.dedup_key,
        assignment_id=row.assignment_id,
        due_at=row.due_at,
    )


__all__ = [
    "CONVERSATION_TRANSFERRED_EVENT",
    "ESCALATION_REQUESTED_EVENT",
    "TRANSFER_REQUESTED_EVENT",
    "accept_transfer",
    "cancel_transfer",
    "claim_conversation",
    "decline_transfer",
    "escalate_conversation",
    "expire_transfer_requests",
    "request_transfer",
    "requeue_conversation",
    "transfer_conversation",
]
