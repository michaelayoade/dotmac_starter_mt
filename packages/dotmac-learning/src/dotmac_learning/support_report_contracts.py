"""Detached support-report projections; products own labels and serialization."""

from dataclasses import dataclass
from datetime import datetime

from pydantic import JsonValue

from .reports_contracts import ReportCohort, ReportPerson


@dataclass(frozen=True, slots=True)
class EngagementSnapshot:
    enrollees: int
    ever_active: int
    active_in_window: int
    never_started: int


@dataclass(frozen=True, slots=True)
class TopMover:
    person: ReportPerson
    cohort_name: str
    events: int
    submissions: int
    avg_score: float | None


@dataclass(frozen=True, slots=True)
class ActivityWindowCounts:
    since: datetime
    new_enrollments: int
    submissions: int
    completions: int


@dataclass(frozen=True, slots=True)
class AttentionReasonCount:
    reason: str
    count: int


@dataclass(frozen=True, slots=True)
class AttentionLearner:
    person: ReportPerson
    cohort_name: str
    reason: str
    severity: str
    days_inactive: JsonValue
    never_active: bool


@dataclass(frozen=True, slots=True)
class AttentionSnapshot:
    open_total: int
    by_reason: tuple[AttentionReasonCount, ...]
    never_started: int
    top: tuple[AttentionLearner, ...]


@dataclass(frozen=True, slots=True)
class RosterProgressRow:
    person: ReportPerson
    courses: int
    avg_completion_pct: int
    avg_grade_pct: int
    last_activity_at: datetime | None
    days_inactive: int | None
    open_queue_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CohortRosterProgress:
    cohort: ReportCohort
    rows: tuple[RosterProgressRow, ...]


__all__ = [
    "EngagementSnapshot",
    "TopMover",
    "ActivityWindowCounts",
    "AttentionReasonCount",
    "AttentionLearner",
    "AttentionSnapshot",
    "RosterProgressRow",
    "CohortRosterProgress",
]
