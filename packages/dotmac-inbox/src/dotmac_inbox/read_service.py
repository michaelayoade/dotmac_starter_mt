"""Read-only SQL owner for tenant conversations and message timelines."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime
from typing import Final
from uuid import UUID

from sqlalchemy import Select, and_, or_, select
from sqlalchemy.orm import Session

from dotmac_inbox.lifecycle import Direction, Status
from dotmac_inbox.models import Conversation, Message
from dotmac_inbox.read import (
    ConversationFilter,
    ConversationPage,
    ConversationView,
    CursorKind,
    MessagePage,
    MessageView,
)
from dotmac_inbox.service import ConversationNotFound

MAX_LIMIT: Final[int] = 100


def _limit(limit: int) -> int:
    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= MAX_LIMIT
    ):
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return limit


def _scope(*values: str) -> str:
    return hashlib.sha256("\x1f".join(values).encode()).hexdigest()


def _conversation_scope(tenant_id: UUID, filters: ConversationFilter) -> str:
    return _scope(
        "conversation-list",
        str(tenant_id),
        json.dumps(
            {
                "statuses": [status.value for status in filters.statuses],
                "channel": filters.channel,
                "account_scope": filters.account_scope,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
    )


def _cursor(kind: CursorKind, values: tuple[str | None, str], scope: str) -> str:
    payload = {
        "v": 1,
        "kind": kind.value,
        "scope": scope,
        "at": values[0],
        "id": values[1],
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_time(value: datetime) -> str:
    """Keep SQLite's timezone-dropping result valid for the opaque contract."""
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _decode(cursor: str, kind: CursorKind, scope: str) -> tuple[datetime | None, UUID]:
    if not isinstance(cursor, str) or not cursor or len(cursor) > 512:
        raise ValueError("invalid cursor")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode())
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("kind") != kind.value
            or set(payload) != {"v", "kind", "scope", "at", "id"}
            or payload.get("scope") != scope
            or not isinstance(payload["id"], str)
        ):
            raise ValueError
        value = payload["at"]
        parsed = None if value is None else datetime.fromisoformat(value)
        if parsed is not None and (parsed.tzinfo is None or parsed.utcoffset() is None):
            raise ValueError
        return parsed, UUID(payload["id"])
    except (
        ValueError,
        TypeError,
        KeyError,
        UnicodeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise ValueError("invalid cursor") from exc


def _conversation_view(row: Conversation) -> ConversationView:
    return ConversationView(
        id=row.id,
        tenant_id=row.tenant_id,
        channel=row.channel,
        account_scope=row.account_scope,
        contact=row.contact,
        thread_key=row.thread_key,
        status=Status(row.status),
        status_reason=row.status_reason,
        subject=row.subject,
        tags=tuple(row.tags or ()),
        first_message_at=row.first_message_at,
        last_message_at=row.last_message_at,
        resolved_at=row.resolved_at,
        snoozed_until=row.snoozed_until,
    )


def _message_view(row: Message) -> MessageView:
    return MessageView(
        id=row.id,
        tenant_id=row.tenant_id,
        conversation_id=row.conversation_id,
        channel=row.channel,
        direction=Direction(row.direction),
        message_key=row.message_key,
        subject=row.subject,
        body=row.body,
        transport_message_ref=row.transport_message_ref,
        supplied_message_ref=row.supplied_message_ref,
        transport_observation_ref=row.transport_observation_ref,
        author_id=row.author_id,
        occurred_at=row.occurred_at,
    )


def get_conversation(
    db: Session, *, tenant_id: UUID, conversation_id: UUID
) -> ConversationView:
    row = db.scalar(
        select(Conversation).where(
            Conversation.tenant_id == tenant_id, Conversation.id == conversation_id
        )
    )
    if row is None:
        raise ConversationNotFound("conversation not found")
    return _conversation_view(row)


def list_conversations(
    db: Session,
    *,
    tenant_id: UUID,
    filters: ConversationFilter | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> ConversationPage:
    size = _limit(limit)
    active = filters or ConversationFilter()
    scope = _conversation_scope(tenant_id, active)
    statement: Select[tuple[Conversation]] = select(Conversation).where(
        Conversation.tenant_id == tenant_id
    )
    if active.statuses:
        statement = statement.where(
            Conversation.status.in_([s.value for s in active.statuses])
        )
    if active.channel is not None:
        statement = statement.where(Conversation.channel == active.channel)
    if active.account_scope is not None:
        statement = statement.where(Conversation.account_scope == active.account_scope)
    statement = statement.order_by(
        Conversation.last_message_at.desc().nullslast(), Conversation.id.desc()
    )
    if cursor is not None:
        at, row_id = _decode(cursor, CursorKind.CONVERSATIONS, scope)
        if at is None:
            statement = statement.where(
                Conversation.last_message_at.is_(None), Conversation.id < row_id
            )
        else:
            statement = statement.where(
                or_(
                    Conversation.last_message_at < at,
                    Conversation.last_message_at.is_(None),
                    and_(Conversation.last_message_at == at, Conversation.id < row_id),
                )
            )
    rows = list(db.scalars(statement.limit(size + 1)))
    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = _cursor(
            CursorKind.CONVERSATIONS,
            (
                _cursor_time(last.last_message_at) if last.last_message_at else None,
                str(last.id),
            ),
            scope,
        )
    return ConversationPage(tuple(_conversation_view(row) for row in rows), next_cursor)


def list_messages(
    db: Session,
    *,
    tenant_id: UUID,
    conversation_id: UUID,
    cursor: str | None = None,
    limit: int = 50,
) -> MessagePage:
    size = _limit(limit)
    scope = _scope("message-list", str(tenant_id), str(conversation_id))
    if (
        db.scalar(
            select(Conversation.id).where(
                Conversation.tenant_id == tenant_id, Conversation.id == conversation_id
            )
        )
        is None
    ):
        raise ConversationNotFound("conversation not found")
    statement: Select[tuple[Message]] = select(Message).where(
        Message.tenant_id == tenant_id, Message.conversation_id == conversation_id
    )
    statement = statement.order_by(Message.occurred_at.asc(), Message.id.asc())
    if cursor is not None:
        at, row_id = _decode(cursor, CursorKind.MESSAGES, scope)
        if at is None:
            raise ValueError("invalid cursor")
        statement = statement.where(
            or_(
                Message.occurred_at > at,
                and_(Message.occurred_at == at, Message.id > row_id),
            )
        )
    rows = list(db.scalars(statement.limit(size + 1)))
    has_more = len(rows) > size
    rows = rows[:size]
    next_cursor = None
    if has_more:
        last = rows[-1]
        next_cursor = _cursor(
            CursorKind.MESSAGES,
            (_cursor_time(last.occurred_at), str(last.id)),
            scope,
        )
    return MessagePage(tuple(_message_view(row) for row in rows), next_cursor)


__all__ = ["MAX_LIMIT", "get_conversation", "list_conversations", "list_messages"]
