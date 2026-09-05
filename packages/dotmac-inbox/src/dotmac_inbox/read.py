"""Typed, tenant-scoped read contracts for the inbox aggregate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from dotmac_inbox.lifecycle import Direction, Status


class CursorKind(StrEnum):
    CONVERSATIONS = "conversations"
    MESSAGES = "messages"


@dataclass(frozen=True, slots=True)
class ConversationFilter:
    """The deliberately closed set of predicates supported by the list read."""

    statuses: tuple[Status, ...] = ()
    channel: str | None = None
    account_scope: str | None = None

    def __post_init__(self) -> None:
        statuses = tuple(Status(status) for status in self.statuses)
        if len(set(statuses)) != len(statuses):
            raise ValueError("statuses must not contain duplicates")
        object.__setattr__(self, "statuses", statuses)
        for name in ("channel", "account_scope"):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must not be blank")


@dataclass(frozen=True, slots=True)
class ConversationView:
    id: UUID
    tenant_id: UUID
    channel: str
    account_scope: str
    contact: str | None
    thread_key: str
    status: Status
    status_reason: str | None
    subject: str | None
    tags: tuple[str, ...]
    first_message_at: datetime | None
    last_message_at: datetime | None
    resolved_at: datetime | None
    snoozed_until: datetime | None


@dataclass(frozen=True, slots=True)
class MessageView:
    id: UUID
    tenant_id: UUID
    conversation_id: UUID
    channel: str
    direction: Direction
    message_key: str
    subject: str | None
    body: str | None
    transport_message_ref: str | None
    supplied_message_ref: str | None
    transport_observation_ref: str | None
    author_id: UUID | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationPage:
    items: tuple[ConversationView, ...]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class MessagePage:
    items: tuple[MessageView, ...]
    next_cursor: str | None


__all__ = [
    "ConversationFilter",
    "ConversationPage",
    "ConversationView",
    "CursorKind",
    "MessagePage",
    "MessageView",
]
