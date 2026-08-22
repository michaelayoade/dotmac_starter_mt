"""Persistence-owner behavior for reusable conversations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_inbox import (
    AddressForm,
    ChannelSpec,
    ConversationConflict,
    Direction,
    InboundIdentity,
    MessageIdScope,
    ReasonSpec,
    StaleConversationState,
    Status,
    ThreadIdentity,
    Transport,
    create_conversation,
    mark_conversation_read,
    record_message,
    register_channels,
    register_reasons,
    reset_channel_registry_for_tests,
    reset_reason_registry_for_tests,
    transition_conversation_status,
)
from dotmac_inbox.models import Conversation, ConversationReadState, Message
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_inbox": None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            Conversation.__table__,
            Message.__table__,
            ConversationReadState.__table__,
        ],
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _declarations():
    reset_channel_registry_for_tests()
    reset_reason_registry_for_tests()
    register_channels(
        [
            ChannelSpec(
                code="email",
                owner="test_product",
                address_form=AddressForm.EMAIL,
                transport=Transport.EXTERNAL,
                thread_identity=ThreadIdentity.PROVIDER,
                message_id_scope=MessageIdScope.GLOBAL,
            )
        ]
    )
    register_reasons(
        [
            ReasonSpec(
                code="resolved_to_ticket",
                owner="test_product",
                statuses=frozenset({Status.RESOLVED}),
            )
        ]
    )
    yield
    reset_channel_registry_for_tests()
    reset_reason_registry_for_tests()


def _tenant(db: Session) -> Tenant:
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(row)
    db.flush()
    return row


def _identity(message_id: str | None = None) -> InboundIdentity:
    return InboundIdentity(
        channel="email",
        account_scope="support@example.net",
        contact="customer@example.net",
        external_thread_id="thread-1",
        external_message_id=message_id,
        subject="Hello",
        body="Can you help?",
    )


def test_create_and_record_message_own_the_thread_and_activity_clock(
    db: Session,
) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    occurred_at = datetime(2026, 8, 18, 8, tzinfo=UTC)

    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-1"),
        direction=Direction.INBOUND,
        occurred_at=occurred_at,
        transport_observation_ref="integrator-receipt-1",
    )

    assert message.conversation_id == conversation.id
    assert message.message_key == "email:m:message-1"
    assert conversation.first_message_at == occurred_at
    assert conversation.last_message_at == occurred_at
    assert conversation.status == Status.OPEN.value


def test_channel_codes_are_canonicalized_before_persistence(db: Session) -> None:
    tenant = _tenant(db)
    identity = replace(_identity("message-normalized"), channel=" EMAIL ")
    conversation = create_conversation(db, tenant_id=tenant.id, identity=identity)

    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=identity,
        direction=Direction.INBOUND,
        occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
    )

    assert conversation.channel == "email"
    assert message.channel == "email"


def test_create_conversation_replays_the_existing_thread(db: Session) -> None:
    tenant = _tenant(db)

    first = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    replay = create_conversation(db, tenant_id=tenant.id, identity=_identity())

    assert replay is first
    assert db.scalars(select(Conversation)).all() == [first]


def test_record_message_replays_an_exact_provider_redelivery(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    occurred_at = datetime(2026, 8, 18, 8, tzinfo=UTC)

    first = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-replay"),
        direction=Direction.INBOUND,
        occurred_at=occurred_at,
        transport_observation_ref="receipt-replay",
    )
    replay = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-replay"),
        direction=Direction.INBOUND,
        occurred_at=occurred_at,
        transport_observation_ref="receipt-replay",
    )

    assert replay is first
    assert db.scalars(select(Message)).all() == [first]
    assert conversation.first_message_at == occurred_at
    assert conversation.last_message_at == occurred_at


def test_message_key_reuse_with_different_content_is_a_conflict(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    occurred_at = datetime(2026, 8, 18, 8, tzinfo=UTC)
    record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-conflict"),
        direction=Direction.INBOUND,
        occurred_at=occurred_at,
    )

    changed = replace(_identity("message-conflict"), body="Different body")
    with pytest.raises(ConversationConflict, match="reused with different"):
        record_message(
            db,
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            identity=changed,
            direction=Direction.INBOUND,
            occurred_at=occurred_at,
        )


def test_a_new_inbound_message_reopens_the_existing_thread(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    resolved_at = datetime(2026, 8, 18, 8, tzinfo=UTC)
    transition_conversation_status(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        expected=Status.OPEN,
        requested=Status.RESOLVED,
        reason="resolved_to_ticket",
        occurred_at=resolved_at,
    )

    record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-2"),
        direction=Direction.INBOUND,
        occurred_at=resolved_at + timedelta(minutes=1),
    )

    assert conversation.status == Status.OPEN.value
    assert conversation.status_reason is None
    assert conversation.resolved_at is None


def test_a_stale_transition_is_refused_before_mutation(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    with pytest.raises(StaleConversationState, match="expected pending"):
        transition_conversation_status(
            db,
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            expected=Status.PENDING,
            requested=Status.RESOLVED,
            reason="resolved_to_ticket",
            occurred_at=datetime.now(UTC),
        )
    assert conversation.status == Status.OPEN.value


def test_read_state_advances_to_a_message_on_the_same_conversation(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    occurred_at = datetime(2026, 8, 18, 8, tzinfo=UTC)
    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-3"),
        direction=Direction.INBOUND,
        occurred_at=occurred_at,
    )
    actor_id = uuid4()

    state = mark_conversation_read(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        actor_id=actor_id,
        through_message_id=message.id,
        read_at=occurred_at,
    )
    repeated = mark_conversation_read(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        actor_id=actor_id,
        through_message_id=message.id,
        read_at=occurred_at - timedelta(minutes=1),
    )

    assert repeated.id == state.id
    assert repeated.last_read_at == occurred_at
    assert db.scalar(select(ConversationReadState)) is state


def test_read_state_never_moves_back_to_an_older_message(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    first_at = datetime(2026, 8, 18, 8, tzinfo=UTC)
    first = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-first"),
        direction=Direction.INBOUND,
        occurred_at=first_at,
    )
    second = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-second"),
        direction=Direction.INBOUND,
        occurred_at=first_at + timedelta(minutes=1),
    )
    actor_id = uuid4()
    state = mark_conversation_read(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        actor_id=actor_id,
        through_message_id=second.id,
        read_at=first_at + timedelta(minutes=2),
    )

    repeated = mark_conversation_read(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        actor_id=actor_id,
        through_message_id=first.id,
        read_at=first_at + timedelta(minutes=3),
    )

    assert repeated is state
    assert repeated.last_read_message_id == second.id
    assert repeated.last_read_at == first_at + timedelta(minutes=2)
