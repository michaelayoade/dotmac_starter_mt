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
    PresenceState,
    PromoteFromQueue,
    QueueEntryStatus,
    ReleaseConversation,
    RouteConversation,
    RoutedConversation,
    SetAgentPresence,
)
from dotmac_inbox_operations.models import (
    ConversationAssignment,
    InboxAgentPresence,
    InboxQueue,
    InboxQueueEntry,
    InboxRoundRobinCursor,
    InboxRoutingDecision,
    InboxRoutingRule,
    InboxWorkflowEvent,
)


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-inbox-operations requires TenantScope")
    return scope.tenant_id


def _required(value: str, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _utc_instant(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive round trip for portable unit canaries."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _eligible_references(values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(
        sorted({_required(value, "agent reference") for value in values})
    )
    if not normalized:
        raise Conflict("queue eligibility must name at least one agent")
    return normalized


def _queue(
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
    tenant_id = _tenant(scope)
    decision_reference = _required(
        command.decision_reference, "routing decision reference"
    )
    conversation = _required(command.conversation_reference, "conversation reference")
    channel = _required(command.channel_code, "channel code").lower()
    decided_at = _aware(command.routed_at, "routed_at")
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


def set_agent_presence(
    db: Session, *, scope: TenantScope, command: SetAgentPresence
) -> InboxAgentPresence:
    tenant_id = _tenant(scope)
    agent = _required(command.agent_reference, "agent reference")
    observed_at = _aware(command.observed_at, "observed_at")
    if command.assignment_capacity < 0:
        raise Conflict("assignment capacity must not be negative")
    row = db.scalar(
        select(InboxAgentPresence)
        .where(
            InboxAgentPresence.tenant_id == tenant_id,
            InboxAgentPresence.agent_reference == agent,
        )
        .with_for_update()
    )
    if row is None:
        candidate = InboxAgentPresence(
            tenant_id=tenant_id,
            agent_reference=agent,
            state=command.state,
            assignment_capacity=command.assignment_capacity,
            observed_at=observed_at,
        )
        from dotmac_kernel.db import conflict_savepoint

        try:
            with conflict_savepoint(db):
                db.add(candidate)
                db.flush()
        except IntegrityError as exc:
            row = db.scalar(
                select(InboxAgentPresence)
                .where(
                    InboxAgentPresence.tenant_id == tenant_id,
                    InboxAgentPresence.agent_reference == agent,
                )
                .with_for_update()
            )
            if row is None:  # pragma: no cover - database invariant defense
                raise Conflict("agent presence conflicted outside the tenant") from exc
        else:
            row = candidate
    if _utc_instant(observed_at) >= _utc_instant(row.observed_at):
        row.state = command.state
        row.assignment_capacity = command.assignment_capacity
        row.observed_at = observed_at
    db.flush()
    return row


def _active_assignment(
    db: Session, tenant_id: UUID, conversation_reference: str
) -> ConversationAssignment | None:
    return db.scalar(
        select(ConversationAssignment).where(
            ConversationAssignment.tenant_id == tenant_id,
            ConversationAssignment.conversation_reference == conversation_reference,
            ConversationAssignment.status == AssignmentStatus.ASSIGNED,
        )
    )


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


def _available_agents(
    db: Session,
    *,
    tenant_id: UUID,
    eligible_agent_references: tuple[str, ...],
    presence_fresh_after: datetime,
    lock: bool,
) -> list[str]:
    eligible = _eligible_references(eligible_agent_references)
    fresh_after = _aware(presence_fresh_after, "presence_fresh_after")
    statement = (
        select(InboxAgentPresence)
        .where(
            InboxAgentPresence.tenant_id == tenant_id,
            InboxAgentPresence.agent_reference.in_(eligible),
            InboxAgentPresence.state == PresenceState.AVAILABLE,
            InboxAgentPresence.observed_at >= fresh_after,
            InboxAgentPresence.assignment_capacity > 0,
        )
        .order_by(InboxAgentPresence.agent_reference)
    )
    if lock:
        statement = statement.with_for_update()
    presences = list(db.scalars(statement))
    if not presences:
        return []
    references = tuple(row.agent_reference for row in presences)
    counts = db.execute(
        select(
            ConversationAssignment.agent_reference,
            func.count(ConversationAssignment.id),
        )
        .where(
            ConversationAssignment.tenant_id == tenant_id,
            ConversationAssignment.agent_reference.in_(references),
            ConversationAssignment.status == AssignmentStatus.ASSIGNED,
        )
        .group_by(ConversationAssignment.agent_reference)
    )
    assigned_counts: dict[str, int] = {}
    for agent_reference, count in counts:
        assigned_counts[agent_reference] = count
    return [
        row.agent_reference
        for row in presences
        if assigned_counts.get(row.agent_reference, 0) < row.assignment_capacity
    ]


def _rotated_agent(
    available: list[str], cursor: InboxRoundRobinCursor | None
) -> str | None:
    if not available:
        return None
    last = cursor.last_assigned_agent_reference if cursor is not None else None
    start = available.index(last) + 1 if last in available else 0
    return available[start % len(available)]


def _insert_assignment(
    db: Session,
    *,
    tenant_id: UUID,
    conversation_reference: str,
    queue_id: UUID,
    agent_reference: str,
    assigned_at: datetime,
    reason: str,
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
                    event_type="ASSIGNED",
                    occurred_at=assigned_at,
                    reason=reason,
                )
            )
            db.flush()
    except IntegrityError as exc:
        if _active_assignment(db, tenant_id, conversation_reference) is not None:
            raise Conflict("conversation is already assigned") from exc
        raise Conflict("assignment conflicted with operational state") from exc
    return row


def assign_conversation(
    db: Session, *, scope: TenantScope, command: AssignConversation
) -> ConversationAssignment:
    """Assign eligible work while serializing capacity on the presence row."""
    tenant_id = _tenant(scope)
    conversation = _required(command.conversation_reference, "conversation reference")
    agent = _required(command.agent_reference, "agent reference")
    assigned_at = _aware(command.assigned_at, "assigned_at")
    eligible = _eligible_references(command.eligible_agent_references)
    if agent not in eligible:
        raise Conflict("agent is not eligible for the queue")
    if _active_assignment(db, tenant_id, conversation) is not None:
        raise Conflict("conversation is already assigned")
    queue = _queue(db, tenant_id, command.queue_id)
    available = _available_agents(
        db,
        tenant_id=tenant_id,
        eligible_agent_references=eligible,
        presence_fresh_after=command.presence_fresh_after,
        lock=True,
    )
    if agent not in available:
        presence = db.scalar(
            select(InboxAgentPresence).where(
                InboxAgentPresence.tenant_id == tenant_id,
                InboxAgentPresence.agent_reference == agent,
            )
        )
        if presence is None or _utc_instant(presence.observed_at) < _utc_instant(
            command.presence_fresh_after
        ):
            raise Conflict("agent presence is missing or not fresh")
        if presence.state != PresenceState.AVAILABLE:
            raise Conflict("agent is not available for inbox assignment")
        raise Conflict("agent assignment capacity is exhausted")
    return _insert_assignment(
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
    tenant_id = _tenant(scope)
    released_at = _aware(command.released_at, "released_at")
    reason = _required(command.reason, "release reason")
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
    if _utc_instant(released_at) < _utc_instant(row.assigned_at):
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
        )
    )
    db.flush()
    return row


def admit_to_queue(
    db: Session, *, scope: TenantScope, command: AdmitToQueue
) -> InboxQueueEntry:
    """Admit active work idempotently and allocate position under a queue lock."""
    tenant_id = _tenant(scope)
    queue = _queue(db, tenant_id, command.queue_id, lock=True)
    conversation = _required(command.conversation_reference, "conversation reference")
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
            _aware(command.entered_at, "entered_at")
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
    tenant_id = _tenant(scope)
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
    tenant_id = _tenant(scope)
    _queue(db, tenant_id, queue_id)
    cursor = _cursor(db, tenant_id, queue_id, lock=False)
    available = _available_agents(
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
    tenant_id = _tenant(scope)
    queue = _queue(db, tenant_id, command.queue_id, lock=True)
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
    available = _available_agents(
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
        _aware(command.promoted_at, "promoted_at")
        if command.promoted_at is not None
        else datetime.now(UTC)
    )
    assignment = _insert_assignment(
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
    dispatched_at = _aware(command.dispatched_at, "dispatched_at")
    fresh_after = _aware(command.presence_fresh_after, "presence_fresh_after")
    queue_ids = [candidate.queue_id for candidate in command.queues]
    if len(queue_ids) != len(set(queue_ids)):
        raise ValueError("dispatch queue candidates must be unique")
    if queue_ids:
        locked = list(
            db.scalars(
                select(InboxQueue)
                .where(
                    InboxQueue.tenant_id == _tenant(scope),
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
