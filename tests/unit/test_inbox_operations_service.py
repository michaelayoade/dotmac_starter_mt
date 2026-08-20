"""Inbox-operations behavior canaries ported from Sub and CRM."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from dotmac_inbox_operations.contracts import (
    AssignConversation,
    Conflict,
    CreateQueue,
    CreateRoutingRule,
    PresenceState,
    SetAgentPresence,
)
from dotmac_inbox_operations.models import TENANT_TABLES
from dotmac_inbox_operations.service import (
    assign_conversation,
    create_queue,
    create_routing_rule,
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
