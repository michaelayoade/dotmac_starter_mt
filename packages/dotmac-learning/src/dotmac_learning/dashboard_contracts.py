"""Detached learner dashboard facts and semantic navigation decisions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from .curriculum_contracts import ChapterFact
from .delivery_contracts import ClassSessionFact
from .progress_contracts import ScoreFact
from .references import ActivityFact, CourseFact

CardState = Literal["in_progress", "upcoming", "completed", "expired", "locked"]
CertificateStatus = Literal["issued", "eligible"]
Trend = Literal["flat", "up", "down"]


@dataclass(frozen=True, slots=True)
class DashboardAction:
    code: Literal["start", "resume", "review_feedback"]
    course_id: UUID
    chapter_number: int | None = None


@dataclass(frozen=True, slots=True)
class LastRead:
    number: int
    title: str


@dataclass(frozen=True, slots=True)
class ContinueTarget:
    course: CourseFact
    chapter: ChapterFact


@dataclass(frozen=True, slots=True)
class CourseCard:
    course: CourseFact
    state: CardState
    pct: int
    passed: int
    total: int
    grade_pct: int
    final_threshold_pct: int
    next_activity: ActivityFact | None
    next_deadline: datetime | None
    starts_at: datetime | None
    last_read: LastRead | None
    next_session: ClassSessionFact | None
    certificate_status: CertificateStatus | None
    locked_reason: str | None
    action: DashboardAction | None


@dataclass(frozen=True, slots=True)
class StateCount:
    state: CardState
    count: int


@dataclass(frozen=True, slots=True)
class CourseCards:
    cards: tuple[CourseCard, ...]
    counts: tuple[StateCount, ...]
    filters: tuple[CardState, ...]


@dataclass(frozen=True, slots=True)
class LearnerHome:
    cards: tuple[CourseCard, ...]
    counts: tuple[StateCount, ...]
    filters: tuple[CardState, ...]
    continue_to: ContinueTarget | None


@dataclass(frozen=True, slots=True)
class ProgressActivity:
    activity: ActivityFact
    chapter_number: int | None
    score: ScoreFact | None
    pct: int | None
    passed: bool
    threshold_pct: int
    attempts_used: int
    max_attempts: int | None
    attempts_left: int | None
    attempts_granted: int
    feedback_available: bool
    due_at: datetime | None
    overdue: bool


@dataclass(frozen=True, slots=True)
class ProgressCertificate:
    status: CertificateStatus | None
    serial: str | None
    target_course_id: UUID | None


@dataclass(frozen=True, slots=True)
class CourseProgress:
    course: CourseFact
    grade_pct: int
    completion_pct: int
    final_threshold_pct: int
    rows: tuple[ProgressActivity, ...]
    upcoming: tuple[ProgressActivity, ...]
    overdue: tuple[ProgressActivity, ...]
    trend_points: tuple[int, ...]
    trend: Trend
    certificate: ProgressCertificate
