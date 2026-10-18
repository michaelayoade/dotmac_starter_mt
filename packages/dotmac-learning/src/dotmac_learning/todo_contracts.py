"""Detached source timeline facts; products own presentation and time zones."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from .delivery_contracts import PersonSession
from .references import ActivityFact


@dataclass(frozen=True, slots=True)
class DeadlineFact:
    due_at: datetime
    activity: ActivityFact
    course_title: str
    course_slug: str


@dataclass(frozen=True, slots=True)
class RecentGradeFact:
    score_id: UUID
    created_at: datetime
    score: float
    max_score: float
    fraction: float
    passed: bool
    activity: ActivityFact
    course_title: str
    course_slug: str
    feedback_available: bool


@dataclass(frozen=True, slots=True)
class CourseWindowFact:
    kind: Literal["opens", "closes"]
    at: datetime
    course_title: str


@dataclass(frozen=True, slots=True)
class PersonTimeline:
    overdue: tuple[DeadlineFact, ...]
    due_today: tuple[DeadlineFact, ...]
    next_7: tuple[DeadlineFact, ...]
    next_30: tuple[DeadlineFact, ...]
    sessions: tuple[PersonSession, ...]
    joinable: frozenset[UUID]
    graded: tuple[RecentGradeFact, ...]
    feedback: tuple[RecentGradeFact, ...]
    course_windows: tuple[CourseWindowFact, ...]
