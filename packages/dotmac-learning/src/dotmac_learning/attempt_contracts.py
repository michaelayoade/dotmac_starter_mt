"""Concrete sitting commands, detached facts and trusted audit attribution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal, NotRequired, get_args
from uuid import UUID

from pydantic import BaseModel, ConfigDict, JsonValue

from .assessment_contracts import GradingQuestion
from .event_contracts import EventEmission
from .references import ActivityFact
from .result_contracts import SubmissionFact


class AttemptCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SelectionQuestion(AttemptCommand):
    """Concrete input satisfying the public exam engine's Question protocol."""

    ext_id: str
    category: str | None = None
    options: JsonValue = None


class OpenAttemptCommand(AttemptCommand):
    tenant_id: UUID
    person_id: UUID
    activity_id: UUID
    all_ext_ids: list[str]
    count: int
    now: datetime | None = None
    questions: tuple[SelectionQuestion, ...] | None = None


class CloseAttemptCommand(AttemptCommand):
    tenant_id: UUID
    person_id: UUID
    activity_id: UUID
    now: datetime | None = None


class GrantExtraAttemptCommand(AttemptCommand):
    tenant_id: UUID
    person_id: UUID
    activity_id: UUID
    reason: str
    granted_by: UUID | None = None
    extra_attempts: int = 1


# The Kernel owns this closed taxonomy. Derive the runtime guard from the type
# so the constructor and typed audit callers cannot accept different vocabularies.
AttemptActorType = Literal["system", "user", "api_key", "service"]
_ATTEMPT_ACTOR_TYPES: Final[frozenset[str]] = frozenset(get_args(AttemptActorType))


@dataclass(frozen=True, slots=True)
class AttemptAuditContext:
    """Trusted product attribution, never inferred from grant client fields."""

    tenant_id: UUID
    actor_type: AttemptActorType
    source_application: str
    actor_id: str | None = None
    actor_label: str | None = None

    def __post_init__(self) -> None:
        if self.actor_type not in _ATTEMPT_ACTOR_TYPES:
            raise ValueError(
                "attempt audit actor_type must be "
                + ", ".join(sorted(_ATTEMPT_ACTOR_TYPES))
            )
        # Actor IDs remain the canonical Kernel writer's responsibility. Keeping
        # that validation there preserves the operation's existing error order.


@dataclass(frozen=True, slots=True)
class AttemptFact:
    id: UUID
    tenant_id: UUID
    activity_id: UUID
    person_id: UUID
    question_ext_ids: JsonValue
    started_at: datetime
    submitted_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AttemptWrite:
    attempt: AttemptFact
    event: EventEmission | None = None


@dataclass(frozen=True, slots=True)
class AttemptGrantFact:
    id: UUID
    tenant_id: UUID
    activity_id: UUID
    person_id: UUID
    extra_attempts: int
    reason: str
    granted_by: UUID | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PendingGradingFact:
    submission: SubmissionFact
    activity: ActivityFact
    person_email: str | None


class AssessmentQuestion(GradingQuestion):
    explanation: NotRequired[str]


__all__ = [
    "AttemptCommand",
    "SelectionQuestion",
    "OpenAttemptCommand",
    "CloseAttemptCommand",
    "GrantExtraAttemptCommand",
    "AttemptAuditContext",
    "AttemptFact",
    "AttemptWrite",
    "AttemptGrantFact",
    "PendingGradingFact",
    "AssessmentQuestion",
    "QuestionFact",
]


@dataclass(frozen=True, slots=True)
class QuestionFact:
    """Detached stored question; answer-key disclosure belongs to the caller.

    JSON fields retain storage shapes without authoring validation. Frozen
    fields contain independent deep-copied JSON, not recursively immutable JSON.
    """

    id: UUID
    tenant_id: UUID
    bank_id: UUID
    ext_id: str
    stem: str
    type: str
    options: JsonValue
    correct: JsonValue
    rubric_category: str
    category: str | None
    explanation: str
    weight: int
    created_at: datetime
    updated_at: datetime
