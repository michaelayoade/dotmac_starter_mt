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


class WarrantRefusal(enum.StrEnum):
    """Why a decision cannot warrant a promotion. Closed and data-free."""

    NOT_FOUND = "NOT_FOUND"
    SUBJECT_MISMATCH = "SUBJECT_MISMATCH"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    NOT_YET_EFFECTIVE = "NOT_YET_EFFECTIVE"
    EXPIRED = "EXPIRED"


class WarrantRefused(Conflict):
    """The decision does not warrant the promotion asked of it."""

    def __init__(self, reason: WarrantRefusal) -> None:
        super().__init__(f"qualification warrant refused: {reason.value}")
        self.reason = reason


@dataclass(frozen=True, slots=True)
class DecisionRead:
    """The decision facts a verifier needs, and nothing else.

    It deliberately omits the rationale and every evidence fact, so a warrant
    check can never carry location, address or network data to its caller.
    """

    decision_id: UUID
    case_id: UUID
    subject_reference: str
    specification_reference: str
    outcome: DecisionOutcome
    decided_at: datetime
    expires_at: datetime


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
    "DecisionRead",
    "OpenQualification",
    "QualificationError",
    "RecordDecision",
    "RecordEvidence",
    "WarrantRefusal",
    "WarrantRefused",
]
