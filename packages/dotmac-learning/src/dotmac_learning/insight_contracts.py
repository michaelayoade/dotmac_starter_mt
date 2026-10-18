"""Frozen insight facts and semantic codes; Academy owns text and rendering."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from .event_contracts import WeekBucket
from .reports_contracts import ReportCohort


@dataclass(frozen=True, slots=True)
class GradeTrend:
    points: tuple[int, ...]
    direction: Literal["flat", "up", "down"]


@dataclass(frozen=True, slots=True)
class DisciplineScore:
    discipline: str
    pct: int


@dataclass(frozen=True, slots=True)
class DisciplineStrengths:
    strong: str | None
    weak: str | None
    all: tuple[DisciplineScore, ...]


type BlockerReason = Literal["overdue", "attempts_exhausted"]
type Recommendation = (
    BlockerReason
    | Literal["start_first_chapter", "resume_after_week", "continue_unfinished"]
)


@dataclass(frozen=True, slots=True)
class CurrentBlocker:
    activity_id: UUID
    title: str
    course_id: UUID
    reason: BlockerReason
    due_at: datetime | None


@dataclass(frozen=True, slots=True)
class LearnerActivity:
    weekly: tuple[WeekBucket, ...]
    max_week: int
    velocity_per_week: float
    grade_trend: GradeTrend
    days_since_last: int | None
    disciplines: DisciplineStrengths
    attendance: None
    blocker: CurrentBlocker | None
    recommendation: Recommendation


@dataclass(frozen=True, slots=True)
class CohortFunnel:
    enrolled: int
    started: int
    half: int
    completed: int
    certified: int


@dataclass(frozen=True, slots=True)
class LabFailures:
    course_id: UUID | None
    course_title: str | None
    failed_checks: int


@dataclass(frozen=True, slots=True)
class CohortOverview:
    learners: int
    active: int
    inactive: int
    active_window_days: int
    submitted: int
    overdue: int
    not_attempted: int
    avg_score_pct: int | None
    funnel: CohortFunnel
    lab_failures: tuple[LabFailures, ...]
    week_events: int
    prior_week_avg: float


@dataclass(frozen=True, slots=True)
class CohortComparison:
    cohort: ReportCohort
    learners: int
    active: int
    completed: int
    completion_pct: int
