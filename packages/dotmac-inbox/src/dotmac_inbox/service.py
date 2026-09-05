"""Flush-only persistence owner for conversations, messages, and read cursors."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_inbox.channels import channel_spec
from dotmac_inbox.lifecycle import (
    Direction,
    SnoozeUntilReply,
    Status,
    validate_reason,
    validate_transition,
)
from dotmac_inbox.models import Conversation, ConversationReadState, Message
from dotmac_inbox.threading import InboundIdentity, dedup_key, thread_key


class ConversationNotFound(LookupError):
    """The tenant cannot see the requested conversation aggregate member."""


class ConversationConflict(ValueError):
    """A command contradicts the authoritative conversation aggregate."""


class StaleConversationState(ConversationConflict):
    """The caller's expected lifecycle state is no longer current."""


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _persisted_snooze_deadline(
    status: Status, snoozed_until: datetime | SnoozeUntilReply | None
) -> datetime | None:
    if status is Status.SNOOZED:
        if snoozed_until is None:
            raise ConversationConflict("snoozed conversation requires snoozed_until")
        if isinstance(snoozed_until, SnoozeUntilReply):
            return None
        return _aware(snoozed_until, "snoozed_until")
    if snoozed_until is not None:
        raise ConversationConflict("snoozed_until is valid only for snoozed status")
    return None


def _conversation(db: Session, tenant_id: UUID, conversation_id: UUID) -> Conversation:
    row = db.scalar(
        select(Conversation)
        .where(
            Conversation.tenant_id == tenant_id,
            Conversation.id == conversation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise ConversationNotFound("conversation not found")
    return row


def _conversation_by_thread(
    db: Session, tenant_id: UUID, canonical_thread_key: str
) -> Conversation | None:
    return db.scalar(
        select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.thread_key == canonical_thread_key,
        )
    )


def _message_by_key(db: Session, tenant_id: UUID, message_key: str) -> Message | None:
    return db.scalar(
        select(Message).where(
            Message.tenant_id == tenant_id,
            Message.message_key == message_key,
        )
    )


def _message(db: Session, tenant_id: UUID, message_id: UUID) -> Message:
    row = db.scalar(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.id == message_id)
        .with_for_update()
    )
    if row is None:
        raise ConversationNotFound("message not found")
    return row


def _require_same_message(
    row: Message,
    *,
    conversation_id: UUID,
    channel: str,
    direction: Direction,
    identity: InboundIdentity,
    author_id: UUID | None,
    occurred_at: datetime,
) -> Message:
    expected = {
        "conversation_id": conversation_id,
        "channel": channel,
        "direction": direction.value,
        "subject": identity.subject,
        "body": identity.body,
        "transport_message_ref": identity.external_message_id,
        "supplied_message_ref": identity.supplied_message_ref,
        "author_id": author_id,
        "occurred_at": occurred_at,
    }
    changed = sorted(
        name for name, value in expected.items() if getattr(row, name) != value
    )
    if changed:
        raise ConversationConflict(
            f"message key {row.message_key!r} was reused with different "
            f"{', '.join(changed)}"
        )
    return row


def _apply_message_activity(
    conversation: Conversation, *, direction: Direction, occurred_at: datetime
) -> None:
    first = conversation.first_message_at
    last = conversation.last_message_at
    conversation.first_message_at = (
        occurred_at if first is None else min(first, occurred_at)
    )
    conversation.last_message_at = (
        occurred_at if last is None else max(last, occurred_at)
    )
    current = Status(conversation.status)
    if direction is Direction.INBOUND and (
        current is Status.RESOLVED
        or (current is Status.SNOOZED and conversation.snoozed_until is None)
    ):
        conversation.status = validate_transition(current, Status.OPEN).value
        conversation.status_reason = None
        conversation.resolved_at = None
        conversation.snoozed_until = None


def create_conversation(
    db: Session,
    *,
    tenant_id: UUID,
    identity: InboundIdentity,
    status: Status = Status.OPEN,
    reason: str | None = None,
    snoozed_until: datetime | SnoozeUntilReply | None = None,
    subject: str | None = None,
    tags: tuple[str, ...] = (),
) -> Conversation:
    """Create one thread, or replay the durable winner for the same identity."""
    validated_reason = validate_reason(reason, status=status)
    persisted_snoozed_until = _persisted_snooze_deadline(status, snoozed_until)
    channel = channel_spec(identity.channel).code
    canonical_thread_key = thread_key(identity)
    existing = _conversation_by_thread(db, tenant_id, canonical_thread_key)
    if existing is not None:
        return existing

    row = Conversation(
        tenant_id=tenant_id,
        channel=channel,
        account_scope=identity.account_scope,
        contact=identity.contact,
        thread_key=canonical_thread_key,
        transport_thread_ref=identity.external_thread_id,
        supplied_thread_ref=identity.supplied_thread_ref,
        status=status.value,
        status_reason=validated_reason,
        snoozed_until=persisted_snoozed_until,
        subject=subject or identity.subject,
        tags=list(tags) or None,
    )
    # Lazy by design: importing dotmac_kernel.db constructs configured engines,
    # while reading this package's manifest/version must require no database.
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = _conversation_by_thread(db, tenant_id, canonical_thread_key)
        if winner is None:
            raise ConversationConflict(
                f"conversation identity {canonical_thread_key!r} conflicts "
                "outside its declared tenant scope"
            ) from exc
        return winner
    return row


def transition_conversation_status(
    db: Session,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    expected: Status,
    requested: Status,
    reason: str | None,
    occurred_at: datetime,
    snoozed_until: datetime | SnoozeUntilReply | None = None,
) -> Conversation:
    """Apply an expected-state lifecycle transition without owning commit."""
    occurred = _aware(occurred_at, "occurred_at")
    row = _conversation(db, tenant_id, conversation_id)
    current = Status(row.status)
    if current is not expected:
        raise StaleConversationState(
            f"conversation status expected {expected.value}, found {current.value}"
        )
    resolved = validate_transition(current, requested)
    validated_reason = validate_reason(reason, status=resolved)
    persisted_snoozed_until = _persisted_snooze_deadline(resolved, snoozed_until)

    row.status = resolved.value
    row.status_reason = validated_reason
    row.resolved_at = occurred if resolved is Status.RESOLVED else None
    row.snoozed_until = persisted_snoozed_until
    db.flush()
    return row


def record_message(
    db: Session,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    identity: InboundIdentity,
    direction: Direction,
    occurred_at: datetime,
    author_id: UUID | None = None,
    transport_observation_ref: str | None = None,
) -> Message:
    """Append one message and update the conversation activity projection."""
    occurred = _aware(occurred_at, "occurred_at")
    conversation = _conversation(db, tenant_id, conversation_id)
    channel = channel_spec(identity.channel).code
    derived_thread = thread_key(identity)
    if derived_thread != conversation.thread_key:
        raise ConversationConflict("message identity belongs to a different thread")
    if channel != conversation.channel:
        raise ConversationConflict("message channel differs from the conversation")

    key = dedup_key(identity)
    existing = _message_by_key(db, tenant_id, key.value)
    if existing is not None:
        return _require_same_message(
            existing,
            conversation_id=conversation.id,
            channel=channel,
            direction=direction,
            identity=identity,
            author_id=author_id,
            occurred_at=occurred,
        )

    row = Message(
        tenant_id=tenant_id,
        conversation_id=conversation.id,
        channel=channel,
        direction=direction.value,
        message_key=key.value,
        subject=identity.subject,
        body=identity.body,
        transport_message_ref=identity.external_message_id,
        supplied_message_ref=identity.supplied_message_ref,
        transport_observation_ref=transport_observation_ref,
        author_id=author_id,
        occurred_at=occurred,
    )

    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            _apply_message_activity(
                conversation, direction=direction, occurred_at=occurred
            )
            db.flush()
    except IntegrityError as exc:
        winner = _message_by_key(db, tenant_id, key.value)
        if winner is None:
            raise ConversationConflict(
                f"message identity {key.value!r} conflicts outside its "
                "declared tenant scope"
            ) from exc
        return _require_same_message(
            winner,
            conversation_id=conversation.id,
            channel=channel,
            direction=direction,
            identity=identity,
            author_id=author_id,
            occurred_at=occurred,
        )
    return row


def bind_message_observation_ref(
    db: Session,
    *,
    tenant_id: UUID,
    message_id: UUID,
    transport_observation_ref: str,
) -> Message:
    """Late-bind one opaque transport observation reference to a message."""
    if not transport_observation_ref.strip():
        raise ValueError("transport_observation_ref must not be empty")

    row = _message(db, tenant_id, message_id)
    current = row.transport_observation_ref
    if current is None:
        row.transport_observation_ref = transport_observation_ref
    elif current != transport_observation_ref:
        raise ConversationConflict(
            "message transport observation reference is already bound"
        )
    db.flush()
    return row


def mark_conversation_read(
    db: Session,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    actor_id: UUID,
    through_message_id: UUID | None,
    read_at: datetime,
) -> ConversationReadState:
    """Advance one operator cursor monotonically within one conversation."""
    occurred = _aware(read_at, "read_at")
    conversation = _conversation(db, tenant_id, conversation_id)
    message: Message | None = None
    if through_message_id is not None:
        message = db.scalar(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.conversation_id == conversation.id,
                Message.id == through_message_id,
            )
        )
        if message is None:
            raise ConversationNotFound("read cursor message not found in conversation")

    state = db.scalar(
        select(ConversationReadState)
        .where(
            ConversationReadState.tenant_id == tenant_id,
            ConversationReadState.conversation_id == conversation.id,
            ConversationReadState.actor_id == actor_id,
        )
        .with_for_update()
    )
    if state is None:
        state = ConversationReadState(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            actor_id=actor_id,
            last_read_message_id=through_message_id,
            last_read_at=occurred,
        )
        db.add(state)
    elif through_message_id is None:
        if occurred > state.last_read_at:
            state.last_read_at = occurred
    else:
        if message is None:  # Defensive narrowing; the branch requires an id.
            raise ConversationNotFound("read cursor message not found in conversation")
        current_message: Message | None = None
        if state.last_read_message_id is not None:
            current_message = db.scalar(
                select(Message).where(
                    Message.tenant_id == tenant_id,
                    Message.conversation_id == conversation.id,
                    Message.id == state.last_read_message_id,
                )
            )
        current_position = (
            None
            if current_message is None
            else (current_message.occurred_at, str(current_message.id))
        )
        target_position = (message.occurred_at, str(message.id))
        if current_position is None or target_position >= current_position:
            state.last_read_message_id = through_message_id
            state.last_read_at = max(state.last_read_at, occurred)
    db.flush()
    return state


__all__ = [
    "ConversationConflict",
    "ConversationNotFound",
    "StaleConversationState",
    "bind_message_observation_ref",
    "create_conversation",
    "mark_conversation_read",
    "record_message",
    "transition_conversation_status",
]
