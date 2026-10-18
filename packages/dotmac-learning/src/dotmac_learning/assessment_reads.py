"""Thin source assessment reads; grading and result writing retain their owners."""

from __future__ import annotations

from copy import deepcopy
from typing import Protocol, cast
from uuid import UUID

from dotmac_kernel.models import Party, PartyPerson
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import attempt_policy
from .attempt_contracts import AssessmentQuestion, PendingGradingFact, QuestionFact
from .curriculum_models import Question
from .models import Activity, Score, Submission
from .references import _activity_fact
from .results import _submission_fact

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class FeedbackTerms(attempt_policy.ActivityTerms, Protocol):
    @property
    def assessment_mode(self) -> str: ...


def questions_for(
    db: Session,
    *,
    tenant_id: UUID,
    bank_id: UUID | None,
    only_ext_ids: list[str] | None = None,
) -> list[AssessmentQuestion]:
    rows = db.scalars(
        select(Question)
        .where(Question.tenant_id == tenant_id)
        .where(Question.bank_id == bank_id)
    ).all()
    if only_ext_ids is not None:
        keep = set(only_ext_ids)
        rows = [q for q in rows if q.ext_id in keep]
    return [
        AssessmentQuestion(
            ext_id=q.ext_id,
            type=q.type,
            correct=_JSON.validate_python(deepcopy(q.correct)),
            weight=q.weight,
            explanation=q.explanation,
            options=_JSON.validate_python(deepcopy(q.options)),
        )
        for q in rows
    ]


def pending_grading(db: Session, *, tenant_id: UUID) -> list[PendingGradingFact]:
    rows = db.execute(
        select(Submission, Activity, Party.email)
        .join(
            Activity,
            (Activity.id == Submission.activity_id)
            & (Activity.tenant_id == Submission.tenant_id),
        )
        .join(
            Party,
            (Party.id == Submission.person_id)
            & (Party.tenant_id == Submission.tenant_id),
        )
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .outerjoin(
            Score,
            (Score.submission_id == Submission.id)
            & (Score.tenant_id == Submission.tenant_id),
        )
        .where(Submission.tenant_id == tenant_id)
        .where(Score.id.is_(None))
        .order_by(Submission.created_at)
    ).all()
    return [
        PendingGradingFact(_submission_fact(s), _activity_fact(a), email)
        for s, a, email in rows
    ]


def attempts_used(
    db: Session, *, tenant_id: UUID, person_id: UUID, activity_id: UUID
) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Submission)
            .where(Submission.tenant_id == tenant_id)
            .where(Submission.activity_id == activity_id)
            .where(Submission.person_id == person_id)
        )
        or 0
    )


def attempts_used_by_person(
    db: Session, *, tenant_id: UUID, person_id: UUID
) -> dict[UUID, int]:
    """Source submission counts across all activities in one grouped query."""
    rows = db.execute(
        select(Submission.activity_id, func.count())
        .where(Submission.tenant_id == tenant_id)
        .where(Submission.person_id == person_id)
        .group_by(Submission.activity_id)
    ).all()
    return {activity_id: int(n) for activity_id, n in rows}


def reveal_feedback(
    activity: FeedbackTerms,
    *,
    passed: bool,
    attempts_used: int,
    attempts_granted: int = 0,
) -> bool:
    mode = activity.assessment_mode
    if mode == "practice":
        return True
    if mode == "exam":
        return False
    exhausted = attempt_policy.entitlement(
        activity, used=attempts_used, granted=attempts_granted
    ).exhausted
    return passed or exhausted


__all__ = [
    "FeedbackTerms",
    "questions_for",
    "bank_questions",
    "pending_grading",
    "attempts_used",
    "attempts_used_by_person",
    "reveal_feedback",
    "has_other_passing_score",
]


def has_other_passing_score(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    activity_id: UUID,
    excluded_score_id: UUID,
) -> bool:
    """Source first-pass suppression: ANY other pass, including equal/later time.

    Academy owns preceding pass/email/opt-out/setting checks, the optional
    savepoint, rendering and outbox writes. This predicate adds no time rank.
    """
    count = db.scalar(
        select(func.count())
        .select_from(Score)
        .join(
            Submission,
            (Submission.id == Score.submission_id)
            & (Submission.tenant_id == Score.tenant_id),
        )
        .where(Score.tenant_id == tenant_id)
        .where(Submission.activity_id == activity_id)
        .where(Submission.person_id == person_id)
        .where(Score.passed.is_(True))
        .where(Score.id != excluded_score_id)
    )
    return int(count or 0) > 0


def bank_questions(
    db: Session, *, tenant_id: UUID, bank_id: UUID | None
) -> tuple[QuestionFact, ...]:
    """Read all stored bank questions without authoring or feedback policy.

    The caller retains answer-key disclosure policy and ordinary Session query
    behavior. Missing and None bank IDs match no rows, without a bank lookup.
    """
    rows = db.scalars(
        select(Question)
        .where(Question.tenant_id == tenant_id)
        .where(Question.bank_id == bank_id)
    ).all()
    return tuple(
        QuestionFact(
            id=q.id,
            tenant_id=q.tenant_id,
            bank_id=q.bank_id,
            ext_id=q.ext_id,
            stem=q.stem,
            type=q.type,
            options=cast(JsonValue, deepcopy(q.options)),
            correct=cast(JsonValue, deepcopy(q.correct)),
            rubric_category=q.rubric_category,
            category=q.category,
            explanation=q.explanation,
            weight=q.weight,
            created_at=q.created_at,
            updated_at=q.updated_at,
        )
        for q in rows
    )
