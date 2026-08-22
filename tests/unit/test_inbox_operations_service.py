"""Inbox-operations behavior canaries ported from Sub and CRM."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_inbox_operations.contracts import (
    AdmitToQueue,
    AssignConversation,
    Conflict,
    CreateQueue,
    CreateRoutingRule,
    PresenceState,
    PromoteFromQueue,
    QueueEntryStatus,
    SetAgentPresence,
)
from dotmac_inbox_operations.models import TENANT_TABLES
from dotmac_inbox_operations.service import (
    admit_to_queue,
    assign_conversation,
    cancel_queue_entry,
    create_queue,
    create_routing_rule,
    next_round_robin_agent,
    promote_from_queue,
    set_agent_presence,
)
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
NOW = datetime(2026, 8, 20, tzinfo=UTC)


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
        command=SetAgentPresence("party:agent-1", PresenceState.AVAILABLE, 1, NOW),
    )
    assignment = assign_conversation(
        db,
        scope=scope,
        command=AssignConversation("conversation:1", queue.id, "party:agent-1", NOW),
    )
    assert rule.channel_code == "whatsapp"
    assert assignment.agent_reference == "party:agent-1"


def test_agent_capacity_and_conversation_uniqueness_are_enforced(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("sales", "Sales"))
    set_agent_presence(
        db,
        scope=scope,
        command=SetAgentPresence("party:agent-2", PresenceState.AVAILABLE, 1, NOW),
    )
    command = AssignConversation("conversation:2", queue.id, "party:agent-2", NOW)
    assign_conversation(db, scope=scope, command=command)
    with pytest.raises(Conflict, match="already assigned"):
        assign_conversation(db, scope=scope, command=command)
    with pytest.raises(Conflict, match="capacity"):
        assign_conversation(
            db,
            scope=scope,
            command=AssignConversation(
                "conversation:3", queue.id, "party:agent-2", NOW
            ),
        )


def _available(db: Session, scope: TenantScope, *agents: str) -> None:
    for agent in agents:
        set_agent_presence(
            db,
            scope=scope,
            command=SetAgentPresence(agent, PresenceState.AVAILABLE, 5, NOW),
        )


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
            command=PromoteFromQueue(queue.id, "agent-1", promoted_at=NOW),
        )
    assert front.status is QueueEntryStatus.QUEUED

    _available(db, scope, "agent-1")
    assignment = promote_from_queue(
        db, scope=scope, command=PromoteFromQueue(queue.id, "agent-1", promoted_at=NOW)
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
    assert next_round_robin_agent(db, scope=scope, queue_id=queue.id) == "agent-1"

    admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-1", entered_at=NOW)
    )
    promote_from_queue(
        db, scope=scope, command=PromoteFromQueue(queue.id, "agent-1", promoted_at=NOW)
    )
    assert next_round_robin_agent(db, scope=scope, queue_id=queue.id) == "agent-2"

    admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-2", entered_at=NOW)
    )
    promote_from_queue(
        db, scope=scope, command=PromoteFromQueue(queue.id, "agent-2", promoted_at=NOW)
    )
    assert next_round_robin_agent(db, scope=scope, queue_id=queue.id) == "agent-3"


def test_rotation_wraps_and_survives_a_departed_agent(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    _available(db, scope, "agent-1", "agent-2")
    admit_to_queue(
        db, scope=scope, command=AdmitToQueue(queue.id, "conv-1", entered_at=NOW)
    )
    promote_from_queue(
        db, scope=scope, command=PromoteFromQueue(queue.id, "agent-2", promoted_at=NOW)
    )
    # Cursor is on the last agent, so the next turn wraps to the front.
    assert next_round_robin_agent(db, scope=scope, queue_id=queue.id) == "agent-1"

    set_agent_presence(
        db,
        scope=scope,
        command=SetAgentPresence("agent-2", PresenceState.OFFLINE, 5, NOW),
    )
    # The remembered agent is gone; rotation restarts rather than returning None.
    assert next_round_robin_agent(db, scope=scope, queue_id=queue.id) == "agent-1"


def test_no_available_agent_yields_no_turn(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    queue = create_queue(db, scope=scope, command=CreateQueue("support", "Support"))
    assert next_round_robin_agent(db, scope=scope, queue_id=queue.id) is None


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
        db, scope=scope, command=PromoteFromQueue(queue.id, "agent-1", promoted_at=NOW)
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
            command=PromoteFromQueue(queue.id, "agent-1", promoted_at=NOW),
        )
