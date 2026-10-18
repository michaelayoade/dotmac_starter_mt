"""Frozen support-read facts; product owns notifications, snippets and URLs."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from .curriculum_contracts import ChapterFact
from .references import CourseFact
from .reports_contracts import ReportCohort, ReportPerson


@dataclass(frozen=True, slots=True)
class LegacyCohortStudent:
    person: ReportPerson
    completion_pct: float
    last_activity_at: datetime | None
    at_risk: bool


@dataclass(frozen=True, slots=True)
class LegacyCohortOverview:
    cohort: ReportCohort
    rows: tuple[LegacyCohortStudent, ...]


type CourseRiskReason = Literal["overdue", "behind"]


@dataclass(frozen=True, slots=True)
class AtRiskCourse:
    course_id: UUID
    title: str | None
    reason: CourseRiskReason
    pct: float
    expected: float


@dataclass(frozen=True, slots=True)
class SearchAuthority:
    """Trusted Academy/Kernel staff observation, not client-supplied input."""

    tenant_id: UUID
    person_id: UUID
    is_staff: bool


@dataclass(frozen=True, slots=True)
class SearchChapter:
    chapter: ChapterFact
    course_slug: str


@dataclass(frozen=True, slots=True)
class SearchResults:
    term: str
    courses: tuple[CourseFact, ...]
    chapters: tuple[SearchChapter, ...]
