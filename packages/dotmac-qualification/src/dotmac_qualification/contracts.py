"""Qualification commands and outcomes."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


class QualificationError(Exception):
    """Base refusal."""


class Conflict(QualificationError):
    """The case/evidence/decision state is inadmissible."""


class DecisionOutcome(enum.StrEnum):
    ELIGIBLE = "ELIGIBLE"
    INELIGIBLE = "INELIGIBLE"
    MANUAL_REVIEW = "MANUAL_REVIEW"


@dataclass(frozen=True, slots=True)
class OpenQualification:
    subject_reference: str
    specification_reference: str


@dataclass(frozen=True, slots=True)
class RecordEvidence:
    case_id: UUID
    source_type: str
    observed_at: datetime
    valid_until: datetime
    facts: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RecordDecision:
    case_id: UUID
    outcome: DecisionOutcome
    decided_at: datetime
    expires_at: datetime
    rationale: str


__all__ = [
    "Conflict",
    "DecisionOutcome",
    "OpenQualification",
    "QualificationError",
    "RecordDecision",
    "RecordEvidence",
]
