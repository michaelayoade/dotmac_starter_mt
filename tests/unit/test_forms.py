"""ERP-parity behavior for the reusable Forms owner."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_forms import (
    AnswerInput,
    FieldDefinition,
    FieldType,
    FormDefinition,
    FormUnavailable,
    FormValidationError,
    OptionDefinition,
    SectionDefinition,
    SubmissionConflict,
    SubmissionRequest,
    add_field,
    add_option,
    add_section,
    create_draft_version,
    create_form,
    publish_version,
    submit_form,
)
from dotmac_forms.models import TENANT_MODELS, FormAnswer, FormSubmission
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_forms": None}},
    )
    Base.metadata.create_all(
        engine, tables=[Tenant.__table__, *(m.__table__ for m in TENANT_MODELS)]
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


def _published_form(db: Session, tenant_id):
    now = datetime(2026, 8, 21, 8, tzinfo=UTC)
    form = create_form(
        db,
        tenant_id=tenant_id,
        definition=FormDefinition(
            name="Application",
            form_type="recruitment",
            owner_ref="job:engineer",
        ),
        created_at=now,
    )
    version = create_draft_version(
        db, tenant_id=tenant_id, form_id=form.id, created_at=now
    )
    section = add_section(
        db,
        tenant_id=tenant_id,
        version_id=version.id,
        definition=SectionDefinition(key="applicant", title="Applicant", position=1),
    )
    email = add_field(
        db,
        tenant_id=tenant_id,
        version_id=version.id,
        definition=FieldDefinition(
            section_id=section.id,
            key="email",
            label="Email",
            field_type=FieldType.EMAIL,
            required=True,
            position=1,
        ),
    )
    role = add_field(
        db,
        tenant_id=tenant_id,
        version_id=version.id,
        definition=FieldDefinition(
            section_id=section.id,
            key="role",
            label="Role",
            field_type=FieldType.SINGLE_CHOICE,
            required=True,
            position=2,
        ),
    )
    add_option(
        db,
        tenant_id=tenant_id,
        field_id=role.id,
        definition=OptionDefinition(value="noc", label="NOC", position=1),
    )
    published = publish_version(
        db, tenant_id=tenant_id, version_id=version.id, published_at=now
    )
    return published, section, email


def test_published_version_is_immutable_and_carries_a_content_digest(
    db: Session,
) -> None:
    tenant = _tenant(db)
    version, section, _ = _published_form(db, tenant.id)
    assert version.status == "published"
    assert len(version.content_digest or "") == 64

    with pytest.raises(FormUnavailable, match="draft"):
        add_field(
            db,
            tenant_id=tenant.id,
            version_id=version.id,
            definition=FieldDefinition(
                section_id=section.id,
                key="late",
                label="Late mutation",
                field_type=FieldType.TEXT,
                position=3,
            ),
        )


def test_submission_validates_required_email_and_closed_choice(db: Session) -> None:
    tenant = _tenant(db)
    version, _, _ = _published_form(db, tenant.id)
    base = {
        "submission_key": "application:42",
        "form_version_id": version.id,
        "subject_ref": "candidate:42",
        "submitted_at": datetime(2026, 8, 21, 9, tzinfo=UTC),
    }

    with pytest.raises(FormValidationError, match="email"):
        submit_form(
            db,
            tenant_id=tenant.id,
            request=SubmissionRequest(
                **base,
                answers=(
                    AnswerInput("email", "not-an-email"),
                    AnswerInput("role", "noc"),
                ),
            ),
        )
    with pytest.raises(FormValidationError, match="role"):
        submit_form(
            db,
            tenant_id=tenant.id,
            request=SubmissionRequest(
                **base,
                answers=(
                    AnswerInput("email", "ops@example.com"),
                    AnswerInput("role", "sales"),
                ),
            ),
        )

    receipt = submit_form(
        db,
        tenant_id=tenant.id,
        request=SubmissionRequest(
            **base,
            answers=(
                AnswerInput("email", "ops@example.com"),
                AnswerInput("role", "noc"),
            ),
        ),
    )
    answers = db.scalars(
        select(FormAnswer).where(FormAnswer.submission_id == receipt.submission.id)
    ).all()
    assert [
        (a.field_key_snapshot, a.field_label_snapshot, a.display_value) for a in answers
    ] == [
        ("email", "Email", "ops@example.com"),
        ("role", "Role", "NOC"),
    ]


def test_submission_key_is_idempotent_and_conflicts_on_different_answers(
    db: Session,
) -> None:
    tenant = _tenant(db)
    version, _, _ = _published_form(db, tenant.id)
    request = SubmissionRequest(
        submission_key="application:43",
        form_version_id=version.id,
        subject_ref="candidate:43",
        submitted_at=datetime(2026, 8, 21, 9, tzinfo=UTC),
        answers=(AnswerInput("email", "one@example.com"), AnswerInput("role", "noc")),
    )
    first = submit_form(db, tenant_id=tenant.id, request=request)
    replay = submit_form(db, tenant_id=tenant.id, request=request)
    assert replay.replayed is True
    assert replay.submission.id == first.submission.id
    assert len(db.scalars(select(FormSubmission)).all()) == 1

    with pytest.raises(SubmissionConflict, match="different content"):
        submit_form(
            db,
            tenant_id=tenant.id,
            request=SubmissionRequest(
                submission_key=request.submission_key,
                form_version_id=version.id,
                subject_ref=request.subject_ref,
                submitted_at=request.submitted_at,
                answers=(
                    AnswerInput("email", "two@example.com"),
                    AnswerInput("role", "noc"),
                ),
            ),
        )
