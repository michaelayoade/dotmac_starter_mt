"""Flush-only persistence owner for reusable feedback mechanics."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_surveys.contracts import (
    InvitationRequest,
    InvitationStatus,
    InvitationUnavailable,
    Question,
    QuestionType,
    ResponseSubmission,
    SurveyConflict,
    SurveyDefinition,
    SurveyMetrics,
    SurveyStatus,
    SurveyUnavailable,
)
from dotmac_surveys.lifecycle import (
    calculate_metrics,
    transition_survey,
    validate_answers,
)
from dotmac_surveys.models import Survey, SurveyInvitation, SurveyResponse


@dataclass(frozen=True)
class InvitationIssue:
    invitation: SurveyInvitation
    token: str
    replayed: bool = False


@dataclass(frozen=True)
class ResponseReceipt:
    survey_id: UUID
    response_id: UUID
    rating: int | None
    nps_value: int | None
    thank_you_message: str | None


def _question_row(question: Question) -> dict[str, object]:
    return {
        "key": question.key,
        "type": question.type.value,
        "label": question.label,
        "required": question.required,
        "options": list(question.options) if question.options else None,
    }


def _questions(row: Survey) -> tuple[Question, ...]:
    reviewed: list[Question] = []
    for raw in row.questions:
        raw_options = raw.get("options")
        options = (
            tuple(str(value) for value in raw_options)
            if isinstance(raw_options, list | tuple)
            else ()
        )
        reviewed.append(
            Question(
                key=str(raw["key"]),
                type=QuestionType(str(raw["type"])),
                label=str(raw["label"]),
                required=bool(raw.get("required", True)),
                options=options,
            )
        )
    return tuple(reviewed)


def _survey(
    db: Session, tenant_id: UUID, survey_id: UUID, *, lock: bool = True
) -> Survey:
    statement = select(Survey).where(
        Survey.tenant_id == tenant_id, Survey.id == survey_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise SurveyUnavailable("survey not found")
    return row


def _apply_definition(row: Survey, definition: SurveyDefinition) -> None:
    row.name = definition.name
    row.description = definition.description
    row.questions = [_question_row(question) for question in definition.questions]
    row.public_slug = definition.public_slug
    row.thank_you_message = definition.thank_you_message
    row.expires_at = definition.expires_at


def create_survey(
    db: Session, *, tenant_id: UUID, definition: SurveyDefinition
) -> Survey:
    row = Survey(
        tenant_id=tenant_id,
        name=definition.name,
        description=definition.description,
        questions=[_question_row(question) for question in definition.questions],
        public_slug=definition.public_slug,
        thank_you_message=definition.thank_you_message,
        status=SurveyStatus.DRAFT.value,
        expires_at=definition.expires_at,
        created_by_id=definition.created_by_id,
        total_invited=0,
        total_responses=0,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise SurveyConflict(
            "survey public slug already exists for this tenant"
        ) from exc
    return row


def update_draft_survey(
    db: Session,
    *,
    tenant_id: UUID,
    survey_id: UUID,
    expected: SurveyStatus,
    definition: SurveyDefinition,
) -> Survey:
    row = _survey(db, tenant_id, survey_id)
    current = SurveyStatus(row.status)
    if current is not expected:
        raise SurveyUnavailable(
            f"survey state expected {expected.value}, found {current.value}"
        )
    if current is not SurveyStatus.DRAFT:
        raise SurveyUnavailable("only a draft survey definition may be edited")

    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            _apply_definition(row, definition)
            db.flush()
    except IntegrityError as exc:
        raise SurveyConflict(
            "survey public slug already exists for this tenant"
        ) from exc
    return row


def transition_survey_status(
    db: Session,
    *,
    tenant_id: UUID,
    survey_id: UUID,
    expected: SurveyStatus,
    requested: SurveyStatus,
) -> Survey:
    row = _survey(db, tenant_id, survey_id)
    row.status = transition_survey(
        SurveyStatus(row.status),
        requested,
        expected=expected,
        has_questions=bool(_questions(row)),
    ).value
    db.flush()
    return row


def _is_expired(expires_at: datetime | None, *, at: datetime) -> bool:
    if expires_at is None:
        return False
    comparable = expires_at
    if comparable.utcoffset() is None:
        comparable = comparable.replace(tzinfo=UTC)
    return comparable <= at


def _answerable(row: Survey, *, at: datetime) -> tuple[Question, ...]:
    if row.status != SurveyStatus.ACTIVE.value:
        raise SurveyUnavailable("survey is not active")
    if _is_expired(row.expires_at, at=at):
        raise SurveyUnavailable("survey is expired")
    questions = _questions(row)
    if not questions:
        raise SurveyUnavailable("survey has no answerable questions")
    return questions


def _effective_invitation_expiry(
    survey_expires_at: datetime | None, requested_expires_at: datetime | None
) -> datetime | None:
    candidates = tuple(
        value
        for value in (survey_expires_at, requested_expires_at)
        if value is not None
    )
    return min(candidates) if candidates else None


def issue_invitation(
    db: Session,
    *,
    tenant_id: UUID,
    request: InvitationRequest,
    issued_at: datetime | None = None,
) -> InvitationIssue:
    effective_now = issued_at or datetime.now(UTC)
    if effective_now.utcoffset() is None:
        raise ValueError("issued_at must be timezone-aware")
    survey = _survey(db, tenant_id, request.survey_id)
    _answerable(survey, at=effective_now)

    identity = (
        SurveyInvitation.tenant_id == tenant_id,
        SurveyInvitation.survey_id == survey.id,
        SurveyInvitation.recipient_ref == request.recipient_ref,
        SurveyInvitation.source_owner == request.source_owner,
        SurveyInvitation.source_event_id == request.source_event_id,
    )
    existing = db.scalar(select(SurveyInvitation).where(*identity))
    if existing is not None:
        if existing.subject_ref != request.subject_ref:
            raise SurveyConflict(
                "source event identity was reused with a different subject reference"
            )
        return InvitationIssue(existing, existing.token, replayed=True)

    token = secrets.token_urlsafe(32)
    row = SurveyInvitation(
        tenant_id=tenant_id,
        survey_id=survey.id,
        recipient_ref=request.recipient_ref,
        token=token,
        source_owner=request.source_owner,
        source_event_id=request.source_event_id,
        subject_ref=request.subject_ref,
        status=InvitationStatus.PENDING.value,
        expires_at=_effective_invitation_expiry(survey.expires_at, request.expires_at),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        replay = db.scalar(select(SurveyInvitation).where(*identity))
        if replay is not None and replay.subject_ref == request.subject_ref:
            return InvitationIssue(replay, replay.token, replayed=True)
        raise SurveyConflict("survey invitation identity conflicts") from exc

    survey.total_invited += 1
    db.flush()
    return InvitationIssue(row, token)


def expire_invitation(
    db: Session,
    *,
    tenant_id: UUID,
    invitation_id: UUID,
    expired_at: datetime,
) -> SurveyInvitation:
    if expired_at.utcoffset() is None:
        raise ValueError("expired_at must be timezone-aware")
    invitation = db.scalar(
        select(SurveyInvitation)
        .where(
            SurveyInvitation.tenant_id == tenant_id,
            SurveyInvitation.id == invitation_id,
        )
        .with_for_update()
    )
    if invitation is None:
        raise InvitationUnavailable("survey invitation is unavailable")
    if invitation.status == InvitationStatus.EXPIRED.value:
        return invitation
    if invitation.status != InvitationStatus.PENDING.value:
        raise InvitationUnavailable("survey invitation is unavailable")
    if invitation.expires_at is None or not _is_expired(
        invitation.expires_at, at=expired_at
    ):
        raise InvitationUnavailable("survey invitation expiry is not due")
    invitation.status = InvitationStatus.EXPIRED.value
    db.flush()
    return invitation


def _refresh_metrics(db: Session, survey: Survey) -> SurveyMetrics:
    rows = db.execute(
        select(SurveyResponse.rating, SurveyResponse.nps_value)
        .where(
            SurveyResponse.tenant_id == survey.tenant_id,
            SurveyResponse.survey_id == survey.id,
        )
        .order_by(SurveyResponse.id)
    ).all()
    metrics = calculate_metrics(
        ratings=(row.rating for row in rows),
        nps_values=(row.nps_value for row in rows),
    )
    survey.total_invited = int(
        db.scalar(
            select(func.count(SurveyInvitation.id)).where(
                SurveyInvitation.tenant_id == survey.tenant_id,
                SurveyInvitation.survey_id == survey.id,
            )
        )
        or 0
    )
    survey.total_responses = metrics.total_responses
    survey.avg_rating = metrics.avg_rating
    survey.nps_score = metrics.nps_score
    db.flush()
    return metrics


def _record_response(
    db: Session,
    *,
    survey: Survey,
    invitation: SurveyInvitation | None,
    submission: ResponseSubmission,
) -> ResponseReceipt:
    reviewed = validate_answers(_questions(survey), submission.answers)
    row = SurveyResponse(
        tenant_id=survey.tenant_id,
        survey_id=survey.id,
        invitation_id=invitation.id if invitation is not None else None,
        answers=reviewed.answers,
        rating=reviewed.rating,
        nps_value=reviewed.nps_value,
        submitted_at=submission.submitted_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            if invitation is not None:
                invitation.status = InvitationStatus.COMPLETED.value
                invitation.completed_at = submission.submitted_at
            db.flush()
    except IntegrityError as exc:
        raise InvitationUnavailable("survey invitation is unavailable") from exc
    _refresh_metrics(db, survey)
    return ResponseReceipt(
        survey_id=survey.id,
        response_id=row.id,
        rating=row.rating,
        nps_value=row.nps_value,
        thank_you_message=survey.thank_you_message,
    )


def submit_invited_response(
    db: Session,
    *,
    tenant_id: UUID,
    token: str,
    submission: ResponseSubmission,
) -> ResponseReceipt:
    invitation = db.scalar(
        select(SurveyInvitation)
        .where(
            SurveyInvitation.tenant_id == tenant_id,
            SurveyInvitation.token == token,
        )
        .with_for_update()
    )
    if invitation is None or invitation.status != InvitationStatus.PENDING.value:
        raise InvitationUnavailable("survey invitation is unavailable")
    if _is_expired(invitation.expires_at, at=submission.submitted_at):
        raise InvitationUnavailable("survey invitation is unavailable")
    survey = _survey(db, tenant_id, invitation.survey_id)
    _answerable(survey, at=submission.submitted_at)
    return _record_response(
        db, survey=survey, invitation=invitation, submission=submission
    )


def submit_public_response(
    db: Session,
    *,
    tenant_id: UUID,
    public_slug: str,
    submission: ResponseSubmission,
) -> ResponseReceipt:
    survey = db.scalar(
        select(Survey)
        .where(
            Survey.tenant_id == tenant_id,
            Survey.public_slug == public_slug.strip().lower(),
        )
        .with_for_update()
    )
    if survey is None:
        raise SurveyUnavailable("survey is unavailable")
    _answerable(survey, at=submission.submitted_at)
    return _record_response(db, survey=survey, invitation=None, submission=submission)


def rebuild_survey_metrics(
    db: Session, *, tenant_id: UUID, survey_id: UUID
) -> SurveyMetrics:
    survey = _survey(db, tenant_id, survey_id)
    return _refresh_metrics(db, survey)


__all__ = [
    "InvitationIssue",
    "ResponseReceipt",
    "create_survey",
    "expire_invitation",
    "issue_invitation",
    "rebuild_survey_metrics",
    "submit_invited_response",
    "submit_public_response",
    "transition_survey_status",
    "update_draft_survey",
]
