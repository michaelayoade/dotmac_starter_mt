"""Persistence-owner behavior for reusable conversations."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, tzinfo
from uuid import uuid4

import pytest
from dotmac_inbox import (
    UNTIL_REPLY,
    AddressForm,
    ChannelSpec,
    ConversationConflict,
    ConversationNotFound,
    Direction,
    ImportConversation,
    ImportMessage,
    ImportReadState,
    InboundIdentity,
    MessageIdScope,
    ReasonSpec,
    SnoozeUntilReply,
    StaleConversationState,
    Status,
    ThreadIdentity,
    Transport,
    bind_message_observation_ref,
    create_conversation,
    import_conversation,
    import_message,
    import_read_state,
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


class _OffsetlessTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> None:
        return None

    def dst(self, dt: datetime | None) -> None:
        return None

    def tzname(self, dt: datetime | None) -> str:
        return "offsetless"


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


def test_create_snoozed_conversation_requires_an_explicit_target(
    db: Session,
) -> None:
    tenant = _tenant(db)

    with pytest.raises(ConversationConflict, match="requires snoozed_until"):
        create_conversation(
            db,
            tenant_id=tenant.id,
            identity=_identity(),
            status=Status.SNOOZED,
        )


def test_create_conversation_accepts_explicit_until_reply(db: Session) -> None:
    tenant = _tenant(db)

    conversation = create_conversation(
        db,
        tenant_id=tenant.id,
        identity=_identity(),
        status=Status.SNOOZED,
        snoozed_until=UNTIL_REPLY,
    )

    assert conversation.status == Status.SNOOZED.value
    assert conversation.snoozed_until is None


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


def test_late_binding_sets_observation_ref_without_changing_message_or_activity(
    db: Session,
) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    occurred_at = datetime(2026, 8, 18, 8, tzinfo=UTC)
    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-bind"),
        direction=Direction.INBOUND,
        occurred_at=occurred_at,
    )
    immutable = (
        message.message_key,
        message.subject,
        message.body,
        message.direction,
        message.conversation_id,
        message.transport_message_ref,
        message.author_id,
        message.occurred_at,
    )
    activity = (
        conversation.first_message_at,
        conversation.last_message_at,
        conversation.status,
        conversation.status_reason,
        conversation.resolved_at,
        conversation.snoozed_until,
    )

    bound = bind_message_observation_ref(
        db,
        tenant_id=tenant.id,
        message_id=message.id,
        transport_observation_ref="  receipt:opaque  ",
    )

    assert bound is message
    assert message.transport_observation_ref == "  receipt:opaque  "
    assert (
        message.message_key,
        message.subject,
        message.body,
        message.direction,
        message.conversation_id,
        message.transport_message_ref,
        message.author_id,
        message.occurred_at,
    ) == immutable
    assert (
        conversation.first_message_at,
        conversation.last_message_at,
        conversation.status,
        conversation.status_reason,
        conversation.resolved_at,
        conversation.snoozed_until,
    ) == activity


def test_late_binding_exact_replay_is_idempotent_and_initial_value_is_compatible(
    db: Session,
) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-bound-at-admission"),
        direction=Direction.INBOUND,
        occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
        transport_observation_ref="receipt:admission",
    )

    assert (
        bind_message_observation_ref(
            db,
            tenant_id=tenant.id,
            message_id=message.id,
            transport_observation_ref="receipt:admission",
        )
        is message
    )
    assert message.transport_observation_ref == "receipt:admission"


def test_record_message_replay_is_not_an_alternate_late_binding_path(
    db: Session,
) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    identity = _identity("message-replay-before-bind")
    occurred_at = datetime(2026, 8, 18, 8, tzinfo=UTC)
    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=identity,
        direction=Direction.INBOUND,
        occurred_at=occurred_at,
    )

    replay = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=identity,
        direction=Direction.INBOUND,
        occurred_at=occurred_at,
        transport_observation_ref="receipt:late",
    )

    assert replay is message
    assert message.transport_observation_ref is None
    assert db.scalars(select(Message)).all() == [message]


def test_late_binding_conflict_preserves_original_ref(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-bind-conflict"),
        direction=Direction.INBOUND,
        occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
        transport_observation_ref="receipt:original",
    )

    with pytest.raises(ConversationConflict):
        bind_message_observation_ref(
            db,
            tenant_id=tenant.id,
            message_id=message.id,
            transport_observation_ref="receipt:different",
        )

    assert message.transport_observation_ref == "receipt:original"


def test_late_binding_rejects_empty_ref_without_mutation(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-bind-empty"),
        direction=Direction.INBOUND,
        occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="must not be empty"):
        bind_message_observation_ref(
            db,
            tenant_id=tenant.id,
            message_id=message.id,
            transport_observation_ref=" \t\n",
        )

    assert message.transport_observation_ref is None


def test_late_binding_refuses_missing_and_cross_tenant_messages(db: Session) -> None:
    tenant = _tenant(db)
    other_tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    message = record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=_identity("message-bind-scope"),
        direction=Direction.INBOUND,
        occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
    )

    with pytest.raises(ConversationNotFound):
        bind_message_observation_ref(
            db,
            tenant_id=other_tenant.id,
            message_id=message.id,
            transport_observation_ref="receipt:cross-tenant",
        )
    with pytest.raises(ConversationNotFound):
        bind_message_observation_ref(
            db,
            tenant_id=tenant.id,
            message_id=uuid4(),
            transport_observation_ref="receipt:missing",
        )
    assert message.transport_observation_ref is None


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


def test_snooze_until_reply_is_explicit_and_persists_a_null_deadline(
    db: Session,
) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    occurred_at = datetime(2026, 8, 18, 8, tzinfo=UTC)

    transitioned = transition_conversation_status(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        expected=Status.OPEN,
        requested=Status.SNOOZED,
        reason=None,
        occurred_at=occurred_at,
        snoozed_until=UNTIL_REPLY,
    )

    assert transitioned.status == Status.SNOOZED.value
    assert transitioned.snoozed_until is None
    assert isinstance(UNTIL_REPLY, SnoozeUntilReply)


def test_snooze_without_an_explicit_deadline_is_still_rejected(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())

    with pytest.raises(ConversationConflict, match="requires snoozed_until"):
        transition_conversation_status(
            db,
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            expected=Status.OPEN,
            requested=Status.SNOOZED,
            reason=None,
            occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
        )


def test_until_reply_is_rejected_for_non_snoozed_statuses(db: Session) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())

    with pytest.raises(ConversationConflict, match="only for snoozed status"):
        transition_conversation_status(
            db,
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            expected=Status.OPEN,
            requested=Status.OPEN,
            reason=None,
            occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
            snoozed_until=UNTIL_REPLY,
        )


def test_a_finite_snooze_stays_until_its_deadline_and_is_timezone_aware(
    db: Session,
) -> None:
    tenant = _tenant(db)
    conversation = create_conversation(db, tenant_id=tenant.id, identity=_identity())
    deadline = datetime(2026, 8, 19, 8, tzinfo=UTC)

    transitioned = transition_conversation_status(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        expected=Status.OPEN,
        requested=Status.SNOOZED,
        reason=None,
        occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
        snoozed_until=deadline,
    )
    assert transitioned.snoozed_until == deadline

    with pytest.raises(ValueError, match="timezone-aware"):
        transition_conversation_status(
            db,
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            expected=Status.SNOOZED,
            requested=Status.SNOOZED,
            reason=None,
            occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
            snoozed_until=datetime(2026, 8, 19, 8),
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        transition_conversation_status(
            db,
            tenant_id=tenant.id,
            conversation_id=conversation.id,
            expected=Status.SNOOZED,
            requested=Status.SNOOZED,
            reason=None,
            occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
            snoozed_until=datetime(2026, 8, 19, 8, tzinfo=_OffsetlessTimezone()),
        )


def test_inbound_message_wakes_only_an_until_reply_snooze(db: Session) -> None:
    tenant = _tenant(db)
    until_reply = create_conversation(
        db,
        tenant_id=tenant.id,
        identity=replace(
            _identity("until-reply-thread"), external_thread_id="until-reply-thread"
        ),
    )
    transition_conversation_status(
        db,
        tenant_id=tenant.id,
        conversation_id=until_reply.id,
        expected=Status.OPEN,
        requested=Status.SNOOZED,
        reason=None,
        occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
        snoozed_until=UNTIL_REPLY,
    )
    record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=until_reply.id,
        identity=replace(
            _identity("until-reply-message"),
            external_thread_id="until-reply-thread",
        ),
        direction=Direction.INBOUND,
        occurred_at=datetime(2026, 8, 18, 9, tzinfo=UTC),
    )
    assert until_reply.status == Status.OPEN.value

    finite = create_conversation(
        db,
        tenant_id=tenant.id,
        identity=replace(
            _identity("finite-thread"), external_thread_id="finite-thread"
        ),
    )
    transition_conversation_status(
        db,
        tenant_id=tenant.id,
        conversation_id=finite.id,
        expected=Status.OPEN,
        requested=Status.SNOOZED,
        reason=None,
        occurred_at=datetime(2026, 8, 18, 8, tzinfo=UTC),
        snoozed_until=datetime(2026, 8, 19, 8, tzinfo=UTC),
    )
    record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=finite.id,
        identity=replace(
            _identity("finite-message"), external_thread_id="finite-thread"
        ),
        direction=Direction.INBOUND,
        occurred_at=datetime(2026, 8, 18, 9, tzinfo=UTC),
    )
    assert finite.status == Status.SNOOZED.value


@pytest.mark.parametrize("direction", [Direction.OUTBOUND, Direction.INTERNAL])
def test_non_inbound_message_does_not_wake_an_until_reply_snooze(
    db: Session, direction: Direction
) -> None:
    tenant = _tenant(db)
    identity = replace(
        _identity(f"{direction.value}-message"),
        external_thread_id=f"{direction.value}-thread",
    )
    conversation = create_conversation(
        db,
        tenant_id=tenant.id,
        identity=identity,
        status=Status.SNOOZED,
        snoozed_until=UNTIL_REPLY,
    )

    record_message(
        db,
        tenant_id=tenant.id,
        conversation_id=conversation.id,
        identity=identity,
        direction=direction,
        occurred_at=datetime(2026, 8, 18, 9, tzinfo=UTC),
    )

    assert conversation.status == Status.SNOOZED.value
    assert conversation.snoozed_until is None


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


def test_history_import_preserves_identity_timestamps_and_exact_replay(
    db: Session,
) -> None:
    tenant = _tenant(db)
    created_at = datetime(2025, 1, 2, 8, tzinfo=UTC)
    updated_at = created_at + timedelta(days=1)
    first_message_at = created_at + timedelta(minutes=1)
    resolved_at = first_message_at + timedelta(hours=1)
    conversation_command = ImportConversation(
        id=uuid4(),
        identity=_identity(),
        status=Status.RESOLVED,
        reason=None,
        subject="Historical subject",
        tags=("adopted",),
        first_message_at=first_message_at,
        last_message_at=first_message_at,
        resolved_at=resolved_at,
        snoozed_until=None,
        created_at=created_at,
        updated_at=updated_at,
    )

    conversation = import_conversation(
        db, tenant_id=tenant.id, command=conversation_command
    )
    assert conversation.id == conversation_command.id
    assert conversation.created_at == created_at
    assert (
        import_conversation(db, tenant_id=tenant.id, command=conversation_command)
        is conversation
    )

    message_command = ImportMessage(
        id=uuid4(),
        conversation_id=conversation.id,
        identity=_identity("historical-message"),
        direction=Direction.INBOUND,
        occurred_at=first_message_at,
        author_id=None,
        transport_observation_ref="receipt:historical",
        created_at=created_at,
        updated_at=updated_at,
    )
    message = import_message(db, tenant_id=tenant.id, command=message_command)
    assert message.id == message_command.id
    assert message.transport_observation_ref == "receipt:historical"
    assert import_message(db, tenant_id=tenant.id, command=message_command) is message
    assert conversation.first_message_at == first_message_at
    assert conversation.last_message_at == first_message_at
    assert conversation.status == Status.RESOLVED.value
    assert conversation.resolved_at == resolved_at

    read_command = ImportReadState(
        id=uuid4(),
        conversation_id=conversation.id,
        actor_id=uuid4(),
        last_read_message_id=message.id,
        last_read_at=first_message_at,
        created_at=created_at,
        updated_at=updated_at,
    )
    state = import_read_state(db, tenant_id=tenant.id, command=read_command)
    assert state.id == read_command.id
    assert import_read_state(db, tenant_id=tenant.id, command=read_command) is state


def test_history_import_refuses_same_identity_with_different_facts(
    db: Session,
) -> None:
    tenant = _tenant(db)
    occurred_at = datetime(2025, 1, 2, 8, tzinfo=UTC)
    command = ImportConversation(
        id=uuid4(),
        identity=_identity(),
        status=Status.OPEN,
        reason=None,
        subject="Original",
        tags=(),
        first_message_at=None,
        last_message_at=None,
        resolved_at=None,
        snoozed_until=None,
        created_at=occurred_at,
        updated_at=occurred_at,
    )
    import_conversation(db, tenant_id=tenant.id, command=command)

    with pytest.raises(ConversationConflict, match="different subject"):
        import_conversation(
            db,
            tenant_id=tenant.id,
            command=replace(command, subject="Changed"),
        )
    with pytest.raises(ConversationConflict, match="already belongs"):
        import_conversation(
            db,
            tenant_id=tenant.id,
            command=replace(command, id=uuid4()),
        )


def test_history_import_preserves_until_reply_and_rejects_finite_rewrite(
    db: Session,
) -> None:
    tenant = _tenant(db)
    recorded_at = datetime(2025, 1, 2, 8, tzinfo=UTC)
    command = ImportConversation(
        id=uuid4(),
        identity=_identity("historical-until-reply"),
        status=Status.SNOOZED,
        reason=None,
        subject="Snoozed history",
        tags=(),
        first_message_at=None,
        last_message_at=None,
        resolved_at=None,
        snoozed_until=UNTIL_REPLY,
        created_at=recorded_at,
        updated_at=recorded_at,
    )

    conversation = import_conversation(db, tenant_id=tenant.id, command=command)
    assert conversation.status == Status.SNOOZED.value
    assert conversation.snoozed_until is None
    assert import_conversation(db, tenant_id=tenant.id, command=command) is conversation

    with pytest.raises(ConversationConflict, match="different snoozed_until"):
        import_conversation(
            db,
            tenant_id=tenant.id,
            command=replace(
                command,
                snoozed_until=recorded_at + timedelta(days=1),
            ),
        )


def test_history_import_rejects_implicit_or_naive_snooze_targets(db: Session) -> None:
    tenant = _tenant(db)
    recorded_at = datetime(2025, 1, 2, 8, tzinfo=UTC)
    command = ImportConversation(
        id=uuid4(),
        identity=_identity("historical-invalid-snooze"),
        status=Status.SNOOZED,
        reason=None,
        subject="Invalid snoozed history",
        tags=(),
        first_message_at=None,
        last_message_at=None,
        resolved_at=None,
        snoozed_until=None,
        created_at=recorded_at,
        updated_at=recorded_at,
    )

    with pytest.raises(ConversationConflict, match="requires snoozed_until"):
        import_conversation(db, tenant_id=tenant.id, command=command)

    with pytest.raises(ValueError, match="timezone-aware"):
        import_conversation(
            db,
            tenant_id=tenant.id,
            command=replace(
                command,
                snoozed_until=datetime(2025, 1, 3, 8),
            ),
        )


def test_history_import_rejects_until_reply_for_non_snoozed_status(
    db: Session,
) -> None:
    tenant = _tenant(db)
    recorded_at = datetime(2025, 1, 2, 8, tzinfo=UTC)
    command = ImportConversation(
        id=uuid4(),
        identity=_identity("historical-invalid-until-reply"),
        status=Status.OPEN,
        reason=None,
        subject="Invalid snooze marker",
        tags=(),
        first_message_at=None,
        last_message_at=None,
        resolved_at=None,
        snoozed_until=UNTIL_REPLY,
        created_at=recorded_at,
        updated_at=recorded_at,
    )

    with pytest.raises(ConversationConflict, match="only for snoozed history"):
        import_conversation(db, tenant_id=tenant.id, command=command)
