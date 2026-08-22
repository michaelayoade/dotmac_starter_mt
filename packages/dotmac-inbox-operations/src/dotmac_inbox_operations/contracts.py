"""Staffed inbox operation commands and states."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


class InboxOperationsError(Exception):
    """Base inbox-operations refusal."""


class Conflict(InboxOperationsError):
    """The requested inbox operation is inadmissible."""


class PresenceState(enum.StrEnum):
    AVAILABLE = "AVAILABLE"
    AWAY = "AWAY"
    OFFLINE = "OFFLINE"


class AssignmentStatus(enum.StrEnum):
    ASSIGNED = "ASSIGNED"
    RELEASED = "RELEASED"


@dataclass(frozen=True, slots=True)
class CreateQueue:
    code: str
    name: str


@dataclass(frozen=True, slots=True)
class CreateRoutingRule:
    queue_id: UUID
    channel_code: str
    priority: int


@dataclass(frozen=True, slots=True)
class SetAgentPresence:
    agent_reference: str
    state: PresenceState
    assignment_capacity: int
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class AssignConversation:
    conversation_reference: str
    queue_id: UUID
    agent_reference: str
    assigned_at: datetime


__all__ = [
    "AssignConversation",
    "AssignmentStatus",
    "Conflict",
    "CreateQueue",
    "CreateRoutingRule",
    "InboxOperationsError",
    "PresenceState",
    "SetAgentPresence",
]
