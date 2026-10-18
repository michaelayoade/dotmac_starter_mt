"""Source digest counters, without account/recipient or delivery ownership.

HR assembly must perform its original credential/session checks BEFORE invoking
staff_learning_window. Reuse audience.staff_person_ids/counts_by_audience for
classification. Independent reads do not qualify a coupled recipient cutover:
a same-Session set is not the original READ COMMITTED statement snapshot.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .digest_contracts import LearnerWindowActivity, StaffLearningWindow
from .models import (
    KIND_CHAPTER_VIEWED,
    CourseCompletion,
    LearningEvent,
    Score,
    Submission,
)


def staff_learning_window(
    db: Session, *, tenant_id: UUID, staff_ids: Sequence[UUID], since: datetime
) -> StaffLearningWindow:
    """Source active -> ever-active -> completion-row queries, in that order."""
    if not staff_ids:
        return StaffLearningWindow(frozenset(), frozenset(), 0)
    active_ids = frozenset(
        db.scalars(
            select(LearningEvent.person_id)
            .where(LearningEvent.tenant_id == tenant_id)
            .where(LearningEvent.person_id.in_(staff_ids))
            .where(LearningEvent.occurred_at >= since)
            .distinct()
        ).all()
    )
    ever_active_ids = frozenset(
        db.scalars(
            select(LearningEvent.person_id)
            .where(LearningEvent.tenant_id == tenant_id)
            .where(LearningEvent.person_id.in_(staff_ids))
            .distinct()
        ).all()
    )
    completed = int(
        db.scalar(
            select(func.count())
            .select_from(CourseCompletion)
            .where(CourseCompletion.tenant_id == tenant_id)
            .where(CourseCompletion.person_id.in_(staff_ids))
            .where(CourseCompletion.completed_at >= since)
        )
        or 0
    )
    return StaffLearningWindow(active_ids, ever_active_ids, completed)


def learner_window_activity(
    db: Session, *, tenant_id: UUID, person_id: UUID, since: datetime
) -> LearnerWindowActivity:
    """Distinct nonnull chapter subjects, submission rows and passing-score rows."""
    chapters = int(
        db.scalar(
            select(func.count(func.distinct(LearningEvent.subject_id)))
            .where(LearningEvent.tenant_id == tenant_id)
            .where(LearningEvent.person_id == person_id)
            .where(LearningEvent.kind == KIND_CHAPTER_VIEWED)
            .where(LearningEvent.occurred_at >= since)
        )
        or 0
    )
    submissions = int(
        db.scalar(
            select(func.count())
            .select_from(Submission)
            .where(Submission.tenant_id == tenant_id)
            .where(Submission.person_id == person_id)
            .where(Submission.created_at >= since)
        )
        or 0
    )
    passed = int(
        db.scalar(
            select(func.count())
            .select_from(Score)
            .join(
                Submission,
                (Submission.id == Score.submission_id)
                & (Submission.tenant_id == Score.tenant_id),
            )
            .where(Score.tenant_id == tenant_id)
            .where(Submission.person_id == person_id)
            .where(Score.created_at >= since)
            .where(Score.passed.is_(True))
        )
        or 0
    )
    return LearnerWindowActivity(chapters, submissions, passed)
