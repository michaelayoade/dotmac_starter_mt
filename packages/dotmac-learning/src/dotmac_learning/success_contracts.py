"""Detached Success Queue facts and required product delivery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import JsonValue

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class SuccessBadRequest(ValueError):
    """Invalid source queue operation."""


class SuccessNotFound(LookupError):
    """The scoped queue entry does not exist."""


@dataclass(frozen=True, slots=True)
class SuccessTerms:
    """Academy supplies source-coerced setting values; no domain clamps."""

    inactive_days: int = 7
    min_grade_pct: int = 60
    certificate_grace_hours: int = 6


@dataclass(frozen=True, slots=True)
class QueueFact:
    id: UUID
    tenant_id: UUID
    person_id: UUID
    cohort_id: UUID | None
    reason_kind: str
    supporting_facts: JsonValue
    severity: str
    detected_at: datetime
    assigned_to: UUID | None
    recommended_action: str
    status: str
    resolved_at: datetime | None
    resolved_by: UUID | None
    score_hint: float
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SuccessPersonFact:
    id: UUID
    tenant_id: UUID
    first_name: str
    last_name: str
    email: str | None


@dataclass(frozen=True, slots=True)
class QueueListFact:
    entry: QueueFact
    person: SuccessPersonFact
    cohort_name: str


@dataclass(frozen=True, slots=True)
class SegmentMessageFact:
    tenant_id: UUID
    cohort_id: UUID
    segment: str
    subject: str
    body: str
    notification_kind: str = "instructor_message"
    email_kind: str = "segment_message"
    idempotency_prefix: str = "segmsg"


@dataclass(frozen=True, slots=True)
class SweepResult:
    opened: int
    refreshed: int
    auto_resolved: int


@dataclass(frozen=True, slots=True)
class SegmentMessageResult:
    recipients: int
    action_id: str


class SegmentDeliveryProtocol(Protocol):
    """Product delivers notify then email on this exact caller Session."""

    def __call__(
        self,
        db: Session,
        recipient_fact: SuccessPersonFact,
        message_fact: SegmentMessageFact,
        action_id: str,
    ) -> None: ...


__all__ = [
    "SuccessBadRequest",
    "SuccessNotFound",
    "SuccessTerms",
    "QueueFact",
    "SweepResult",
    "SuccessPersonFact",
    "QueueListFact",
    "SegmentMessageFact",
    "SegmentMessageResult",
    "SegmentDeliveryProtocol",
]
