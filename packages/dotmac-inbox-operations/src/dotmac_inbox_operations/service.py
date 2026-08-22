"""Staffed inbox operations; callers own authorization and transactions."""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_inbox_operations.contracts import (
    AssignConversation,
    AssignmentStatus,
    Conflict,
    CreateQueue,
    CreateRoutingRule,
    PresenceState,
    SetAgentPresence,
)
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxAgentPresence,
    InboxQueue,
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


__all__ = [
    "assign_conversation",
    "create_queue",
    "create_routing_rule",
    "set_agent_presence",
]
