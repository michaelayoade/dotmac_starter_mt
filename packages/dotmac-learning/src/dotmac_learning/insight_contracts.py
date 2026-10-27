"""Frozen insight facts and semantic codes; Academy owns text and rendering."""

from dataclasses import dataclass
from datetime import datetime
from math import isfinite
from typing import Literal, TypeAlias
from uuid import UUID

from .event_contracts import WeekBucket
from .reports_contracts import ReportCohort


def _integer(name: str, value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _fraction(name: str, value: float) -> None:
    if type(value) not in (int, float) or not isfinite(value) or not 0 <= value <= 1:
        raise ValueError(f"{name} must be a finite fraction in [0, 1]")


@dataclass(frozen=True, slots=True)
class GradeTrendTerms:
    sample_limit: int
    min_samples: int
    min_delta_fraction: float

    def __post_init__(self) -> None:
        _integer("sample_limit", self.sample_limit, 2)
        _integer("min_samples", self.min_samples, 2)
        if self.sample_limit < self.min_samples:
            raise ValueError("sample_limit must be >= min_samples")
        _fraction("min_delta_fraction", self.min_delta_fraction)


@dataclass(frozen=True, slots=True)
class ProgressTrendTerms:
    sample_limit: int
    min_samples: int
    min_delta_fraction: float

    def __post_init__(self) -> None:
        _integer("sample_limit", self.sample_limit, 2)
        _integer("min_samples", self.min_samples, 2)
        if self.sample_limit < self.min_samples:
            raise ValueError("sample_limit must be >= min_samples")
        _fraction("min_delta_fraction", self.min_delta_fraction)


@dataclass(frozen=True, slots=True)
class LearnerInsightTerms:
    chart_weeks: int
    velocity_weeks: int
    resume_after_days: int
    grade_trend: GradeTrendTerms

    def __post_init__(self) -> None:
        for name in ("chart_weeks", "velocity_weeks", "resume_after_days"):
            _integer(name, getattr(self, name))
        if not isinstance(self.grade_trend, GradeTrendTerms):
            raise ValueError("grade_trend must be GradeTrendTerms")


@dataclass(frozen=True, slots=True)
class CohortInsightTerms:
    active_window_days: int
    comparison_prior_weeks: int
    half_fraction: float

    def __post_init__(self) -> None:
        _integer("active_window_days", self.active_window_days)
        _integer("comparison_prior_weeks", self.comparison_prior_weeks, 1)
        _fraction("half_fraction", self.half_fraction)


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


BlockerReason: TypeAlias = Literal["overdue", "attempts_exhausted"]  # noqa: UP040 - Python 3.11 floor
Recommendation: TypeAlias = (  # noqa: UP040 - Python 3.11 floor
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
