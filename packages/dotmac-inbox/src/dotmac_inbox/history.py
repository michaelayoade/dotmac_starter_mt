"""Identity-preserving adoption seam for historical conversation aggregates.

Runtime commands mint identities and apply live consequences.  Adoption has a
different job: reproduce an already-authoritative aggregate under its existing
identity without replaying runtime transitions.  These commands validate the
same channel, lifecycle, threading and message-identity contracts, preserve
source timestamps, and make an exact replay idempotent.  Reusing an identity
for different facts is always a conflict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_inbox.channels import channel_spec
from dotmac_inbox.lifecycle import Direction, Status, validate_reason
from dotmac_inbox.models import Conversation, ConversationReadState, Message
from dotmac_inbox.service import (
    ConversationConflict,
    ConversationNotFound,
)
from dotmac_inbox.threading import InboundIdentity, dedup_key, thread_key


@dataclass(frozen=True, slots=True)
class ImportConversation:
    id: UUID
    identity: InboundIdentity
    status: Status
    reason: str | None
    subject: str | None
    tags: tuple[str, ...]
    first_message_at: datetime | None
    last_message_at: datetime | None
    resolved_at: datetime | None
    snoozed_until: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ImportMessage:
    id: UUID
    conversation_id: UUID
    identity: InboundIdentity
    direction: Direction
    occurred_at: datetime
    author_id: UUID | None
    transport_observation_ref: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ImportReadState:
    id: UUID
    conversation_id: UUID
    actor_id: UUID
    last_read_message_id: UUID | None
    last_read_at: datetime
    created_at: datetime
    updated_at: datetime


_Row = TypeVar("_Row", Conversation, Message, ConversationReadState)


def _aware(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value


def _optional_aware(value: datetime | None, field: str) -> datetime | None:
    return None if value is None else _aware(value, field)


def _instant(value: datetime) -> datetime:
    """Normalize SQLite's timezone-naive unit-test round trip."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _equal(current: Any, expected: Any) -> bool:
    if isinstance(current, datetime) and isinstance(expected, datetime):
        return _instant(current) == _instant(expected)
    return bool(current == expected)


def _require_same(row: _Row, *, identity: str, expected: dict[str, Any]) -> _Row:
    changed = sorted(
        name
        for name, value in expected.items()
        if not _equal(getattr(row, name), value)
    )
    if changed:
        raise ConversationConflict(
            f"historical {identity} was reused with different {', '.join(changed)}"
        )
    return row


def _history_timestamps(
    created_at: datetime, updated_at: datetime
) -> tuple[datetime, datetime]:
    created = _aware(created_at, "created_at")
    updated = _aware(updated_at, "updated_at")
    if _instant(updated) < _instant(created):
        raise ValueError("updated_at cannot precede created_at")
    return created, updated


def _validate_conversation_times(command: ImportConversation) -> None:
    first = _optional_aware(command.first_message_at, "first_message_at")
    last = _optional_aware(command.last_message_at, "last_message_at")
    resolved = _optional_aware(command.resolved_at, "resolved_at")
    snoozed = _optional_aware(command.snoozed_until, "snoozed_until")
    if first is not None and last is not None and _instant(last) < _instant(first):
        raise ValueError("last_message_at cannot precede first_message_at")
    if command.status is not Status.RESOLVED and resolved is not None:
        raise ConversationConflict("resolved_at is valid only for resolved history")
    if command.status is Status.SNOOZED:
        if snoozed is None:
            raise ConversationConflict("snoozed history requires snoozed_until")
    elif snoozed is not None:
        raise ConversationConflict("snoozed_until is valid only for snoozed history")


def import_conversation(
    db: Session, *, tenant_id: UUID, command: ImportConversation
) -> Conversation:
    """Import or exactly replay one historical conversation under its source id."""
    _validate_conversation_times(command)
    created_at, updated_at = _history_timestamps(command.created_at, command.updated_at)
    channel = channel_spec(command.identity.channel).code
    canonical_thread_key = thread_key(command.identity)
    reason = validate_reason(command.reason, status=command.status)
    expected = {
        "tenant_id": tenant_id,
        "id": command.id,
        "channel": channel,
        "account_scope": command.identity.account_scope,
        "contact": command.identity.contact,
        "thread_key": canonical_thread_key,
        "transport_thread_ref": command.identity.external_thread_id,
        "status": command.status.value,
        "status_reason": reason,
        "subject": command.subject,
        "tags": list(command.tags) or None,
        "first_message_at": command.first_message_at,
        "last_message_at": command.last_message_at,
        "resolved_at": command.resolved_at,
        "snoozed_until": command.snoozed_until,
        "created_at": created_at,
        "updated_at": updated_at,
    }

    def replay() -> Conversation | None:
        by_id = db.scalar(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.id == command.id,
            )
        )
        if by_id is not None:
            return _require_same(
                by_id, identity=f"conversation id {command.id}", expected=expected
            )
        by_thread = db.scalar(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.thread_key == canonical_thread_key,
            )
        )
        if by_thread is not None:
            raise ConversationConflict(
                f"historical thread {canonical_thread_key!r} already belongs to "
                f"conversation {by_thread.id}"
            )
        return None

    existing = replay()
    if existing is not None:
        return existing
    row = Conversation(**expected)
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = replay()
        if winner is None:
            raise ConversationConflict(
                "historical conversation conflicted outside its declared tenant"
            ) from exc
        return winner
    return row


def import_message(db: Session, *, tenant_id: UUID, command: ImportMessage) -> Message:
    """Import a historical message without replaying live activity consequences."""
    occurred_at = _aware(command.occurred_at, "occurred_at")
    created_at, updated_at = _history_timestamps(command.created_at, command.updated_at)
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.id == command.conversation_id,
        )
    )
    if conversation is None:
        raise ConversationNotFound("historical message conversation not found")
    channel = channel_spec(command.identity.channel).code
    if (
        channel != conversation.channel
        or thread_key(command.identity) != conversation.thread_key
    ):
        raise ConversationConflict("historical message belongs to a different thread")
    message_key = dedup_key(command.identity).value
    expected = {
        "tenant_id": tenant_id,
        "id": command.id,
        "conversation_id": command.conversation_id,
        "channel": channel,
        "direction": command.direction.value,
        "message_key": message_key,
        "subject": command.identity.subject,
        "body": command.identity.body,
        "transport_message_ref": command.identity.external_message_id,
        "transport_observation_ref": command.transport_observation_ref,
        "author_id": command.author_id,
        "occurred_at": occurred_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }

    def replay() -> Message | None:
        by_id = db.scalar(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.id == command.id,
            )
        )
        if by_id is not None:
            return _require_same(
                by_id, identity=f"message id {command.id}", expected=expected
            )
        by_key = db.scalar(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.message_key == message_key,
            )
        )
        if by_key is not None:
            raise ConversationConflict(
                f"historical message key {message_key!r} already belongs to "
                f"message {by_key.id}"
            )
        return None

    existing = replay()
    if existing is not None:
        return existing
    row = Message(**expected)
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = replay()
        if winner is None:
            raise ConversationConflict(
                "historical message conflicted outside its declared tenant"
            ) from exc
        return winner
    return row


def import_read_state(
    db: Session, *, tenant_id: UUID, command: ImportReadState
) -> ConversationReadState:
    """Import one historical monotonic read cursor under its source id."""
    last_read_at = _aware(command.last_read_at, "last_read_at")
    created_at, updated_at = _history_timestamps(command.created_at, command.updated_at)
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.tenant_id == tenant_id,
            Conversation.id == command.conversation_id,
        )
    )
    if conversation is None:
        raise ConversationNotFound("historical read-state conversation not found")
    if command.last_read_message_id is not None:
        message = db.scalar(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.conversation_id == command.conversation_id,
                Message.id == command.last_read_message_id,
            )
        )
        if message is None:
            raise ConversationNotFound(
                "historical read-state message not found in conversation"
            )
    expected = {
        "tenant_id": tenant_id,
        "id": command.id,
        "conversation_id": command.conversation_id,
        "actor_id": command.actor_id,
        "last_read_message_id": command.last_read_message_id,
        "last_read_at": last_read_at,
        "created_at": created_at,
        "updated_at": updated_at,
    }

    def replay() -> ConversationReadState | None:
        by_id = db.scalar(
            select(ConversationReadState).where(
                ConversationReadState.tenant_id == tenant_id,
                ConversationReadState.id == command.id,
            )
        )
        if by_id is not None:
            return _require_same(
                by_id, identity=f"read-state id {command.id}", expected=expected
            )
        by_actor = db.scalar(
            select(ConversationReadState).where(
                ConversationReadState.tenant_id == tenant_id,
                ConversationReadState.conversation_id == command.conversation_id,
                ConversationReadState.actor_id == command.actor_id,
            )
        )
        if by_actor is not None:
            raise ConversationConflict(
                "historical read cursor already belongs to a different source id"
            )
        return None

    existing = replay()
    if existing is not None:
        return existing
    row = ConversationReadState(**expected)
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = replay()
        if winner is None:
            raise ConversationConflict(
                "historical read state conflicted outside its declared tenant"
            ) from exc
        return winner
    return row


__all__ = [
    "ImportConversation",
    "ImportMessage",
    "ImportReadState",
    "import_conversation",
    "import_message",
    "import_read_state",
]
