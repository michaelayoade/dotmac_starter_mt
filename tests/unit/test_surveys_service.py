"""Persistence-owner behavior for reusable Surveys/CSAT mechanics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.models import Base, Tenant
from dotmac_surveys import (
    Answer,
    InvitationRequest,
    InvitationUnavailable,
    Question,
    QuestionType,
    ResponseSubmission,
    SurveyDefinition,
    SurveyStatus,
    SurveyUnavailable,
    create_survey,
    expire_invitation,
    issue_invitation,
    rebuild_survey_metrics,
    submit_invited_response,
    transition_survey_status,
    update_draft_survey,
)
from dotmac_surveys.models import Survey, SurveyInvitation, SurveyResponse
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_surveys": None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            Survey.__table__,
            SurveyInvitation.__table__,
            SurveyResponse.__table__,
        ],
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tenant(db: Session) -> Tenant:
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(row)
    db.flush()
    return row


def _definition(*, slug: str = "customer-feedback") -> SurveyDefinition:
    return SurveyDefinition(
        name="Customer feedback",
        public_slug=slug,
        questions=(
            Question("rating", QuestionType.RATING, "Rate us"),
            Question("recommend", QuestionType.NPS, "Recommend us", required=False),
            Question("comment", QuestionType.FREE_TEXT, "Comment", required=False),
        ),
    )


def _active_survey(db: Session, tenant_id):
    row = create_survey(db, tenant_id=tenant_id, definition=_definition())
    transition_survey_status(
        db,
        tenant_id=tenant_id,
        survey_id=row.id,
        expected=SurveyStatus.DRAFT,
        requested=SurveyStatus.ACTIVE,
    )
    return row


def test_create_is_draft_and_does_not_create_side_effect_rows(db: Session) -> None:
    tenant = _tenant(db)
    row = create_survey(db, tenant_id=tenant.id, definition=_definition())

    assert row.status == SurveyStatus.DRAFT.value
    assert row.total_invited == 0
    assert row.total_responses == 0
    assert db.scalars(select(SurveyInvitation)).all() == []
    assert db.scalars(select(SurveyResponse)).all() == []


def test_only_a_draft_definition_may_be_edited(db: Session) -> None:
    tenant = _tenant(db)
    row = create_survey(db, tenant_id=tenant.id, definition=_definition())
    update_draft_survey(
        db,
        tenant_id=tenant.id,
        survey_id=row.id,
        expected=SurveyStatus.DRAFT,
        definition=_definition(slug="updated-feedback"),
    )
    transition_survey_status(
        db,
        tenant_id=tenant.id,
        survey_id=row.id,
        expected=SurveyStatus.DRAFT,
        requested=SurveyStatus.ACTIVE,
    )

    with pytest.raises(SurveyUnavailable, match="draft"):
        update_draft_survey(
            db,
            tenant_id=tenant.id,
            survey_id=row.id,
            expected=SurveyStatus.ACTIVE,
            definition=_definition(slug="silently-changed"),
        )


def test_invitation_replay_uses_source_event_identity_not_a_product_fk(
    db: Session,
) -> None:
    tenant = _tenant(db)
    survey = _active_survey(db, tenant.id)
    request = InvitationRequest(
        survey_id=survey.id,
        recipient_ref="party:customer-42",
        source_owner="support.ticket_lifecycle",
        source_event_id="ticket-resolution-confirmed:evt-1",
        subject_ref="ticket:TKT-42",
        expires_at=datetime.now(UTC) + timedelta(days=7),
    )

    first = issue_invitation(db, tenant_id=tenant.id, request=request)
    replay = issue_invitation(db, tenant_id=tenant.id, request=request)

    assert replay.replayed is True
    assert replay.invitation.id == first.invitation.id
    assert replay.token == first.token
    assert db.scalars(select(SurveyInvitation)).all() == [first.invitation]


def test_invitation_creation_refuses_inactive_or_expired_surveys(db: Session) -> None:
    tenant = _tenant(db)
    draft = create_survey(db, tenant_id=tenant.id, definition=_definition())
    request = InvitationRequest(
        survey_id=draft.id,
        recipient_ref="customer-42",
        source_owner="operations.work_order_commands",
        source_event_id="work-order-outcome:evt-1",
        subject_ref="work-order:WO-42",
    )

    with pytest.raises(SurveyUnavailable, match="active"):
        issue_invitation(db, tenant_id=tenant.id, request=request)


def test_invitation_expiry_has_an_explicit_idempotent_writer(db: Session) -> None:
    tenant = _tenant(db)
    survey = _active_survey(db, tenant.id)
    due_at = datetime(2026, 8, 25, 9, tzinfo=UTC)
    issued = issue_invitation(
        db,
        tenant_id=tenant.id,
        request=InvitationRequest(
            survey_id=survey.id,
            recipient_ref="customer-42",
            source_owner="service.lifecycle",
            source_event_id="service-outcome:evt-1",
            subject_ref="service:SVC-42",
            expires_at=due_at,
        ),
        issued_at=due_at - timedelta(days=7),
    )

    with pytest.raises(InvitationUnavailable, match="not due"):
        expire_invitation(
            db,
            tenant_id=tenant.id,
            invitation_id=issued.invitation.id,
            expired_at=due_at - timedelta(seconds=1),
        )

    expired = expire_invitation(
        db,
        tenant_id=tenant.id,
        invitation_id=issued.invitation.id,
        expired_at=due_at,
    )
    replay = expire_invitation(
        db,
        tenant_id=tenant.id,
        invitation_id=issued.invitation.id,
        expired_at=due_at + timedelta(hours=1),
    )

    assert expired.status == "expired"
    assert replay is expired


def test_tracked_response_is_immutable_and_rebuilds_metrics(db: Session) -> None:
    tenant = _tenant(db)
    survey = _active_survey(db, tenant.id)
    issued = issue_invitation(
        db,
        tenant_id=tenant.id,
        request=InvitationRequest(
            survey_id=survey.id,
            recipient_ref="customer-42",
            source_owner="support.ticket_lifecycle",
            source_event_id="ticket-resolution-confirmed:evt-2",
            subject_ref="ticket:TKT-43",
        ),
    )
    submitted_at = datetime(2026, 8, 18, 9, tzinfo=UTC)

    receipt = submit_invited_response(
        db,
        tenant_id=tenant.id,
        token=issued.token,
        submission=ResponseSubmission(
            answers=(
                Answer("rating", "5"),
                Answer("recommend", "10"),
                Answer("comment", "Resolved quickly"),
            ),
            submitted_at=submitted_at,
        ),
    )

    assert receipt.rating == 5
    assert receipt.nps_value == 10
    assert issued.invitation.status == "completed"
    assert survey.total_responses == 1
    assert float(survey.avg_rating) == 5.0
    assert float(survey.nps_score) == 100.0

    with pytest.raises(InvitationUnavailable, match="unavailable"):
        submit_invited_response(
            db,
            tenant_id=tenant.id,
            token=issued.token,
            submission=ResponseSubmission(
                answers=(Answer("rating", "1"),),
                submitted_at=submitted_at + timedelta(minutes=1),
            ),
        )

    survey.total_responses = 99
    survey.avg_rating = None
    metrics = rebuild_survey_metrics(db, tenant_id=tenant.id, survey_id=survey.id)
    assert metrics.total_responses == 1
    assert float(metrics.avg_rating or 0) == 5.0
