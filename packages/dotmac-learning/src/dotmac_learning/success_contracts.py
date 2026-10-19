"""Detached Success Queue facts and required product delivery boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
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
    """Required, validated intervention policy supplied by the product."""

    inactive_days: int
    min_grade_pct: int
    certificate_grace_hours: int
    inactivity_high_factor: float
    overdue_high_count: int
    overdue_high_days: int
    overdue_title_limit: int
    below_passing_min_graded: int
    below_passing_min_weight_fraction: float
    below_passing_high_grade_factor: float
    almost_complete_fraction: float

    def __post_init__(self) -> None:
        for name in (
            "overdue_high_count",
            "overdue_high_days",
            "overdue_title_limit",
            "below_passing_min_graded",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        # These three established scalar inputs deliberately retain signed
        # values; the product owns their interpretation and supplies no clamps.
        for name in ("inactive_days", "min_grade_pct", "certificate_grace_hours"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} must be an integer")
        factor = self.inactivity_high_factor
        if type(factor) not in (int, float) or not isfinite(factor) or factor < 1:
            raise ValueError("inactivity_high_factor must be finite and >= 1")
        for name in (
            "below_passing_min_weight_fraction",
            "below_passing_high_grade_factor",
            "almost_complete_fraction",
        ):
            value = getattr(self, name)
            if (
                type(value) not in (int, float)
                or not isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError(f"{name} must be a finite fraction in [0, 1]")


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


@dataclass(frozen=True, slots=True)
class SuccessTransitionEffect:
    """Observed transition and its actual audit row in the caller transaction."""

    entry: QueueFact
    audit_event_id: UUID


@dataclass(frozen=True, slots=True)
class SegmentMessageEffect:
    """Observed delivery result and its actual caller-transaction audit row."""

    result: SegmentMessageResult
    audit_event_id: UUID


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
    "SuccessTransitionEffect",
    "SegmentMessageEffect",
    "SegmentDeliveryProtocol",
]
