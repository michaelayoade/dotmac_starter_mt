"""Source-faithful best-result projections, without attached ORM results."""

from __future__ import annotations

from collections.abc import Sequence
from copy import deepcopy
from datetime import datetime
from typing import cast
from uuid import UUID

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Activity, Score, Submission
from .progress_contracts import ScoreFact


def score_fact(score: Score) -> ScoreFact:
    return ScoreFact(
        id=score.id,
        tenant_id=score.tenant_id,
        submission_id=score.submission_id,
        score=score.score,
        max_score=score.max_score,
        fraction=score.fraction,
        passed=score.passed,
        per_item=cast(JsonValue, deepcopy(score.per_item)),
        source=score.source,
        override_reason=score.override_reason,
        created_at=score.created_at,
        updated_at=score.updated_at,
    )


def best_rank(score: ScoreFact) -> tuple[float, bool, bool, datetime]:
    """Fraction, pass, manual override, recency; equal ranks retain first row."""
    return (score.fraction, score.passed, score.source == "override", score.created_at)


def best_scores_for(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> dict[UUID, ScoreFact]:
    return best_scores_for_person(
        db, tenant_id=tenant_id, person_id=person_id, course_ids=[course_id]
    )


def best_scores_for_person(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_ids: Sequence[UUID] | None = None,
) -> dict[UUID, ScoreFact]:
    if course_ids is not None and not course_ids:
        return {}
    query = (
        select(Activity.id, Score)
        .join(
            Submission,
            (Submission.activity_id == Activity.id)
            & (Submission.tenant_id == Activity.tenant_id),
        )
        .join(
            Score,
            (Score.submission_id == Submission.id)
            & (Score.tenant_id == Submission.tenant_id),
        )
        .where(Activity.tenant_id == tenant_id, Submission.person_id == person_id)
        .order_by(Activity.id, Submission.attempt_no, Score.created_at)
    )
    if course_ids is not None:
        query = query.where(Activity.course_id.in_(course_ids))
    best: dict[UUID, ScoreFact] = {}
    for activity_id, score in db.execute(query).all():
        fact = score_fact(score)
        current = best.get(activity_id)
        if current is None or best_rank(fact) > best_rank(current):
            best[activity_id] = fact
    return best


def best_scores_by_course_for_person(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_ids: Sequence[UUID] | None = None,
) -> dict[UUID, dict[UUID, ScoreFact]]:
    if course_ids is not None and not course_ids:
        return {}
    query = (
        select(Activity.course_id, Activity.id, Score)
        .join(
            Submission,
            (Submission.activity_id == Activity.id)
            & (Submission.tenant_id == Activity.tenant_id),
        )
        .join(
            Score,
            (Score.submission_id == Submission.id)
            & (Score.tenant_id == Submission.tenant_id),
        )
        .where(Activity.tenant_id == tenant_id, Submission.person_id == person_id)
        .order_by(Activity.id, Submission.attempt_no, Score.created_at)
    )
    if course_ids is not None:
        query = query.where(Activity.course_id.in_(course_ids))
    out: dict[UUID, dict[UUID, ScoreFact]] = {}
    for course_id, activity_id, score in db.execute(query).all():
        per_course = out.setdefault(course_id, {})
        fact = score_fact(score)
        current = per_course.get(activity_id)
        if current is None or best_rank(fact) > best_rank(current):
            per_course[activity_id] = fact
    return out
