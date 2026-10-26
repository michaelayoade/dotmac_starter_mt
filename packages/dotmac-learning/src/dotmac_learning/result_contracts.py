"""Result commands. Product grading and entitlement decisions keep their owners."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from .event_contracts import EventEmission

if TYPE_CHECKING:
    from .progress_contracts import CompletionResult, ScoreFact


class Command(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GradedResult(Command):
    """Validated grading values; ledger provenance belongs to the writer."""

    score: float
    max_score: float
    fraction: float
    passed: bool
    per_item: list[dict[str, JsonValue]] = Field(default_factory=list)


class SubmitMCQCommand(Command):
    """MCQ inputs; admission, question reads and grading keep their domain owners.

    Answers retain ordinary JSON values, including malformed grading inputs.
    The optional selection is a list of string external question IDs.
    """

    tenant_id: UUID
    person_id: UUID
    activity_id: UUID
    answers: dict[str, JsonValue] = Field(default_factory=dict)
    only_ext_ids: list[str] | None = None
    missing_question_weight: int = Field(strict=True, gt=0)


class MCQResultCommand(Command):
    tenant_id: UUID
    person_id: UUID
    activity_id: UUID
    answers: dict[str, JsonValue] = Field(default_factory=dict)
    grading: GradedResult | None = None


class LabResultCommand(Command):
    """Producer must validate and retain its lab operation/instance authority.

    These IDs describe that already-authorized operation, they do not prove it.
    The producer owns live checks, instance status, leases and last_active_at.
    No learner admission lock or new identity UPDATE grant is needed here.
    """

    tenant_id: UUID
    person_id: UUID
    activity_id: UUID
    instance_id: UUID
    seed: dict[str, JsonValue]
    grading: GradedResult


class LabResultRead(Command):
    """Selectors for a caller-authorized instance and actual check worker window.

    Academy owns instance/course access, operation eligibility and reset/deploy
    selection. IDs and inclusive claimed/finished bounds describe that selected
    operation; they confer no authority. Missing or inverted bounds match nothing.
    """

    tenant_id: UUID
    person_id: UUID
    activity_id: UUID
    instance_id: UUID
    claimed_at: datetime | None
    finished_at: datetime | None


class OverrideScoreCommand(Command):
    tenant_id: UUID
    submission_id: UUID
    score_value: float
    max_score: float
    reason: str | None


@dataclass(frozen=True, slots=True)
class AdmissionObservation:
    tenant_id: UUID
    person_id: UUID
    activity_id: UUID
    course_id: UUID
    max_attempts: int | None
    used: int
    activity_type: str
    grading: str
    pass_threshold: float
    assessment_mode: str
    bank_id: UUID | None
    question_count: int | None
    min_attempt_interval_seconds: int
    last_attempt_at: datetime | None


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    allowed: bool


class AttemptAdmissionPolicy(Protocol):
    """Trusted adapter bound to the exact caller Session and observed IDs.

    Only this immutable observation crosses the interface. This is an
    architecture boundary, not a Python sandbox for arbitrary callback code.
    """

    def evaluate(self, observation: AdmissionObservation) -> AdmissionDecision: ...


@dataclass(frozen=True, slots=True)
class SubmissionFact:
    id: UUID
    tenant_id: UUID
    activity_id: UUID
    person_id: UUID
    answers: dict[str, JsonValue]
    attempt_no: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ResultWrite:
    submission: SubmissionFact
    score: ScoreFact | None
    completion: CompletionResult | None
    events: tuple[EventEmission, ...]


class AdmissionRefused(ValueError):
    pass


class AdmissionContention(AdmissionRefused):
    """Profile NOWAIT conflict. Caller must roll back its root; never retry here."""


class ResultBadRequest(ValueError):
    pass


class TransactionChanged(RuntimeError):
    pass
