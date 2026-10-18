"""Frozen typed weighted-grade facts, without presentation dictionaries."""

from dataclasses import dataclass
from uuid import UUID

from .references import ActivityFact
from .reports_contracts import ReportCohort, ReportPerson


@dataclass(frozen=True, slots=True)
class ActivityGrade:
    activity: ActivityFact
    fraction: float
    weight: float
    graded: bool


@dataclass(frozen=True, slots=True)
class CourseGrade:
    pct: int
    per_activity: tuple[ActivityGrade, ...]


@dataclass(frozen=True, slots=True)
class AttemptedGrade:
    pct: int
    graded_count: int
    graded_weight_fraction: float


@dataclass(frozen=True, slots=True)
class GradebookCell:
    activity_id: UUID
    pct: int


@dataclass(frozen=True, slots=True)
class GradebookRow:
    person: ReportPerson
    email: str | None
    cells: tuple[GradebookCell, ...]
    final_pct: int


@dataclass(frozen=True, slots=True)
class CohortGradebook:
    cohort: ReportCohort
    activities: tuple[ActivityFact, ...]
    rows: tuple[GradebookRow, ...]
