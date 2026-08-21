"""Typed product-neutral Survey commands and values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final
from uuid import UUID

_QUESTION_KEY_PATTERN: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")
_PUBLIC_SLUG_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SurveyError(ValueError):
    """Base class for product-neutral Survey refusals."""


class InvalidSurveyDefinition(SurveyError):
    """A definition cannot be represented by the shared contract."""


class InvalidAnswer(SurveyError):
    """A submitted answer does not satisfy the active definition."""


class InvalidSurveyTransition(SurveyError):
    """The requested lifecycle edge is not allowed."""


class StaleSurveyState(SurveyError):
    """The caller's expected state no longer matches the stored state."""


class SurveyUnavailable(SurveyError):
    """The requested survey is absent or not eligible for the operation."""


class InvitationUnavailable(SurveyError):
    """The invitation is absent, expired, completed, or otherwise unusable."""


class SurveyConflict(SurveyError):
    """A durable Survey identity conflicts with the requested content."""


class QuestionType(StrEnum):
    RATING = "rating"
    NPS = "nps"
    MULTIPLE_CHOICE = "multiple_choice"
    FREE_TEXT = "free_text"


class SurveyStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    EXPIRED = "expired"


def _required_text(value: str, label: str, *, maximum: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise InvalidSurveyDefinition(f"{label} must not be blank")
    if len(cleaned) > maximum:
        raise InvalidSurveyDefinition(f"{label} cannot exceed {maximum} characters")
    return cleaned


def _optional_text(value: str | None, label: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > maximum:
        raise InvalidSurveyDefinition(f"{label} cannot exceed {maximum} characters")
    return cleaned


def _aware(value: datetime | None, label: str) -> datetime | None:
    if value is not None and value.utcoffset() is None:
        raise InvalidSurveyDefinition(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True)
class Question:
    key: str
    type: QuestionType
    label: str
    required: bool = True
    options: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        key = _required_text(self.key, "question key", maximum=80)
        if not _QUESTION_KEY_PATTERN.fullmatch(key):
            raise InvalidSurveyDefinition(
                "question key must start with a letter and contain only letters, "
                "numbers, hyphens, or underscores"
            )
        object.__setattr__(self, "key", key)
        object.__setattr__(
            self, "label", _required_text(self.label, "question label", maximum=500)
        )
        if self.type is not QuestionType.MULTIPLE_CHOICE:
            object.__setattr__(self, "options", ())
            return
        if not 2 <= len(self.options) <= 50:
            raise InvalidSurveyDefinition(
                "multiple-choice questions require 2 to 50 options"
            )
        cleaned: list[str] = []
        seen: set[str] = set()
        for option in self.options:
            value = _required_text(option, "choice option", maximum=200)
            duplicate_key = value.casefold()
            if duplicate_key in seen:
                raise InvalidSurveyDefinition("multiple-choice options must be unique")
            seen.add(duplicate_key)
            cleaned.append(value)
        object.__setattr__(self, "options", tuple(cleaned))


@dataclass(frozen=True)
class SurveyDefinition:
    name: str
    questions: tuple[Question, ...] = ()
    description: str | None = None
    public_slug: str | None = None
    thank_you_message: str | None = None
    expires_at: datetime | None = None
    created_by_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "questions", tuple(self.questions))
        object.__setattr__(
            self, "name", _required_text(self.name, "survey name", maximum=160)
        )
        object.__setattr__(
            self,
            "description",
            _optional_text(self.description, "description", maximum=10_000),
        )
        object.__setattr__(
            self,
            "thank_you_message",
            _optional_text(self.thank_you_message, "thank-you message", maximum=10_000),
        )
        slug = self.public_slug
        if slug is not None:
            slug = re.sub(r"[\s_]+", "-", slug.strip().lower()) or None
        if slug is not None and not _PUBLIC_SLUG_PATTERN.fullmatch(slug):
            raise InvalidSurveyDefinition(
                "public slug must use lowercase letters, numbers, and single hyphens"
            )
        object.__setattr__(self, "public_slug", slug)
        object.__setattr__(self, "expires_at", _aware(self.expires_at, "expires_at"))

        seen: set[str] = set()
        rating_count = 0
        nps_count = 0
        for question in self.questions:
            if question.key in seen:
                raise InvalidSurveyDefinition(
                    f'question key "{question.key}" is duplicated'
                )
            seen.add(question.key)
            rating_count += question.type is QuestionType.RATING
            nps_count += question.type is QuestionType.NPS
        if rating_count > 1 or nps_count > 1:
            raise InvalidSurveyDefinition(
                "a survey may define at most one aggregate rating and one NPS question"
            )


@dataclass(frozen=True)
class InvitationRequest:
    survey_id: UUID
    recipient_ref: str
    source_owner: str
    source_event_id: str
    subject_ref: str | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "recipient_ref",
            _required_text(self.recipient_ref, "recipient_ref", maximum=200),
        )
        object.__setattr__(
            self,
            "source_owner",
            _required_text(self.source_owner, "source_owner", maximum=120),
        )
        object.__setattr__(
            self,
            "source_event_id",
            _required_text(self.source_event_id, "source_event_id", maximum=200),
        )
        object.__setattr__(
            self,
            "subject_ref",
            _optional_text(self.subject_ref, "subject_ref", maximum=200),
        )
        object.__setattr__(self, "expires_at", _aware(self.expires_at, "expires_at"))


@dataclass(frozen=True)
class Answer:
    key: str
    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", self.key.strip())
        object.__setattr__(self, "value", self.value.strip())
        if not self.key:
            raise InvalidAnswer("answer key must not be blank")


@dataclass(frozen=True)
class ResponseSubmission:
    answers: tuple[Answer, ...]
    submitted_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "answers", tuple(self.answers))
        if self.submitted_at.utcoffset() is None:
            raise InvalidAnswer("submitted_at must be timezone-aware")


@dataclass(frozen=True)
class ValidatedResponse:
    answers: dict[str, str]
    rating: int | None
    nps_value: int | None


@dataclass(frozen=True)
class SurveyMetrics:
    total_responses: int
    avg_rating: Decimal | None
    nps_score: Decimal | None


__all__ = [
    "Answer",
    "InvalidAnswer",
    "InvalidSurveyDefinition",
    "InvalidSurveyTransition",
    "InvitationRequest",
    "InvitationStatus",
    "InvitationUnavailable",
    "Question",
    "QuestionType",
    "ResponseSubmission",
    "StaleSurveyState",
    "SurveyConflict",
    "SurveyDefinition",
    "SurveyError",
    "SurveyMetrics",
    "SurveyStatus",
    "SurveyUnavailable",
    "ValidatedResponse",
]
