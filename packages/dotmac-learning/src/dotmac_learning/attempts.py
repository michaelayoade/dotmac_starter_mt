"""Source sitting persistence; selection remains with the public exam engine."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from dotmac_kernel.models import Party
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import events, exam_engine
from .attempt_contracts import (
    AttemptFact,
    AttemptWrite,
    CloseAttemptCommand,
    OpenAttemptCommand,
    SelectionQuestion,
)
from .attempt_models import ActivityAttempt
from .bank_lifecycle_contracts import BankLifecycleConflict
from .curriculum_models import QuestionBankVersion
from .event_contracts import EventWrite
from .models import Activity
from .results import _transaction, _unchanged

_IDS: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


@dataclass(frozen=True)
class _Sitter:
    id: UUID


def _fact(row: ActivityAttempt) -> AttemptFact:
    return AttemptFact(
        question_ext_ids=_IDS.validate_python(deepcopy(row.question_ext_ids)),
        **{
            name: getattr(row, name)
            for name in AttemptFact.__dataclass_fields__
            if name != "question_ext_ids"
        },
    )


def _lock_learner(db: Session, *, tenant_id: UUID, person_id: UUID) -> None:
    # Exact source NO KEY UPDATE: compatible with FK KEY SHARE, exclusive admission.
    # A missing row is deliberately ignored; product authorization owns identity.
    db.execute(
        select(Party.id)
        .where(Party.tenant_id == tenant_id)
        .where(Party.id == person_id)
        .with_for_update(key_share=True, read=False)
    )


def _open_attempt(
    db: Session, *, tenant_id: UUID, person_id: UUID, activity_id: UUID
) -> ActivityAttempt | None:
    return db.scalars(
        select(ActivityAttempt)
        .where(ActivityAttempt.tenant_id == tenant_id)
        .where(ActivityAttempt.activity_id == activity_id)
        .where(ActivityAttempt.person_id == person_id)
        .where(ActivityAttempt.submitted_at.is_(None))
        .order_by(ActivityAttempt.started_at.desc())
    ).first()


def _prior_attempts(
    db: Session, *, tenant_id: UUID, person_id: UUID, activity_id: UUID
) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(ActivityAttempt)
            .where(ActivityAttempt.tenant_id == tenant_id)
            .where(ActivityAttempt.activity_id == activity_id)
            .where(ActivityAttempt.person_id == person_id)
        )
        or 0
    )


def _resolve_pin(db: Session, payload: OpenAttemptCommand) -> UUID | None:
    """Pin precedence: explicit command pin, else the activity's version.

    Fail-closed once any pin is present: the activity must exist, carry
    a bank, and the pin must resolve in the tenant to a published
    version of that bank — a ghost, cross-tenant, cross-bank, draft or
    retired pin serves no new sitting. Only a missing or unpinned
    activity with no explicit pin keeps the historical NULL pin.
    """
    activity = db.scalars(
        select(Activity).where(
            Activity.tenant_id == payload.tenant_id,
            Activity.id == payload.activity_id,
        )
    ).first()
    pin = payload.bank_version_id
    if pin is None and activity is not None:
        pin = activity.bank_version_id
    if pin is None:
        return None
    if activity is None:
        raise BankLifecycleConflict(
            "a pinned attempt requires its activity to resolve the pin against"
        )
    if activity.bank_id is None:
        raise BankLifecycleConflict(
            "a pinned attempt requires the activity to carry a bank"
        )
    version = db.scalars(
        select(QuestionBankVersion).where(
            QuestionBankVersion.tenant_id == payload.tenant_id,
            QuestionBankVersion.id == pin,
        )
    ).first()
    if version is None:
        raise BankLifecycleConflict(
            "the pinned bank version was not found for the tenant"
        )
    if version.state != "published":
        raise BankLifecycleConflict(
            "the pinned bank version does not serve new sittings"
        )
    if version.bank_id != activity.bank_id:
        raise BankLifecycleConflict(
            "the pinned bank version belongs to a different bank than the activity"
        )
    return pin


def open_or_create_attempt(db: Session, payload: OpenAttemptCommand) -> AttemptWrite:
    boundary = _transaction(db)
    payload = payload.model_copy(deep=True)
    scope = {
        "tenant_id": payload.tenant_id,
        "person_id": payload.person_id,
        "activity_id": payload.activity_id,
    }
    _lock_learner(db, tenant_id=payload.tenant_id, person_id=payload.person_id)
    existing = _open_attempt(db, **scope)
    if existing is not None:
        result = AttemptWrite(_fact(existing))
        _unchanged(db, boundary)
        return result
    k = min(payload.count, len(payload.all_ext_ids))
    if k <= 0:
        chosen: list[str] = []
    else:
        variant = f"attempt{_prior_attempts(db, **scope)}:"
        questions = (
            list(payload.questions)
            if payload.questions is not None
            else [SelectionQuestion(ext_id=ext_id) for ext_id in payload.all_ext_ids]
        )
        drawn = exam_engine.select(
            _Sitter(payload.person_id),
            questions,
            exam_engine.SelectionPolicy(total=k),
            variant=variant,
        )
        chosen = [q.ext_id for q in drawn]
    attempt = ActivityAttempt(
        **scope,
        bank_version_id=_resolve_pin(db, payload),
        question_ext_ids=chosen,
        started_at=payload.now or datetime.now(UTC),
    )
    db.add(attempt)
    db.flush()
    emitted = events.emit(
        db,
        EventWrite(
            tenant_id=payload.tenant_id,
            person_id=payload.person_id,
            kind="activity_started",
            subject_id=payload.activity_id,
            detail={"attempt": len(chosen)},
        ),
    )
    result = AttemptWrite(_fact(attempt), emitted)
    _unchanged(db, boundary)
    return result


def close_open_attempt(db: Session, payload: CloseAttemptCommand) -> AttemptFact | None:
    boundary = _transaction(db)
    _lock_learner(db, tenant_id=payload.tenant_id, person_id=payload.person_id)
    attempt = _open_attempt(
        db,
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        activity_id=payload.activity_id,
    )
    if attempt is None:
        _unchanged(db, boundary)
        return None
    attempt.submitted_at = payload.now or datetime.now(UTC)
    db.flush()
    result = _fact(attempt)
    _unchanged(db, boundary)
    return result


__all__ = ["open_or_create_attempt", "close_open_attempt"]
