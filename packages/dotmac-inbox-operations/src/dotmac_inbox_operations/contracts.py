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


class QueueEntryStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    PROMOTED = "PROMOTED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class AdmitToQueue:
    """Admit one conversation to the back of a queue.

    `conversation_reference` is opaque: this module owns the ORDER, never the
    conversation. Admission is idempotent on that reference, because a retried
    inbound webhook must not take two places in the line.
    """

    queue_id: UUID
    conversation_reference: str
    entered_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PromoteFromQueue:
    queue_id: UUID
    eligible_agent_references: tuple[str, ...]
    presence_fresh_after: datetime
    promoted_at: datetime | None = None


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
    eligible_agent_references: tuple[str, ...]
    presence_fresh_after: datetime


@dataclass(frozen=True, slots=True)
class ReleaseConversation:
    assignment_id: UUID
    released_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class RouteConversation:
    """Resolve and durably record one routing decision.

    The reference belongs to the ingress adapter and makes delivery replay
    idempotent. The channel is a provider-neutral Inbox vocabulary member.
    """

    decision_reference: str
    conversation_reference: str
    channel_code: str
    routed_at: datetime


@dataclass(frozen=True, slots=True)
class RoutedConversation:
    decision_id: UUID
    rule_id: UUID
    queue_id: UUID
    queue_entry_id: UUID
    queue_position: int


@dataclass(frozen=True, slots=True)
class QueueEligibility:
    """A Workforce/product eligibility projection for one dispatch attempt."""

    queue_id: UUID
    agent_references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DispatchQueues:
    """Attempt one FIFO promotion per queue so a blocked queue cannot starve peers."""

    queues: tuple[QueueEligibility, ...]
    dispatched_at: datetime
    presence_fresh_after: datetime


__all__ = [
    "AdmitToQueue",
    "AssignConversation",
    "AssignmentStatus",
    "Conflict",
    "CreateQueue",
    "CreateRoutingRule",
    "DispatchQueues",
    "InboxOperationsError",
    "PresenceState",
    "PromoteFromQueue",
    "QueueEligibility",
    "QueueEntryStatus",
    "ReleaseConversation",
    "RouteConversation",
    "RoutedConversation",
    "SetAgentPresence",
]
