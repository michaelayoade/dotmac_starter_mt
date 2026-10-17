"""Caller-transaction result writes; no grading engine or product side effects."""

from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from dotmac_kernel.models import Party, PartyPerson, PartyType
from pydantic import JsonValue, TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, SessionTransaction

from . import completion, events
from .event_contracts import EventWrite
from .models import Activity, Score, Submission
from .progress_contracts import CompletionCommand, ScoreFact
from .result_contracts import (
    AdmissionContention,
    AdmissionDecision,
    AdmissionObservation,
    AdmissionRefused,
    AttemptAdmissionPolicy,
    LabResultCommand,
    MCQResultCommand,
    OverrideScoreCommand,
    ResultBadRequest,
    ResultWrite,
    SubmissionFact,
    TransactionChanged,
)


_JSON = TypeAdapter(JsonValue)
_ANSWERS = TypeAdapter(dict[str, JsonValue])


def _transaction(db: Session) -> tuple[SessionTransaction, SessionTransaction | None]:
    root = db.get_transaction()
    if root is None or not root.is_active:
        raise TransactionChanged(
            "An already active caller root transaction is required."
        )
    nested = db.get_nested_transaction()
    if nested is not None and not nested.is_active:
        raise TransactionChanged("The caller savepoint is not active.")
    return root, nested


def _unchanged(
    db: Session, boundary: tuple[SessionTransaction, SessionTransaction | None]
) -> None:
    if _transaction(db) != boundary:
        raise TransactionChanged("Policy changed the caller transaction boundary.")


def _activity(db: Session, tenant_id: UUID, activity_id: UUID) -> Activity:
    activity = db.scalar(
        select(Activity)
        .where(Activity.tenant_id == tenant_id, Activity.id == activity_id)
        .execution_options(populate_existing=True)
    )
    if activity is None:
        raise ResultBadRequest("activity not found for tenant")
    return activity


def _lock_learner(db: Session, tenant_id: UUID, person_id: UUID) -> None:
    person = db.scalar(
        select(Party)
        .where(Party.tenant_id == tenant_id, Party.id == person_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if person is None or person.party_type != PartyType.person:
        raise AdmissionRefused("Learner account no longer exists.")
    try:
        profile = db.scalar(
            select(PartyPerson.party_id)
            .where(PartyPerson.party_id == person_id)
            .with_for_update(read=True, key_share=True, nowait=True)
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise AdmissionContention(
                "Learner profile is contended; caller rollback required."
            ) from exc
        raise
    if profile is None:
        raise AdmissionRefused("Learner account no longer exists.")


def _observation(db: Session, payload: MCQResultCommand) -> AdmissionObservation:
    activity = _activity(db, payload.tenant_id, payload.activity_id)
    used = int(
        db.scalar(
            select(func.count())
            .select_from(Submission)
            .where(
                Submission.tenant_id == payload.tenant_id,
                Submission.person_id == payload.person_id,
                Submission.activity_id == payload.activity_id,
            )
        )
        or 0
    )
    return AdmissionObservation(
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        activity_id=activity.id,
        course_id=activity.course_id,
        max_attempts=activity.max_attempts,
        used=used,
        activity_type=activity.type,
        grading=activity.grading,
        pass_threshold=activity.pass_threshold,
        assessment_mode=activity.assessment_mode,
        bank_id=activity.bank_id,
        question_count=activity.question_count,
    )


def _score_fact(score: Score) -> ScoreFact:
    return ScoreFact(
        **{
            name: deepcopy(getattr(score, name))
            for name in (
                "id",
                "tenant_id",
                "submission_id",
                "score",
                "max_score",
                "fraction",
                "passed",
                "source",
                "override_reason",
                "created_at",
                "updated_at",
            )
        },
        per_item=_JSON.validate_python(deepcopy(score.per_item)),
    )


def _submission_fact(sub: Submission) -> SubmissionFact:
    return SubmissionFact(
        answers=_ANSWERS.validate_python(deepcopy(sub.answers)),
        **{
            name: deepcopy(getattr(sub, name))
            for name in (
                "id",
                "tenant_id",
                "activity_id",
                "person_id",
                "attempt_no",
                "created_at",
                "updated_at",
            )
        },
    )


def _append(
    db: Session,
    payload: MCQResultCommand | LabResultCommand,
    activity: Activity,
    *,
    lab: bool = False,
) -> ResultWrite:
    previous = db.scalar(
        select(func.coalesce(func.max(Submission.attempt_no), 0)).where(
            Submission.tenant_id == payload.tenant_id,
            Submission.activity_id == payload.activity_id,
            Submission.person_id == payload.person_id,
        )
    )
    answers = (
        {"seed": payload.seed, "instance": str(payload.instance_id)}
        if isinstance(payload, LabResultCommand)
        else deepcopy(payload.answers)
    )
    sub = Submission(
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        activity_id=payload.activity_id,
        answers=answers,
        attempt_no=int(previous or 0) + 1,
    )
    db.add(sub)
    db.flush()
    detail: dict[str, JsonValue] = {
        "activity_id": str(activity.id),
        "attempt_no": sub.attempt_no,
    }
    if lab:
        detail["lab"] = True
    emitted = [
        events.emit(
            db,
            EventWrite(
                tenant_id=payload.tenant_id,
                person_id=payload.person_id,
                kind="submission_made",
                course_id=activity.course_id,
                subject_id=sub.id,
                detail=detail,
            ),
        )
    ]
    grade = payload.grading
    if not lab and activity.grading == "manual":
        return ResultWrite(_submission_fact(sub), None, None, tuple(emitted))
    if grade is None:
        raise ResultBadRequest("An externally validated grade is required.")
    values = grade.model_dump()
    values.update(source="auto", override_reason=None)
    score = Score(tenant_id=payload.tenant_id, submission_id=sub.id, **values)
    db.add(score)
    db.flush()
    detail = {
        "activity_id": str(activity.id),
        "passed": score.passed,
        "fraction": score.fraction,
    }
    if lab:
        detail["lab"] = True
    emitted.append(
        events.emit(
            db,
            EventWrite(
                tenant_id=payload.tenant_id,
                person_id=payload.person_id,
                kind="work_graded",
                course_id=activity.course_id,
                subject_id=sub.id,
                detail=detail,
            ),
        )
    )
    if isinstance(payload, LabResultCommand):
        emitted.append(
            events.emit(
                db,
                EventWrite(
                    tenant_id=payload.tenant_id,
                    person_id=payload.person_id,
                    kind="lab_check_passed" if score.passed else "lab_check_failed",
                    course_id=activity.course_id,
                    subject_id=activity.id,
                    detail={
                        "instance": str(payload.instance_id),
                        "checks": len(grade.per_item),
                    },
                ),
            )
        )
    consequence = completion.recompute_completion(
        db,
        CompletionCommand(
            tenant_id=payload.tenant_id,
            person_id=payload.person_id,
            course_id=activity.course_id,
        ),
    )
    return ResultWrite(
        _submission_fact(sub), _score_fact(score), consequence, tuple(emitted)
    )


def record_mcq_result(
    db: Session, payload: MCQResultCommand, *, policy: AttemptAdmissionPolicy
) -> ResultWrite:
    """Serialize admission, invoke the trusted policy, then allocate MAX+1.

    The policy receives no Session/ORM/SQL capability. Its trusted product
    reader must be bound to this exact caller Session and observation IDs.
    Boundary checks detect misuse; they cannot undo a callback's commit.
    Existing activity/grant edits are not serialized by this learner lock.
    """
    boundary = _transaction(db)
    payload = payload.model_copy(deep=True)
    if db.connection().get_isolation_level().upper() != "READ COMMITTED":
        raise TransactionChanged("Admission requires READ COMMITTED isolation.")
    _lock_learner(db, payload.tenant_id, payload.person_id)
    observation = _observation(db, payload)
    if observation.activity_type != "mcq_test":
        raise ResultBadRequest(
            f"{observation.activity_type!r} activities are not graded here; "
            "use the lab workspace to submit this activity."
        )
    if observation.grading != "manual" and payload.grading is None:
        raise ResultBadRequest("An externally validated grade is required.")
    _unchanged(db, boundary)
    try:
        decision = policy.evaluate(observation)
    finally:
        _unchanged(db, boundary)
    _lock_learner(db, payload.tenant_id, payload.person_id)
    if _observation(db, payload) != observation:
        raise AdmissionRefused(
            "Admission observations changed during policy evaluation."
        )
    if not isinstance(decision, AdmissionDecision):
        raise TypeError("Policy must return AdmissionDecision.")
    if not decision.allowed:
        raise AdmissionRefused("No attempts remaining.")
    return _append(db, payload, _activity(db, payload.tenant_id, payload.activity_id))


def record_lab_result(db: Session, payload: LabResultCommand) -> ResultWrite:
    """Append a product-authorized live lab result without learner admission locks."""
    _transaction(db)
    activity = _activity(db, payload.tenant_id, payload.activity_id)
    if activity.type != "lab":
        raise ResultBadRequest("Lab results require a lab activity.")
    return _append(db, payload, activity, lab=True)


def override_score(db: Session, payload: OverrideScoreCommand) -> ResultWrite:
    """Append another score; retain source override arithmetic and multiplicity."""
    _transaction(db)
    sub = db.scalar(
        select(Submission).where(
            Submission.tenant_id == payload.tenant_id,
            Submission.id == payload.submission_id,
        )
    )
    if sub is None:
        raise ResultBadRequest("submission not found for tenant")
    activity = _activity(db, payload.tenant_id, sub.activity_id)
    fraction = payload.score_value / payload.max_score if payload.max_score else 0.0
    score = Score(
        tenant_id=payload.tenant_id,
        submission_id=sub.id,
        score=payload.score_value,
        max_score=payload.max_score,
        fraction=fraction,
        passed=payload.max_score > 0 and fraction >= activity.pass_threshold,
        per_item=[],
        source="override",
        override_reason=payload.reason,
    )
    db.add(score)
    db.flush()
    consequence = completion.recompute_completion(
        db,
        CompletionCommand(
            tenant_id=payload.tenant_id,
            person_id=sub.person_id,
            course_id=activity.course_id,
        ),
    )
    return ResultWrite(_submission_fact(sub), _score_fact(score), consequence, ())
