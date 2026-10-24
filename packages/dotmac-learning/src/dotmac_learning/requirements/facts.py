"""Learner facts and evaluation target: the only inputs conditions may read.

The engine never queries anything. A facts loader (a later slice) builds a
``LearnerFacts`` per person; a missing entry means "unknown", and every
condition treats unknown as not satisfied (fail closed).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

TargetKind = Literal["course_access", "activity_attempt"]


@dataclass(frozen=True)
class Target:
    kind: TargetKind
    id: UUID


@dataclass(frozen=True)
class Route:
    """One way a learner reaches a course.

    ``track_id is None`` is a legacy cohort-wide route (always open).
    ``predecessor_course_id is None`` is the first course in its track.
    """

    track_id: UUID | None
    predecessor_course_id: UUID | None


@dataclass(frozen=True)
class LearnerFacts:
    tenant_id: UUID
    person_id: UUID
    now: datetime
    completed_course_ids: frozenset[UUID] = frozenset()
    certificate_course_ids: frozenset[UUID] = frozenset()
    best_fraction_by_activity: Mapping[UUID, float] = field(default_factory=dict)
    course_final_fraction: Mapping[UUID, float] = field(default_factory=dict)
    course_grade_fraction: Mapping[UUID, float] = field(default_factory=dict)
    attempts_by_activity: Mapping[UUID, int] = field(default_factory=dict)
    # Earliest ACTIVE enrollment that entitles each target; absent = unknown.
    enrolled_at_by_target: Mapping[tuple[TargetKind, UUID], datetime] = field(
        default_factory=dict
    )
    # (target kind, target id, node path) -> approval row id (unrevoked, unexpired).
    approvals: Mapping[tuple[str, UUID, str], str] = field(default_factory=dict)
    routes_by_course: Mapping[UUID, tuple[Route, ...]] = field(default_factory=dict)
    product_facts: Mapping[str, object] = field(default_factory=dict)
