"""Staffed inbox operations; callers own authorization and transactions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_inbox_operations.contracts import (
    AdmitToQueue,
    AssignConversation,
    AssignmentStatus,
    Conflict,
    CreateQueue,
    CreateRoutingRule,
    PresenceState,
    PromoteFromQueue,
    QueueEntryStatus,
    SetAgentPresence,
)
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxAgentPresence,
    InboxQueue,
    InboxQueueEntry,
    InboxRoundRobinCursor,
    InboxRoutingRule,
    InboxWorkflowEvent,
)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-inbox-operations requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError(f"{field} must not be empty")
    return value


def _queue(db: Session, tenant_id: UUID, queue_id: UUID) -> InboxQueue:
    row = db.scalar(
        select(InboxQueue).where(
            InboxQueue.tenant_id == tenant_id, InboxQueue.id == queue_id
        )
    )
    if row is None or not row.active:
        raise Conflict("inbox queue was not found or is inactive")
    return row


def create_queue(
    db: Session, *, scope: TenantScope, command: CreateQueue
) -> InboxQueue:
    row = InboxQueue(
        tenant_id=_tenant(scope),
        code=_required(command.code, "queue code"),
        name=_required(command.name, "queue name"),
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def create_routing_rule(
    db: Session, *, scope: TenantScope, command: CreateRoutingRule
) -> InboxRoutingRule:
    tenant_id = _tenant(scope)
    queue = _queue(db, tenant_id, command.queue_id)
    if command.priority < 0:
        raise Conflict("routing priority must not be negative")
    row = InboxRoutingRule(
        tenant_id=tenant_id,
        queue_id=queue.id,
        channel_code=_required(command.channel_code, "channel code").lower(),
        priority=command.priority,
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def set_agent_presence(
    db: Session, *, scope: TenantScope, command: SetAgentPresence
) -> InboxAgentPresence:
    tenant_id = _tenant(scope)
    agent = _required(command.agent_reference, "agent reference")
    if command.assignment_capacity < 0:
        raise Conflict("assignment capacity must not be negative")
    row = db.scalar(
        select(InboxAgentPresence).where(
            InboxAgentPresence.tenant_id == tenant_id,
            InboxAgentPresence.agent_reference == agent,
        )
    )
    if row is None:
        row = InboxAgentPresence(
            tenant_id=tenant_id,
            agent_reference=agent,
            state=command.state,
            assignment_capacity=command.assignment_capacity,
            observed_at=command.observed_at,
        )
        db.add(row)
    else:
        row.state = command.state
        row.assignment_capacity = command.assignment_capacity
        row.observed_at = command.observed_at
    db.flush()
    return row


def assign_conversation(
    db: Session, *, scope: TenantScope, command: AssignConversation
) -> ConversationAssignment:
    tenant_id = _tenant(scope)
    conversation = _required(command.conversation_reference, "conversation reference")
    agent = _required(command.agent_reference, "agent reference")
    duplicate = db.scalar(
        select(ConversationAssignment.id).where(
            ConversationAssignment.tenant_id == tenant_id,
            ConversationAssignment.conversation_reference == conversation,
            ConversationAssignment.status == AssignmentStatus.ASSIGNED,
        )
    )
    if duplicate is not None:
        raise Conflict("conversation is already assigned")
    queue = _queue(db, tenant_id, command.queue_id)
    presence = db.scalar(
        select(InboxAgentPresence).where(
            InboxAgentPresence.tenant_id == tenant_id,
            InboxAgentPresence.agent_reference == agent,
        )
    )
    if presence is None or presence.state != PresenceState.AVAILABLE:
        raise Conflict("agent is not available for inbox assignment")
    assigned = db.scalar(
        select(func.count())
        .select_from(ConversationAssignment)
        .where(
            ConversationAssignment.tenant_id == tenant_id,
            ConversationAssignment.agent_reference == agent,
            ConversationAssignment.status == AssignmentStatus.ASSIGNED,
        )
    )
    if assigned is None or assigned >= presence.assignment_capacity:
        raise Conflict("agent assignment capacity is exhausted")
    row = ConversationAssignment(
        tenant_id=tenant_id,
        conversation_reference=conversation,
        queue_id=queue.id,
        agent_reference=agent,
        status=AssignmentStatus.ASSIGNED,
        assigned_at=command.assigned_at,
    )
    db.add(row)
    db.flush()
    db.add(
        InboxWorkflowEvent(
            tenant_id=tenant_id,
            assignment_id=row.id,
            event_type="ASSIGNED",
            occurred_at=command.assigned_at,
            reason="routing assignment",
        )
    )
    db.flush()
    return row


def admit_to_queue(
    db: Session, *, scope: TenantScope, command: AdmitToQueue
) -> InboxQueueEntry:
    """Admit a conversation to the back of a queue, idempotently.

    Position is allocated as `max(position) + 1` WITHIN the queue and stored,
    not derived at read time: a customer-visible place in the line must not
    change because a reader sorted differently or a row settled.
    """
    tenant_id = _tenant(scope)
    queue = _queue(db, tenant_id, command.queue_id)
    conversation = _required(command.conversation_reference, "conversation reference")
    existing = db.scalar(
        select(InboxQueueEntry).where(
            InboxQueueEntry.tenant_id == tenant_id,
            InboxQueueEntry.conversation_reference == conversation,
        )
    )
    if existing is not None:
        if existing.queue_id != queue.id:
            raise Conflict("conversation is already queued in another queue")
        return existing
    highest = db.scalar(
        select(func.max(InboxQueueEntry.queue_position)).where(
            InboxQueueEntry.tenant_id == tenant_id,
            InboxQueueEntry.queue_id == queue.id,
        )
    )
    row = InboxQueueEntry(
        tenant_id=tenant_id,
        queue_id=queue.id,
        conversation_reference=conversation,
        queue_position=(highest or 0) + 1,
        status=QueueEntryStatus.QUEUED,
        entered_at=command.entered_at or datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def cancel_queue_entry(
    db: Session, *, scope: TenantScope, entry_id: UUID
) -> InboxQueueEntry:
    tenant_id = _tenant(scope)
    row = db.scalar(
        select(InboxQueueEntry).where(
            InboxQueueEntry.tenant_id == tenant_id, InboxQueueEntry.id == entry_id
        )
    )
    if row is None:
        raise Conflict("queue entry was not found in the tenant")
    if row.status is not QueueEntryStatus.QUEUED:
        raise Conflict("only a queued entry can be cancelled")
    row.status = QueueEntryStatus.CANCELLED
    row.settled_at = datetime.now(UTC)
    db.flush()
    return row


def next_round_robin_agent(
    db: Session, *, scope: TenantScope, queue_id: UUID
) -> str | None:
    """The agent whose turn it is, from DURABLE rotation state.

    In-memory rotation restarts at the same agent after every deploy, which
    quietly concentrates work on whoever sorts first. Returns `None` when no
    agent is available, so the caller can leave the conversation queued rather
    than assign it to nobody.
    """
    tenant_id = _tenant(scope)
    _queue(db, tenant_id, queue_id)
    available = list(
        db.scalars(
            select(InboxAgentPresence)
            .where(
                InboxAgentPresence.tenant_id == tenant_id,
                InboxAgentPresence.state == PresenceState.AVAILABLE,
            )
            .order_by(InboxAgentPresence.agent_reference)
        )
    )
    if not available:
        return None
    references = [presence.agent_reference for presence in available]
    cursor = db.scalar(
        select(InboxRoundRobinCursor).where(
            InboxRoundRobinCursor.tenant_id == tenant_id,
            InboxRoundRobinCursor.queue_id == queue_id,
        )
    )
    last = cursor.last_assigned_agent_reference if cursor is not None else None
    if last in references:
        start = references.index(last) + 1
    else:
        start = 0
    return references[start % len(references)]


def promote_from_queue(
    db: Session, *, scope: TenantScope, command: PromoteFromQueue
) -> ConversationAssignment:
    """Promote the FRONT of the queue and advance the durable cursor.

    Promotion goes through `assign_conversation`, so an entry cannot become an
    assignment while bypassing the presence and capacity refusals — the queue
    decides ORDER, the assignment rules still decide admissibility.
    """
    tenant_id = _tenant(scope)
    queue = _queue(db, tenant_id, command.queue_id)
    agent = _required(command.agent_reference, "agent reference")
    front = db.scalar(
        select(InboxQueueEntry)
        .where(
            InboxQueueEntry.tenant_id == tenant_id,
            InboxQueueEntry.queue_id == queue.id,
            InboxQueueEntry.status == QueueEntryStatus.QUEUED,
        )
        .order_by(InboxQueueEntry.queue_position)
        .limit(1)
    )
    if front is None:
        raise Conflict("queue is empty")
    promoted_at = command.promoted_at or datetime.now(UTC)
    assignment = assign_conversation(
        db,
        scope=scope,
        command=AssignConversation(
            conversation_reference=front.conversation_reference,
            queue_id=queue.id,
            agent_reference=agent,
            assigned_at=promoted_at,
        ),
    )
    front.status = QueueEntryStatus.PROMOTED
    front.settled_at = promoted_at
    cursor = db.scalar(
        select(InboxRoundRobinCursor).where(
            InboxRoundRobinCursor.tenant_id == tenant_id,
            InboxRoundRobinCursor.queue_id == queue.id,
        )
    )
    if cursor is None:
        cursor = InboxRoundRobinCursor(
            tenant_id=tenant_id,
            queue_id=queue.id,
            last_assigned_agent_reference=agent,
            rotation_count=1,
        )
        db.add(cursor)
    else:
        cursor.last_assigned_agent_reference = agent
        cursor.rotation_count += 1
    db.flush()
    return assignment


__all__ = [
    "admit_to_queue",
    "assign_conversation",
    "cancel_queue_entry",
    "create_queue",
    "create_routing_rule",
    "next_round_robin_agent",
    "promote_from_queue",
    "set_agent_presence",
]
