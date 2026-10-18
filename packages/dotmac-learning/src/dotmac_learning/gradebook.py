"""Source weighted grades; assessment ranking retains its existing owner."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .gradebook_contracts import (
    ActivityGrade,
    AttemptedGrade,
    CohortGradebook,
    CourseGrade,
    GradebookCell,
    GradebookRow,
)
from .models import Activity
from .progress_contracts import ScoreFact
from .projections import best_scores_for
from .references import ActivityFact, _activity_fact
from .reports import _best_for_person, _cohort, _cohort_activities, _students


def grade_from(
    activities: Sequence[ActivityFact], best: Mapping[UUID, ScoreFact]
) -> CourseGrade:
    if not activities:
        return CourseGrade(0, ())
    per_activity = tuple(
        ActivityGrade(
            a, best[a.id].fraction if a.id in best else 0.0, a.weight, a.id in best
        )
        for a in activities
    )
    total_weight = sum(a.weight for a in activities)
    if total_weight == 0:
        return CourseGrade(0, per_activity)
    weighted_sum = sum(
        (best[a.id].fraction if a.id in best else 0.0) * a.weight for a in activities
    )
    return CourseGrade(int(round(weighted_sum / total_weight * 100)), per_activity)


def course_grade(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> CourseGrade:
    activities = db.scalars(
        select(Activity)
        .where(Activity.tenant_id == tenant_id)
        .where(Activity.course_id == course_id)
        .order_by(Activity.chapter_number, Activity.type)
    ).all()
    best = best_scores_for(
        db, tenant_id=tenant_id, person_id=person_id, course_id=course_id
    )
    return grade_from(tuple(_activity_fact(a) for a in activities), best)


def attempted_from(grade: CourseGrade) -> AttemptedGrade:
    total_weight = sum(a.weight for a in grade.per_activity)
    graded = [a for a in grade.per_activity if a.graded]
    graded_weight = sum(a.weight for a in graded)
    if graded_weight == 0:
        return AttemptedGrade(0, len(graded), 0.0)
    weighted_sum = sum(a.fraction * a.weight for a in graded)
    return AttemptedGrade(
        int(round(weighted_sum / graded_weight * 100)),
        len(graded),
        (graded_weight / total_weight) if total_weight else 0.0,
    )


def attempted_grade(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> AttemptedGrade:
    return attempted_from(
        course_grade(db, tenant_id=tenant_id, person_id=person_id, course_id=course_id)
    )


def cohort_gradebook(
    db: Session, *, tenant_id: UUID, cohort_id: UUID
) -> CohortGradebook:
    cohort = _cohort(db, tenant_id, cohort_id)
    activities, course_ids = _cohort_activities(db, tenant_id, cohort_id)
    total_weight = sum(a.weight for a in activities)
    rows = []
    for p in _students(db, tenant_id, cohort_id):
        best = _best_for_person(db, tenant_id, p.id, course_ids)
        cells = tuple(
            GradebookCell(
                a.id, int(round((best[a.id].fraction if a.id in best else 0.0) * 100))
            )
            for a in activities
        )
        if total_weight > 0:
            weighted_sum = sum(
                (best[a.id].fraction if a.id in best else 0.0) * a.weight
                for a in activities
            )
            final_pct = int(round(weighted_sum / total_weight * 100))
        else:
            final_pct = 0
        rows.append(GradebookRow(p, p.email, cells, final_pct))
    return CohortGradebook(
        cohort, tuple(_activity_fact(a) for a in activities), tuple(rows)
    )
