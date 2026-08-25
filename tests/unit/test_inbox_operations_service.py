"""Inbox-operations behavior canaries ported from Sub and CRM."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from dotmac_inbox_operations.contracts import (
    AdmitToQueue,
    AssignConversation,
    AssignmentStatus,
    Conflict,
    CreateQueue,
    CreateRoutingRule,
    DispatchQueues,
    ImportAgentPresence,
    ImportConversationAssignment,
    ImportQueueEntry,
    ImportRoundRobinRotation,
    PresenceState,
    PromoteFromQueue,
    QueueEligibility,
    QueueEntryStatus,
    ReleaseConversation,
    RouteConversation,
    SetAgentPresence,
)
from dotmac_inbox_operations.history import (
    import_agent_presence,
    import_conversation_assignment,
    import_queue_entry,
    import_round_robin_rotation,
)
from dotmac_inbox_operations.models import (
    TENANT_TABLES,
    ConversationAssignment,
    InboxAgentPresence,
    InboxQueueEntry,
    InboxRoundRobinCursor,
    InboxRoutingDecision,
)
from dotmac_inbox_operations.service import (
    admit_to_queue,
    assign_conversation,
    cancel_queue_entry,
    create_queue,
    create_routing_rule,
    dispatch_queues_fairly,
    next_round_robin_agent,
    promote_from_queue,
    release_conversation,
    route_conversation,
    set_agent_presence,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
NOW = datetime(2026, 8, 20, tzinfo=UTC)
FRESH_AFTER = NOW - timedelta(minutes=5)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_inbox_ops": None}},
    )
    Tenant.__table__.create(engine)
    from dotmac_inbox_operations import models

    for name in TENANT_TABLES:
        models.metadata_table(name).create(engine)
    with Session(engine) as session:
        session.add(Tenant(id=TENANT_A, slug="a", name="A"))
        session.flush()
        yield session
    engine.dispose()


def test_queue_routing_presence_and_assignment_share_one_owner(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    rule = create_routing_rule(
        db,
        scope=scope,
        command=CreateRoutingRule(queue.id, "whatsapp", 10),
    )
    set_agent_presence(
        db,
        scope=scope,
        command=SetAgentPresence(
            "party:agent-1", PresenceState.AVAILABLE, 1, NOW, "party:agent-1"
        ),
    )
    assignment = assign_conversation(
        db,
        scope=scope,
        command=AssignConversation(
            "conversation:1",
            queue.id,
            "party:agent-1",
            NOW,
            ("party:agent-1",),
            FRESH_AFTER,
        ),
    )
    assert rule.channel_code == "whatsapp"
    assert assignment.agent_reference == "party:agent-1"


def test_agent_capacity_and_conversation_uniqueness_are_enforced(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("sales", "Sales"))
    set_agent_presence(
        db,
        scope=scope,
        command=SetAgentPresence(
            "party:agent-2", PresenceState.AVAILABLE, 1, NOW, "party:agent-2"
        ),
    )
    command = AssignConversation(
        "conversation:2",
        queue.id,
        "party:agent-2",
        NOW,
        ("party:agent-2",),
        FRESH_AFTER,
    )
    assign_conversation(db, scope=scope, command=command)
    with pytest.raises(Conflict, match="already assigned"):
        assign_conversation(db, scope=scope, command=command)
    with pytest.raises(Conflict, match="capacity"):
        assign_conversation(
            db,
            scope=scope,
            command=AssignConversation(
                "conversation:3",
                queue.id,
                "party:agent-2",
                NOW,
                ("party:agent-2",),
                FRESH_AFTER,
            ),
        )


def _available(db: Session, scope: TenantScope, *agents: str) -> None:
    for agent in agents:
        set_agent_presence(
            db,
            scope=scope,
            command=SetAgentPresence(agent, PresenceState.AVAILABLE, 5, NOW, agent),
        )


def test_stale_presence_observation_cannot_overwrite_newer_state(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    newer = NOW + timedelta(minutes=1)
    set_agent_presence(
        db,
        scope=scope,
        command=SetAgentPresence("agent-1", PresenceState.AWAY, 2, newer, "agent-1"),
    )
    row = set_agent_presence(
        db,
        scope=scope,
        command=SetAgentPresence("agent-1", PresenceState.AVAILABLE, 5, NOW, "agent-1"),
    )
    assert row.state is PresenceState.AWAY
    assert row.assignment_capacity == 2
    observed_at = row.observed_at
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    assert observed_at == newer


def test_admission_is_idempotent_and_keeps_a_stored_position(db: Session) -> None:
    """Position is stored, not derived: a customer-visible place in the line
    must not move because a reader sorted differently, and a retried inbound
    webhook must not take two places."""
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    first = admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-1", entered_at=NOW)
    )
    second = admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-2", entered_at=NOW)
    )
    assert (first.queue_position, second.queue_position) == (1, 2)
    replay = admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-1", entered_at=NOW)
    )
    assert replay.id == first.id
    assert replay.queue_position == 1


def test_promotion_takes_the_front_and_still_honours_assignment_refusals(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    front = admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-1", entered_at=NOW)
    )
    admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-2", entered_at=NOW)
    )

    # No available agent: the queue must not be drained into an assignment
    # nobody can hold.
    with pytest.raises(Conflict):
        promote_from_queue(
            db,
            scope=scope,
            command=PromoteFromQueue(
                queue.id,
                ("agent-1",),
                FRESH_AFTER,
                promoted_at=NOW,
            ),
        )
    assert front.status is QueueEntryStatus.QUEUED

    _available(db, scope, "agent-1")
    assignment = promote_from_queue(
        db,
        scope=scope,
        command=PromoteFromQueue(
            queue.id,
            ("agent-1",),
            FRESH_AFTER,
            promoted_at=NOW,
        ),
    )
    assert assignment.conversation_reference == "conv-1"
    assert front.status is QueueEntryStatus.PROMOTED
    assert front.settled_at == NOW


def test_rotation_state_is_durable_and_advances(db: Session) -> None:
    """An in-memory cursor restarts at the same agent after every deploy, which
    quietly concentrates work on whoever sorts first."""
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    _available(db, scope, "agent-1", "agent-2", "agent-3")
    assert (
        next_round_robin_agent(
            db,
            scope=scope,
            queue_id=queue.id,
            eligible_agent_references=("agent-1", "agent-2", "agent-3"),
            presence_fresh_after=FRESH_AFTER,
        )
        == "agent-1"
    )

    admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-1", entered_at=NOW)
    )
    promote_from_queue(
        db,
        scope=scope,
        command=PromoteFromQueue(
            queue.id,
            ("agent-1", "agent-2", "agent-3"),
            FRESH_AFTER,
            promoted_at=NOW,
        ),
    )
    assert (
        next_round_robin_agent(
            db,
            scope=scope,
            queue_id=queue.id,
            eligible_agent_references=("agent-1", "agent-2", "agent-3"),
            presence_fresh_after=FRESH_AFTER,
        )
        == "agent-2"
    )

    admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-2", entered_at=NOW)
    )
    promote_from_queue(
        db,
        scope=scope,
        command=PromoteFromQueue(
            queue.id,
            ("agent-1", "agent-2", "agent-3"),
            FRESH_AFTER,
            promoted_at=NOW,
        ),
    )
    assert (
        next_round_robin_agent(
            db,
            scope=scope,
            queue_id=queue.id,
            eligible_agent_references=("agent-1", "agent-2", "agent-3"),
            presence_fresh_after=FRESH_AFTER,
        )
        == "agent-3"
    )


def test_rotation_wraps_and_survives_a_departed_agent(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    _available(db, scope, "agent-1", "agent-2")
    admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-1", entered_at=NOW)
    )
    promote_from_queue(
        db,
        scope=scope,
        command=PromoteFromQueue(
            queue.id,
            ("agent-2",),
            FRESH_AFTER,
            promoted_at=NOW,
        ),
    )
    # Cursor is on the last agent, so the next turn wraps to the front.
    assert (
        next_round_robin_agent(
            db,
            scope=scope,
            queue_id=queue.id,
            eligible_agent_references=("agent-1", "agent-2"),
            presence_fresh_after=FRESH_AFTER,
        )
        == "agent-1"
    )

    set_agent_presence(
        db,
        scope=scope,
        command=SetAgentPresence("agent-2", PresenceState.OFFLINE, 5, NOW, "agent-2"),
    )
    # The remembered agent is gone; rotation restarts rather than returning None.
    assert (
        next_round_robin_agent(
            db,
            scope=scope,
            queue_id=queue.id,
            eligible_agent_references=("agent-1", "agent-2"),
            presence_fresh_after=FRESH_AFTER,
        )
        == "agent-1"
    )


def test_no_available_agent_yields_no_turn(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    assert (
        next_round_robin_agent(
            db,
            scope=scope,
            queue_id=queue.id,
            eligible_agent_references=("agent-1",),
            presence_fresh_after=FRESH_AFTER,
        )
        is None
    )


def test_a_cancelled_entry_is_skipped_and_cannot_be_cancelled_twice(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    first = admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-1", entered_at=NOW)
    )
    admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-2", entered_at=NOW)
    )
    cancel_queue_entry(db, scope=scope, entry_id=first.id)
    assert first.status is QueueEntryStatus.CANCELLED
    with pytest.raises(Conflict):
        cancel_queue_entry(db, scope=scope, entry_id=first.id)

    _available(db, scope, "agent-1")
    assignment = promote_from_queue(
        db,
        scope=scope,
        command=PromoteFromQueue(
            queue.id,
            ("agent-1",),
            FRESH_AFTER,
            promoted_at=NOW,
        ),
    )
    assert assignment.conversation_reference == "conv-2"


def test_promoting_an_empty_queue_is_refused(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    _available(db, scope, "agent-1")
    with pytest.raises(Conflict):
        promote_from_queue(
            db,
            scope=scope,
            command=PromoteFromQueue(
                queue.id,
                ("agent-1",),
                FRESH_AFTER,
                promoted_at=NOW,
            ),
        )


def test_routing_executes_the_highest_priority_rule_and_records_evidence(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    general = create_queue(db, scope=scope, command=CreateQueue("general", "General"))
    priority = create_queue(
        db, scope=scope, command=CreateQueue("priority", "Priority")
    )
    create_routing_rule(
        db,
        scope=scope,
        command=CreateRoutingRule(general.id, "whatsapp", 20),
    )
    winning_rule = create_routing_rule(
        db,
        scope=scope,
        command=CreateRoutingRule(priority.id, "WHATSAPP", 5),
    )

    result = route_conversation(
        db,
        scope=scope,
        command=RouteConversation(
            decision_reference="ingress:message-1",
            conversation_reference="conversation:routed",
            channel_code=" WhatsApp ",
            routed_at=NOW,
        ),
    )

    assert result.rule_id == winning_rule.id
    assert result.queue_id == priority.id
    assert result.queue_position == 1
    assert db.get(InboxRoutingDecision, result.decision_id) is not None
    assert db.get(InboxQueueEntry, result.queue_entry_id) is not None

    replay = route_conversation(
        db,
        scope=scope,
        command=RouteConversation(
            decision_reference="ingress:message-1",
            conversation_reference="conversation:routed",
            channel_code="whatsapp",
            routed_at=NOW,
        ),
    )
    assert replay == result
    with pytest.raises(Conflict, match="different work"):
        route_conversation(
            db,
            scope=scope,
            command=RouteConversation(
                decision_reference="ingress:message-1",
                conversation_reference="conversation:different",
                channel_code="whatsapp",
                routed_at=NOW,
            ),
        )


def test_routing_without_a_rule_fails_closed(db: Session) -> None:
    with pytest.raises(Conflict, match="routing rule"):
        route_conversation(
            db,
            scope=TenantScope(TENANT_A),
            command=RouteConversation(
                "ingress:missing",
                "conversation:missing",
                "email",
                NOW,
            ),
        )


def test_assignment_requires_current_queue_eligibility_and_fresh_presence(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    _available(db, scope, "eligible", "excluded")

    with pytest.raises(Conflict, match="eligible"):
        assign_conversation(
            db,
            scope=scope,
            command=AssignConversation(
                "conversation:excluded",
                queue.id,
                "excluded",
                NOW,
                ("eligible",),
                FRESH_AFTER,
            ),
        )

    with pytest.raises(Conflict, match="fresh"):
        assign_conversation(
            db,
            scope=scope,
            command=AssignConversation(
                "conversation:stale",
                queue.id,
                "eligible",
                NOW,
                ("eligible",),
                NOW + timedelta(seconds=1),
            ),
        )


def test_released_conversation_can_be_reassigned_and_requeued(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    _available(db, scope, "agent-1", "agent-2")
    first_entry = admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conversation:repeat", NOW)
    )
    first_assignment = promote_from_queue(
        db,
        scope=scope,
        command=PromoteFromQueue(
            queue.id,
            ("agent-1",),
            FRESH_AFTER,
            promoted_at=NOW,
        ),
    )
    release_conversation(
        db,
        scope=scope,
        command=ReleaseConversation(
            first_assignment.id,
            NOW + timedelta(minutes=1),
            "supervisor transfer",
        ),
    )

    second_entry = admit_to_queue(
        db,
        scope=scope,
        command=AdmitToQueue(
            queue.id,
            "conversation:repeat",
            NOW + timedelta(minutes=2),
        ),
    )
    second_assignment = promote_from_queue(
        db,
        scope=scope,
        command=PromoteFromQueue(
            queue.id,
            ("agent-2",),
            FRESH_AFTER,
            promoted_at=NOW + timedelta(minutes=2),
        ),
    )

    assert first_entry.id != second_entry.id
    assert second_entry.queue_position == 2
    assert first_assignment.id != second_assignment.id
    assert second_assignment.agent_reference == "agent-2"
    history = list(
        db.scalars(
            select(ConversationAssignment)
            .where(
                ConversationAssignment.conversation_reference == "conversation:repeat"
            )
            .order_by(ConversationAssignment.assigned_at)
        )
    )
    assert [row.status.value for row in history] == ["RELEASED", "ASSIGNED"]


def test_fair_dispatch_does_not_let_a_blocked_queue_hide_an_assignable_one(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    blocked = create_queue(db, scope=scope, command=CreateQueue("blocked", "Blocked"))
    ready = create_queue(db, scope=scope, command=CreateQueue("ready", "Ready"))
    admit_to_queue(db, scope=scope, command=AdmitToQueue(blocked.id, "oldest", NOW))
    admit_to_queue(db, scope=scope, command=AdmitToQueue(ready.id, "newer", NOW))
    _available(db, scope, "ready-agent")

    assignments = dispatch_queues_fairly(
        db,
        scope=scope,
        command=DispatchQueues(
            queues=(
                QueueEligibility(blocked.id, ("blocked-agent",)),
                QueueEligibility(ready.id, ("ready-agent",)),
            ),
            dispatched_at=NOW,
            presence_fresh_after=FRESH_AFTER,
        ),
    )

    assert [row.conversation_reference for row in assignments] == ["newer"]


def test_history_import_preserves_operational_ids_and_exact_replay(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("history", "History"))
    created_at = NOW - timedelta(days=2)
    updated_at = NOW - timedelta(days=1)

    presence_command = ImportAgentPresence(
        id=uuid.uuid4(),
        agent_reference="agent:historical",
        state=PresenceState.AWAY,
        assignment_capacity=4,
        observed_at=NOW,
        created_at=created_at,
        updated_at=updated_at,
    )
    presence = import_agent_presence(db, scope=scope, command=presence_command)
    assert presence.id == presence_command.id
    assert import_agent_presence(db, scope=scope, command=presence_command) is presence

    assignment_command = ImportConversationAssignment(
        id=uuid.uuid4(),
        conversation_reference="conversation:historical",
        queue_id=queue.id,
        agent_reference="agent:historical",
        status=AssignmentStatus.RELEASED,
        assigned_at=NOW - timedelta(hours=2),
        released_at=NOW - timedelta(hours=1),
        created_at=created_at,
        updated_at=updated_at,
    )
    assignment = import_conversation_assignment(
        db, scope=scope, command=assignment_command
    )
    assert assignment.id == assignment_command.id
    assert (
        import_conversation_assignment(db, scope=scope, command=assignment_command)
        is assignment
    )

    entry_command = ImportQueueEntry(
        id=uuid.uuid4(),
        queue_id=queue.id,
        conversation_reference="conversation:historical",
        queue_position=7,
        status=QueueEntryStatus.PROMOTED,
        entered_at=NOW - timedelta(hours=3),
        settled_at=NOW - timedelta(hours=2),
        created_at=created_at,
        updated_at=updated_at,
    )
    entry = import_queue_entry(db, scope=scope, command=entry_command)
    assert entry.id == entry_command.id
    assert import_queue_entry(db, scope=scope, command=entry_command) is entry

    cursor_command = ImportRoundRobinRotation(
        id=uuid.uuid4(),
        queue_id=queue.id,
        last_assigned_agent_reference="agent:historical",
        rotation_count=9,
        created_at=created_at,
        updated_at=updated_at,
    )
    cursor = import_round_robin_rotation(db, scope=scope, command=cursor_command)
    assert cursor.id == cursor_command.id
    assert (
        import_round_robin_rotation(db, scope=scope, command=cursor_command) is cursor
    )
    assert db.get(InboxAgentPresence, presence.id) is presence
    assert db.get(InboxQueueEntry, entry.id) is entry
    assert db.get(InboxRoundRobinCursor, cursor.id) is cursor


def test_history_import_refuses_same_operational_id_with_different_facts(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    command = ImportAgentPresence(
        id=uuid.uuid4(),
        agent_reference="agent:historical-conflict",
        state=PresenceState.AWAY,
        assignment_capacity=4,
        observed_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    import_agent_presence(db, scope=scope, command=command)

    with pytest.raises(Conflict, match="different assignment_capacity"):
        import_agent_presence(
            db,
            scope=scope,
            command=ImportAgentPresence(
                id=command.id,
                agent_reference=command.agent_reference,
                state=command.state,
                assignment_capacity=5,
                observed_at=command.observed_at,
                created_at=command.created_at,
                updated_at=command.updated_at,
            ),
        )


def test_history_import_refuses_incoherent_terminal_timestamps(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("invalid", "Invalid"))

    with pytest.raises(Conflict, match="requires released_at"):
        import_conversation_assignment(
            db,
            scope=scope,
            command=ImportConversationAssignment(
                id=uuid.uuid4(),
                conversation_reference="conversation:invalid",
                queue_id=queue.id,
                agent_reference="agent:invalid",
                status=AssignmentStatus.RELEASED,
                assigned_at=NOW,
                released_at=None,
                created_at=NOW,
                updated_at=NOW,
            ),
        )

    with pytest.raises(Conflict, match="queued history cannot carry settled_at"):
        import_queue_entry(
            db,
            scope=scope,
            command=ImportQueueEntry(
                id=uuid.uuid4(),
                queue_id=queue.id,
                conversation_reference="conversation:invalid",
                queue_position=1,
                status=QueueEntryStatus.QUEUED,
                entered_at=NOW,
                settled_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            ),
        )
