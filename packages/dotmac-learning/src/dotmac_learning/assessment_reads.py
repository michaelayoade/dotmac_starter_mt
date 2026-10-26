"""Thin source assessment reads; grading and result writing retain their owners."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol, cast
from uuid import UUID

from dotmac_kernel.models import Party, PartyPerson
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import attempt_policy
from .attempt_contracts import AssessmentQuestion, PendingGradingFact, QuestionFact
from .curriculum_models import Question, QuestionBank
from .models import ASSESSMENT_MODES, Activity, Score, Submission
from .references import _activity_fact
from .results import _submission_fact

_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


class FeedbackTerms(attempt_policy.ActivityTerms, Protocol):
    @property
    def assessment_mode(self) -> str: ...


_REVEAL_POLICIES = ("immediate", "on_pass_or_exhausted", "never")


@dataclass(frozen=True, slots=True)
class FeedbackRevealTerms:
    """Required per-mode answer-key reveal policy supplied by the product."""

    reveal_by_mode: Mapping[str, str]

    def __post_init__(self) -> None:
        if not isinstance(self.reveal_by_mode, Mapping):
            raise ValueError("reveal_by_mode must be a mapping")
        copied = dict(self.reveal_by_mode)
        if set(copied) != set(ASSESSMENT_MODES):
            raise ValueError(
                "reveal_by_mode must name exactly the assessment modes "
                f"{ASSESSMENT_MODES}"
            )
        for mode, policy in copied.items():
            if policy not in _REVEAL_POLICIES:
                raise ValueError(
                    f"reveal_by_mode[{mode!r}] must be one of {_REVEAL_POLICIES}"
                )
        if copied["exam"] == "immediate":
            raise ValueError("reveal_by_mode['exam'] may not be 'immediate'")
        object.__setattr__(self, "reveal_by_mode", MappingProxyType(copied))


def _scoped_rows(
    db: Session,
    *,
    tenant_id: UUID,
    bank_id: UUID | None,
    bank_version_id: UUID | None,
) -> list[Question]:
    """Resolve the exact question set for a grading/serving read.

    An explicit version pin wins. Otherwise a bank with a current
    published version serves exactly that version; a bank without one
    (historical/frozen-import estate) serves only its NULL-version
    rows. Rows from several versions are never mixed.
    """
    if bank_version_id is not None:
        pinned = (
            select(Question)
            .where(Question.tenant_id == tenant_id)
            .where(Question.bank_version_id == bank_version_id)
        )
        if bank_id is not None:
            # A pin never widens the read across banks: when the caller
            # also names the bank, only that bank's pinned rows serve.
            pinned = pinned.where(Question.bank_id == bank_id)
        return list(db.scalars(pinned).all())
    if bank_id is None:
        return list(
            db.scalars(
                select(Question)
                .where(Question.tenant_id == tenant_id)
                .where(Question.bank_id == bank_id)
            ).all()
        )
    bank = db.scalars(
        select(QuestionBank).where(
            QuestionBank.tenant_id == tenant_id, QuestionBank.id == bank_id
        )
    ).first()
    statement = (
        select(Question)
        .where(Question.tenant_id == tenant_id)
        .where(Question.bank_id == bank_id)
    )
    if bank is not None and bank.current_version_id is not None:
        statement = statement.where(Question.bank_version_id == bank.current_version_id)
    elif bank is not None:
        statement = statement.where(Question.bank_version_id.is_(None))
    return list(db.scalars(statement).all())


def questions_for(
    db: Session,
    *,
    tenant_id: UUID,
    bank_id: UUID | None,
    only_ext_ids: list[str] | None = None,
    bank_version_id: UUID | None = None,
) -> list[AssessmentQuestion]:
    rows = _scoped_rows(
        db, tenant_id=tenant_id, bank_id=bank_id, bank_version_id=bank_version_id
    )
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
    terms: FeedbackRevealTerms,
    passed: bool,
    attempts_used: int,
    attempts_granted: int = 0,
) -> bool:
    policy = terms.reveal_by_mode.get(activity.assessment_mode, "on_pass_or_exhausted")
    if policy == "immediate":
        return True
    if policy == "never":
        return False
    exhausted = attempt_policy.entitlement(
        activity, used=attempts_used, granted=attempts_granted
    ).exhausted
    return passed or exhausted


__all__ = [
    "FeedbackTerms",
    "FeedbackRevealTerms",
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
    db: Session,
    *,
    tenant_id: UUID,
    bank_id: UUID | None,
    bank_version_id: UUID | None = None,
) -> tuple[QuestionFact, ...]:
    """Read serving bank questions without authoring or feedback policy.

    The caller retains answer-key disclosure policy and ordinary Session query
    behavior. Missing and None bank IDs match no rows. Without an explicit
    version pin the read serves the bank's current published version (or
    its historical NULL-version rows when it has none).
    """
    rows = _scoped_rows(
        db, tenant_id=tenant_id, bank_id=bank_id, bank_version_id=bank_version_id
    )
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
            bank_version_id=q.bank_version_id,
        )
        for q in rows
    )
