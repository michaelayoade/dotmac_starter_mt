"""Staffed inbox operations; callers own authorization and transactions."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.cache import TenantScope
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_inbox_operations.contracts import (
    AdmitToQueue,
    AssignConversation,
    AssignmentStatus,
    Conflict,
    CreateQueue,
    CreateRoutingRule,
    DispatchQueues,
    PromoteFromQueue,
    QueueEntryStatus,
    ReleaseConversation,
    RouteConversation,
    RoutedConversation,
)
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxQueue,
    InboxQueueEntry,
    InboxRoundRobinCursor,
    InboxRoutingDecision,
    InboxRoutingRule,
    InboxWorkflowEvent,
)
from dotmac_inbox_operations.presence import (
    available_agents,
    refuse_undispatchable,
)
from dotmac_inbox_operations.presence import (
    set_agent_presence as set_agent_presence,
)
from dotmac_inbox_operations.validation import (
    aware,
    eligible_references,
    required_text,
    tenant_of,
    utc_instant,
)


def require_queue(
    db: Session, tenant_id: UUID, queue_id: UUID, *, lock: bool = False
) -> InboxQueue:
    statement = select(InboxQueue).where(
        InboxQueue.tenant_id == tenant_id, InboxQueue.id == queue_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None or not row.active:
        raise Conflict("inbox queue was not found or is inactive")
    return row


def create_queue(
    db: Session, *, scope: TenantScope, command: CreateQueue
) -> InboxQueue:
    row = InboxQueue(
        tenant_id=tenant_of(scope),
        code=required_text(command.code, "queue code"),
        name=required_text(command.name, "queue name"),
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def create_routing_rule(
    db: Session, *, scope: TenantScope, command: CreateRoutingRule
) -> InboxRoutingRule:
    tenant_id = tenant_of(scope)
    queue = require_queue(db, tenant_id, command.queue_id)
    if command.priority < 0:
        raise Conflict("routing priority must not be negative")
    row = InboxRoutingRule(
        tenant_id=tenant_id,
        queue_id=queue.id,
        channel_code=required_text(command.channel_code, "channel code").lower(),
        priority=command.priority,
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def _routing_decision(
    db: Session, tenant_id: UUID, decision_reference: str
) -> InboxRoutingDecision | None:
    return db.scalar(
        select(InboxRoutingDecision).where(
            InboxRoutingDecision.tenant_id == tenant_id,
            InboxRoutingDecision.decision_reference == decision_reference,
        )
    )


def _routed_result(
    db: Session, tenant_id: UUID, decision: InboxRoutingDecision
) -> RoutedConversation:
    entry = db.scalar(
        select(InboxQueueEntry).where(
            InboxQueueEntry.tenant_id == tenant_id,
            InboxQueueEntry.id == decision.queue_entry_id,
        )
    )
    if entry is None:
        raise Conflict("routing decision points to missing queue evidence")
    return RoutedConversation(
        decision_id=decision.id,
        rule_id=decision.rule_id,
        queue_id=decision.queue_id,
        queue_entry_id=entry.id,
        queue_position=entry.queue_position,
    )


def _require_same_routing_request(
    decision: InboxRoutingDecision, *, conversation: str, channel: str
) -> None:
    if (
        decision.conversation_reference != conversation
        or decision.channel_code != channel
    ):
        raise Conflict("routing decision reference was reused for different work")


def route_conversation(
    db: Session, *, scope: TenantScope, command: RouteConversation
) -> RoutedConversation:
    """Resolve, admit and record one idempotent routing decision atomically."""
    tenant_id = tenant_of(scope)
    decision_reference = required_text(
        command.decision_reference, "routing decision reference"
    )
    conversation = required_text(
        command.conversation_reference, "conversation reference"
    )
    channel = required_text(command.channel_code, "channel code").lower()
    decided_at = aware(command.routed_at, "routed_at")
    existing = _routing_decision(db, tenant_id, decision_reference)
    if existing is not None:
        _require_same_routing_request(
            existing, conversation=conversation, channel=channel
        )
        return _routed_result(db, tenant_id, existing)

    rule = db.scalar(
        select(InboxRoutingRule)
        .join(
            InboxQueue,
            and_(
                InboxQueue.tenant_id == InboxRoutingRule.tenant_id,
                InboxQueue.id == InboxRoutingRule.queue_id,
            ),
        )
        .where(
            InboxRoutingRule.tenant_id == tenant_id,
            InboxRoutingRule.channel_code == channel,
            InboxRoutingRule.active.is_(True),
            InboxQueue.active.is_(True),
        )
        .order_by(InboxRoutingRule.priority, InboxRoutingRule.id)
        .limit(1)
    )
    if rule is None:
        raise Conflict("no active routing rule matches the channel")

    decision: InboxRoutingDecision | None = None
    # Lazy by design: importing the package manifest must not construct the
    # configured database runtime.
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            entry = admit_to_queue(
                db,
                scope=scope,
                command=AdmitToQueue(
                    queue_id=rule.queue_id,
                    conversation_reference=conversation,
                    entered_at=decided_at,
                ),
            )
            decision = InboxRoutingDecision(
                tenant_id=tenant_id,
                decision_reference=decision_reference,
                conversation_reference=conversation,
                channel_code=channel,
                rule_id=rule.id,
                queue_id=rule.queue_id,
                queue_entry_id=entry.id,
                priority=rule.priority,
                decided_at=decided_at,
            )
            db.add(decision)
            db.flush()
    except IntegrityError as exc:
        winner = _routing_decision(db, tenant_id, decision_reference)
        if winner is None:
            raise Conflict(
                "routing decision conflicted with operational state"
            ) from exc
        _require_same_routing_request(
            winner, conversation=conversation, channel=channel
        )
        decision = winner
    if decision is None:  # pragma: no cover - defensive type narrowing
        raise Conflict("routing decision was not recorded")
    return _routed_result(db, tenant_id, decision)


def active_assignment(
    db: Session,
    tenant_id: UUID,
    conversation_reference: str,
    *,
    lock: bool = False,
) -> ConversationAssignment | None:
    statement = select(ConversationAssignment).where(
        ConversationAssignment.tenant_id == tenant_id,
        ConversationAssignment.conversation_reference == conversation_reference,
        ConversationAssignment.status == AssignmentStatus.ASSIGNED,
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _cursor(
    db: Session, tenant_id: UUID, queue_id: UUID, *, lock: bool
) -> InboxRoundRobinCursor | None:
    statement = select(InboxRoundRobinCursor).where(
        InboxRoundRobinCursor.tenant_id == tenant_id,
        InboxRoundRobinCursor.queue_id == queue_id,
    )
    if lock:
        statement = statement.with_for_update()
    return db.scalar(statement)


def _rotated_agent(
    available: list[str], cursor: InboxRoundRobinCursor | None
) -> str | None:
    if not available:
        return None
    last = cursor.last_assigned_agent_reference if cursor is not None else None
    start = available.index(last) + 1 if last in available else 0
    return available[start % len(available)]


def insert_assignment(
    db: Session,
    *,
    tenant_id: UUID,
    conversation_reference: str,
    queue_id: UUID,
    agent_reference: str,
    assigned_at: datetime,
    reason: str,
    event_type: str = "ASSIGNED",
    actor_reference: str | None = None,
) -> ConversationAssignment:
    row = ConversationAssignment(
        tenant_id=tenant_id,
        conversation_reference=conversation_reference,
        queue_id=queue_id,
        agent_reference=agent_reference,
        status=AssignmentStatus.ASSIGNED,
        assigned_at=assigned_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            db.add(
                InboxWorkflowEvent(
                    tenant_id=tenant_id,
                    assignment_id=row.id,
                    event_type=event_type,
                    occurred_at=assigned_at,
                    reason=reason,
                    actor_reference=actor_reference,
                )
            )
            db.flush()
    except IntegrityError as exc:
        if active_assignment(db, tenant_id, conversation_reference) is not None:
            raise Conflict("conversation is already assigned") from exc
        raise Conflict("assignment conflicted with operational state") from exc
    return row


def assign_conversation(
    db: Session, *, scope: TenantScope, command: AssignConversation
) -> ConversationAssignment:
    """Assign eligible work while serializing capacity on the presence row."""
    tenant_id = tenant_of(scope)
    conversation = required_text(
        command.conversation_reference, "conversation reference"
    )
    agent = required_text(command.agent_reference, "agent reference")
    assigned_at = aware(command.assigned_at, "assigned_at")
    eligible = eligible_references(command.eligible_agent_references)
    if agent not in eligible:
        raise Conflict("agent is not eligible for the queue")
    if active_assignment(db, tenant_id, conversation) is not None:
        raise Conflict("conversation is already assigned")
    queue = require_queue(db, tenant_id, command.queue_id)
    available = available_agents(
        db,
        tenant_id=tenant_id,
        eligible_agent_references=eligible,
        presence_fresh_after=command.presence_fresh_after,
        lock=True,
    )
    if agent not in available:
        refuse_undispatchable(
            db,
            tenant_id=tenant_id,
            agent_reference=agent,
            presence_fresh_after=command.presence_fresh_after,
        )
    return insert_assignment(
        db,
        tenant_id=tenant_id,
        conversation_reference=conversation,
        queue_id=queue.id,
        agent_reference=agent,
        assigned_at=assigned_at,
        reason="direct assignment",
    )


def release_conversation(
    db: Session, *, scope: TenantScope, command: ReleaseConversation
) -> ConversationAssignment:
    tenant_id = tenant_of(scope)
    released_at = aware(command.released_at, "released_at")
    reason = required_text(command.reason, "release reason")
    row = db.scalar(
        select(ConversationAssignment)
        .where(
            ConversationAssignment.tenant_id == tenant_id,
            ConversationAssignment.id == command.assignment_id,
        )
        .with_for_update()
    )
    if row is None:
        raise Conflict("assignment was not found in the tenant")
    if row.status != AssignmentStatus.ASSIGNED:
        raise Conflict("only an active assignment can be released")
    if utc_instant(released_at) < utc_instant(row.assigned_at):
        raise Conflict("release cannot precede assignment")
    row.status = AssignmentStatus.RELEASED
    row.released_at = released_at
    db.add(
        InboxWorkflowEvent(
            tenant_id=tenant_id,
            assignment_id=row.id,
            event_type="RELEASED",
            occurred_at=released_at,
            reason=reason,
            actor_reference=command.actor_reference,
        )
    )
    db.flush()
    return row


def admit_to_queue(
    db: Session, *, scope: TenantScope, command: AdmitToQueue
) -> InboxQueueEntry:
    """Admit active work idempotently and allocate position under a queue lock."""
    tenant_id = tenant_of(scope)
    queue = require_queue(db, tenant_id, command.queue_id, lock=True)
    conversation = required_text(
        command.conversation_reference, "conversation reference"
    )
    existing = db.scalar(
        select(InboxQueueEntry)
        .where(
            InboxQueueEntry.tenant_id == tenant_id,
            InboxQueueEntry.conversation_reference == conversation,
            InboxQueueEntry.status == QueueEntryStatus.QUEUED,
        )
        .with_for_update()
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
        entered_at=(
            aware(command.entered_at, "entered_at")
            if command.entered_at is not None
            else datetime.now(UTC)
        ),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = db.scalar(
            select(InboxQueueEntry).where(
                InboxQueueEntry.tenant_id == tenant_id,
                InboxQueueEntry.conversation_reference == conversation,
                InboxQueueEntry.status == QueueEntryStatus.QUEUED,
            )
        )
        if winner is not None:
            if winner.queue_id != queue.id:
                raise Conflict(
                    "conversation is already queued in another queue"
                ) from exc
            return winner
        raise Conflict("queue admission raced; retry the operation") from exc
    return row


def cancel_queue_entry(
    db: Session, *, scope: TenantScope, entry_id: UUID
) -> InboxQueueEntry:
    tenant_id = tenant_of(scope)
    row = db.scalar(
        select(InboxQueueEntry)
        .where(InboxQueueEntry.tenant_id == tenant_id, InboxQueueEntry.id == entry_id)
        .with_for_update()
    )
    if row is None:
        raise Conflict("queue entry was not found in the tenant")
    if row.status != QueueEntryStatus.QUEUED:
        raise Conflict("only a queued entry can be cancelled")
    row.status = QueueEntryStatus.CANCELLED
    row.settled_at = datetime.now(UTC)
    db.flush()
    return row


def next_round_robin_agent(
    db: Session,
    *,
    scope: TenantScope,
    queue_id: UUID,
    eligible_agent_references: tuple[str, ...],
    presence_fresh_after: datetime,
) -> str | None:
    """Inspect the next capacity-safe eligible turn without advancing it."""
    tenant_id = tenant_of(scope)
    require_queue(db, tenant_id, queue_id)
    cursor = _cursor(db, tenant_id, queue_id, lock=False)
    available = available_agents(
        db,
        tenant_id=tenant_id,
        eligible_agent_references=eligible_agent_references,
        presence_fresh_after=presence_fresh_after,
        lock=False,
    )
    return _rotated_agent(available, cursor)


def _promote_optional(
    db: Session,
    *,
    scope: TenantScope,
    command: PromoteFromQueue,
) -> ConversationAssignment | None:
    tenant_id = tenant_of(scope)
    queue = require_queue(db, tenant_id, command.queue_id, lock=True)
    front = db.scalar(
        select(InboxQueueEntry)
        .where(
            InboxQueueEntry.tenant_id == tenant_id,
            InboxQueueEntry.queue_id == queue.id,
            InboxQueueEntry.status == QueueEntryStatus.QUEUED,
        )
        .order_by(InboxQueueEntry.queue_position)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if front is None:
        return None
    cursor = _cursor(db, tenant_id, queue.id, lock=True)
    available = available_agents(
        db,
        tenant_id=tenant_id,
        eligible_agent_references=command.eligible_agent_references,
        presence_fresh_after=command.presence_fresh_after,
        lock=True,
    )
    agent = _rotated_agent(available, cursor)
    if agent is None:
        return None
    promoted_at = (
        aware(command.promoted_at, "promoted_at")
        if command.promoted_at is not None
        else datetime.now(UTC)
    )
    assignment = insert_assignment(
        db,
        tenant_id=tenant_id,
        conversation_reference=front.conversation_reference,
        queue_id=queue.id,
        agent_reference=agent,
        assigned_at=promoted_at,
        reason="queue promotion",
    )
    front.status = QueueEntryStatus.PROMOTED
    front.settled_at = promoted_at
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


def promote_from_queue(
    db: Session, *, scope: TenantScope, command: PromoteFromQueue
) -> ConversationAssignment:
    """Atomically choose an eligible agent, claim FIFO work and assign it."""
    assignment = _promote_optional(db, scope=scope, command=command)
    if assignment is None:
        raise Conflict("queue has no work that an eligible available agent can hold")
    return assignment


def dispatch_queues_fairly(
    db: Session, *, scope: TenantScope, command: DispatchQueues
) -> tuple[ConversationAssignment, ...]:
    """Attempt one promotion from every queue in the supplied scheduler cohort."""
    dispatched_at = aware(command.dispatched_at, "dispatched_at")
    fresh_after = aware(command.presence_fresh_after, "presence_fresh_after")
    queue_ids = [candidate.queue_id for candidate in command.queues]
    if len(queue_ids) != len(set(queue_ids)):
        raise ValueError("dispatch queue candidates must be unique")
    if queue_ids:
        locked = list(
            db.scalars(
                select(InboxQueue)
                .where(
                    InboxQueue.tenant_id == tenant_of(scope),
                    InboxQueue.id.in_(queue_ids),
                    InboxQueue.active.is_(True),
                )
                .order_by(InboxQueue.id)
                .with_for_update()
            )
        )
        if len(locked) != len(queue_ids):
            raise Conflict("a dispatch queue was not found or is inactive")
    assignments: list[ConversationAssignment] = []
    for candidate in sorted(command.queues, key=lambda value: str(value.queue_id)):
        assignment = _promote_optional(
            db,
            scope=scope,
            command=PromoteFromQueue(
                queue_id=candidate.queue_id,
                eligible_agent_references=candidate.agent_references,
                presence_fresh_after=fresh_after,
                promoted_at=dispatched_at,
            ),
        )
        if assignment is not None:
            assignments.append(assignment)
    return tuple(assignments)


__all__ = [
    "admit_to_queue",
    "assign_conversation",
    "cancel_queue_entry",
    "create_queue",
    "create_routing_rule",
    "dispatch_queues_fairly",
    "next_round_robin_agent",
    "promote_from_queue",
    "release_conversation",
    "route_conversation",
    "set_agent_presence",
]
