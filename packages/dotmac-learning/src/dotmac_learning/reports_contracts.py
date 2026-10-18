"""Detached immutable reporting facts; product adapters own all rendering."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from .references import ActivityFact


class ReportNotFound(ValueError):
    """Missing tenant-scoped person, preserving source error detail."""


@dataclass(frozen=True, slots=True)
class ReportPerson:
    id: UUID
    tenant_id: UUID
    first_name: str
    last_name: str
    email: str | None


@dataclass(frozen=True, slots=True)
class ReportCohort:
    id: UUID
    tenant_id: UUID
    name: str
    discipline: str
    status: str
    delivery_mode: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReportScore:
    """Scalar ledger columns used in reports; item payloads retain their owner."""

    id: UUID
    tenant_id: UUID
    submission_id: UUID
    score: float
    max_score: float
    fraction: float
    passed: bool
    source: str
    override_reason: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MatrixCell:
    activity_id: UUID
    score: ReportScore | None


@dataclass(frozen=True, slots=True)
class MatrixRow:
    person_id: UUID
    first_name: str
    last_name: str
    email: str | None
    cells: tuple[MatrixCell, ...]
    completion: float


@dataclass(frozen=True, slots=True)
class CohortMatrix:
    cohort: ReportCohort
    activities: tuple[ActivityFact, ...]
    rows: tuple[MatrixRow, ...]


@dataclass(frozen=True, slots=True)
class TranscriptRow:
    activity: ActivityFact
    score: ReportScore
    fraction: float
    passed: bool
    graded_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class StudentTranscript:
    person: ReportPerson
    rows: tuple[TranscriptRow, ...]
