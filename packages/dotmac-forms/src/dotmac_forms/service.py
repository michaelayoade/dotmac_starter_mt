"""Flush-only single writer for reusable form definitions and submissions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_forms.contracts import (
    AnswerInput,
    AnswerValue,
    FieldDefinition,
    FieldType,
    FormDefinition,
    OptionDefinition,
    SectionDefinition,
    SubmissionRequest,
    fingerprint,
)
from dotmac_forms.models import (
    Form,
    FormAnswer,
    FormField,
    FormFieldOption,
    FormSection,
    FormSubmission,
    FormVersion,
)

CHOICE_TYPES = {FieldType.SINGLE_CHOICE, FieldType.MULTI_CHOICE, FieldType.DROPDOWN}
FILE_TYPES = {FieldType.FILE, FieldType.IMAGE, FieldType.PDF}


class FormError(ValueError):
    """A Forms command cannot be admitted."""


class FormUnavailable(FormError):
    """The tenant-local form subject is missing or in the wrong state."""


class FormValidationError(FormError):
    """A submitted answer violates the published definition."""


class SubmissionConflict(FormError):
    """A stable submission identity was reused with different content."""


@dataclass(frozen=True, slots=True)
class SubmissionReceipt:
    submission: FormSubmission
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ReviewedAnswer:
    value: object | None
    display: str | None
    file_ref: str | None = None


def _form(db: Session, tenant_id: UUID, form_id: UUID) -> Form:
    row = db.scalar(select(Form).where(Form.tenant_id == tenant_id, Form.id == form_id))
    if row is None:
        raise FormUnavailable("form not found")
    return row


def _version(db: Session, tenant_id: UUID, version_id: UUID) -> FormVersion:
    row = db.scalar(
        select(FormVersion).where(
            FormVersion.tenant_id == tenant_id, FormVersion.id == version_id
        )
    )
    if row is None:
        raise FormUnavailable("form version not found")
    return row


def _draft(db: Session, tenant_id: UUID, version_id: UUID) -> FormVersion:
    row = _version(db, tenant_id, version_id)
    if row.status != "draft":
        raise FormUnavailable("only a draft form version may be changed")
    return row


def create_form(
    db: Session,
    *,
    tenant_id: UUID,
    definition: FormDefinition,
    created_at: datetime,
) -> Form:
    if created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    row = Form(
        tenant_id=tenant_id,
        name=definition.name,
        description=definition.description,
        form_type=definition.form_type,
        owner_ref=definition.owner_ref,
        created_at=created_at,
        updated_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def create_draft_version(
    db: Session,
    *,
    tenant_id: UUID,
    form_id: UUID,
    created_at: datetime,
    settings: dict[str, object] | None = None,
) -> FormVersion:
    if created_at.utcoffset() is None:
        raise ValueError("created_at must be timezone-aware")
    _form(db, tenant_id, form_id)
    highest = db.scalar(
        select(func.max(FormVersion.version_number)).where(
            FormVersion.tenant_id == tenant_id, FormVersion.form_id == form_id
        )
    )
    row = FormVersion(
        tenant_id=tenant_id,
        form_id=form_id,
        version_number=int(highest or 0) + 1,
        status="draft",
        settings=settings or {},
        created_at=created_at,
    )
    db.add(row)
    db.flush()
    return row


def add_section(
    db: Session,
    *,
    tenant_id: UUID,
    version_id: UUID,
    definition: SectionDefinition,
) -> FormSection:
    _draft(db, tenant_id, version_id)
    row = FormSection(
        tenant_id=tenant_id,
        version_id=version_id,
        key=definition.key,
        title=definition.title,
        description=definition.description,
        position=definition.position,
    )
    db.add(row)
    db.flush()
    return row


def add_field(
    db: Session,
    *,
    tenant_id: UUID,
    version_id: UUID,
    definition: FieldDefinition,
) -> FormField:
    _draft(db, tenant_id, version_id)
    section = db.scalar(
        select(FormSection).where(
            FormSection.tenant_id == tenant_id,
            FormSection.version_id == version_id,
            FormSection.id == definition.section_id,
        )
    )
    if section is None:
        raise FormUnavailable("form section not found in this version")
    row = FormField(
        tenant_id=tenant_id,
        version_id=version_id,
        section_id=definition.section_id,
        key=definition.key,
        label=definition.label,
        field_type=definition.field_type.value,
        required=definition.required,
        help_text=definition.help_text,
        settings=dict(definition.settings),
        validation=dict(definition.validation),
        position=definition.position,
    )
    db.add(row)
    db.flush()
    return row


def add_option(
    db: Session,
    *,
    tenant_id: UUID,
    field_id: UUID,
    definition: OptionDefinition,
) -> FormFieldOption:
    field = db.scalar(
        select(FormField).where(
            FormField.tenant_id == tenant_id, FormField.id == field_id
        )
    )
    if field is None:
        raise FormUnavailable("form field not found")
    _draft(db, tenant_id, field.version_id)
    if FieldType(field.field_type) not in CHOICE_TYPES:
        raise FormValidationError("only a choice field may declare options")
    row = FormFieldOption(
        tenant_id=tenant_id,
        field_id=field_id,
        value=definition.value,
        label=definition.label,
        position=definition.position,
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def _definition_payload(db: Session, version: FormVersion) -> dict[str, object]:
    sections = db.scalars(
        select(FormSection)
        .where(
            FormSection.tenant_id == version.tenant_id,
            FormSection.version_id == version.id,
        )
        .order_by(FormSection.position, FormSection.key)
    ).all()
    fields = db.scalars(
        select(FormField)
        .where(
            FormField.tenant_id == version.tenant_id,
            FormField.version_id == version.id,
        )
        .order_by(FormField.position, FormField.key)
    ).all()
    options = db.scalars(
        select(FormFieldOption)
        .join(
            FormField,
            (FormField.tenant_id == FormFieldOption.tenant_id)
            & (FormField.id == FormFieldOption.field_id),
        )
        .where(
            FormField.tenant_id == version.tenant_id,
            FormField.version_id == version.id,
            FormFieldOption.active.is_(True),
        )
        .order_by(FormFieldOption.position, FormFieldOption.value)
    ).all()
    by_field: dict[UUID, list[dict[str, object]]] = {}
    for option in options:
        by_field.setdefault(option.field_id, []).append(
            {"value": option.value, "label": option.label, "position": option.position}
        )
    return {
        "settings": version.settings,
        "sections": [
            {
                "key": section.key,
                "title": section.title,
                "description": section.description,
                "position": section.position,
            }
            for section in sections
        ],
        "fields": [
            {
                "section_id": str(field.section_id),
                "key": field.key,
                "label": field.label,
                "field_type": field.field_type,
                "required": field.required,
                "help_text": field.help_text,
                "settings": field.settings,
                "validation": field.validation,
                "position": field.position,
                "options": by_field.get(field.id, []),
            }
            for field in fields
        ],
    }


def publish_version(
    db: Session,
    *,
    tenant_id: UUID,
    version_id: UUID,
    published_at: datetime,
) -> FormVersion:
    if published_at.utcoffset() is None:
        raise ValueError("published_at must be timezone-aware")
    version = _draft(db, tenant_id, version_id)
    payload = _definition_payload(db, version)
    sections = payload["sections"]
    fields = payload["fields"]
    if not isinstance(sections, list) or not sections:
        raise FormValidationError("a published form requires a section")
    if not isinstance(fields, list) or not fields:
        raise FormValidationError("a published form requires a field")
    for field in fields:
        if not isinstance(field, dict):
            raise FormValidationError("form field definition is malformed")
        if FieldType(str(field["field_type"])) in CHOICE_TYPES and not field["options"]:
            raise FormValidationError(f"choice field {field['key']} requires an option")
    version.content_digest = fingerprint(payload)
    version.published_at = published_at
    version.status = "published"
    form = _form(db, tenant_id, version.form_id)
    form.published_version_id = version.id
    form.updated_at = published_at
    db.flush()
    return version


def _blank(value: AnswerValue) -> bool:
    return value is None or value == "" or value == ()


def _review(
    field: FormField, value: AnswerValue, options: dict[str, str]
) -> ReviewedAnswer:
    kind = FieldType(field.field_type)
    if _blank(value):
        if field.required:
            raise FormValidationError(f"{field.key} is required")
        return ReviewedAnswer(None, None)
    if kind in {FieldType.TEXT, FieldType.LONG_TEXT, FieldType.PHONE}:
        if not isinstance(value, str):
            raise FormValidationError(f"{field.key} must be text")
        return ReviewedAnswer(value.strip(), value.strip())
    if kind is FieldType.EMAIL:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value.strip()) is None
        ):
            raise FormValidationError(f"{field.key} must be a valid email")
        normalized = value.strip().lower()
        return ReviewedAnswer(normalized, normalized)
    if kind is FieldType.URL:
        if not isinstance(value, str):
            raise FormValidationError(f"{field.key} must be a URL")
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise FormValidationError(f"{field.key} must be a valid URL")
        return ReviewedAnswer(value.strip(), value.strip())
    if kind is FieldType.NUMBER:
        try:
            number = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise FormValidationError(f"{field.key} must be a number") from exc
        normalized = format(number, "f")
        return ReviewedAnswer(normalized, normalized)
    if kind is FieldType.DATE:
        if not isinstance(value, str):
            raise FormValidationError(f"{field.key} must be an ISO date")
        try:
            parsed_date = date.fromisoformat(value)
        except ValueError as exc:
            raise FormValidationError(f"{field.key} must be an ISO date") from exc
        return ReviewedAnswer(parsed_date.isoformat(), parsed_date.isoformat())
    if kind in {FieldType.SINGLE_CHOICE, FieldType.DROPDOWN}:
        if not isinstance(value, str) or value not in options:
            raise FormValidationError(f"{field.key} must name a declared option")
        return ReviewedAnswer(value, options[value])
    if kind is FieldType.MULTI_CHOICE:
        if (
            not isinstance(value, tuple)
            or not value
            or any(v not in options for v in value)
        ):
            raise FormValidationError(f"{field.key} must name declared options")
        selected = tuple(dict.fromkeys(value))
        return ReviewedAnswer(list(selected), ", ".join(options[v] for v in selected))
    if kind in {FieldType.CHECKBOX, FieldType.YES_NO, FieldType.CONSENT}:
        if not isinstance(value, bool):
            raise FormValidationError(f"{field.key} must be true or false")
        return ReviewedAnswer(value, "Yes" if value else "No")
    if kind is FieldType.RATING:
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 5:
            raise FormValidationError(f"{field.key} must be a rating from 1 to 5")
        return ReviewedAnswer(value, str(value))
    if kind in FILE_TYPES:
        if not isinstance(value, str) or not value.strip():
            raise FormValidationError(f"{field.key} must be an opaque file reference")
        ref = value.strip()
        return ReviewedAnswer(ref, ref, ref)
    raise FormValidationError(f"unsupported field type {kind.value}")


def submit_form(
    db: Session, *, tenant_id: UUID, request: SubmissionRequest
) -> SubmissionReceipt:
    version = _version(db, tenant_id, request.form_version_id)
    if version.status != "published" or version.content_digest is None:
        raise FormUnavailable("only a published form version accepts submissions")
    fields = db.scalars(
        select(FormField)
        .where(
            FormField.tenant_id == tenant_id,
            FormField.version_id == version.id,
        )
        .order_by(FormField.position, FormField.key)
    ).all()
    field_by_key = {field.key: field for field in fields}
    supplied = {answer.field_key: answer for answer in request.answers}
    unknown = sorted(set(supplied) - set(field_by_key))
    if unknown:
        raise FormValidationError(f"unknown form field: {unknown[0]}")
    option_rows = db.scalars(
        select(FormFieldOption)
        .join(
            FormField,
            (FormField.tenant_id == FormFieldOption.tenant_id)
            & (FormField.id == FormFieldOption.field_id),
        )
        .where(
            FormField.tenant_id == tenant_id,
            FormField.version_id == version.id,
            FormFieldOption.active.is_(True),
        )
    ).all()
    options: dict[UUID, dict[str, str]] = {}
    for option_row in option_rows:
        options.setdefault(option_row.field_id, {})[option_row.value] = option_row.label
    reviewed: list[tuple[FormField, ReviewedAnswer]] = []
    for field in fields:
        answer = supplied.get(field.key, AnswerInput(field.key, None))
        reviewed.append(
            (field, _review(field, answer.value, options.get(field.id, {})))
        )
    request_fingerprint = fingerprint(
        {
            "form_version_id": str(version.id),
            "version_digest": version.content_digest,
            "subject_ref": request.subject_ref,
            "answers": [(a.field_key, a.value) for a in request.answers],
        }
    )
    existing = db.scalar(
        select(FormSubmission).where(
            FormSubmission.tenant_id == tenant_id,
            FormSubmission.submission_key == request.submission_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint != request_fingerprint:
            raise SubmissionConflict("submission key was reused with different content")
        return SubmissionReceipt(existing, replayed=True)
    submission = FormSubmission(
        tenant_id=tenant_id,
        submission_key=request.submission_key,
        form_version_id=version.id,
        subject_ref=request.subject_ref,
        submitted_by_ref=request.submitted_by_ref,
        request_fingerprint=request_fingerprint,
        status="submitted",
        submitted_at=request.submitted_at,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(submission)
            db.flush()
    except IntegrityError as exc:
        replay = db.scalar(
            select(FormSubmission).where(
                FormSubmission.tenant_id == tenant_id,
                FormSubmission.submission_key == request.submission_key,
            )
        )
        if replay is not None and replay.request_fingerprint == request_fingerprint:
            return SubmissionReceipt(replay, replayed=True)
        raise SubmissionConflict("submission identity conflicts") from exc
    db.add_all(
        [
            FormAnswer(
                tenant_id=tenant_id,
                submission_id=submission.id,
                field_id=field.id,
                field_key_snapshot=field.key,
                field_label_snapshot=field.label,
                field_type_snapshot=field.field_type,
                value_json=answer.value,
                display_value=answer.display,
                file_ref=answer.file_ref,
            )
            for field, answer in reviewed
            if answer.value is not None
        ]
    )
    db.flush()
    return SubmissionReceipt(submission)


__all__ = [
    "FormError",
    "FormUnavailable",
    "FormValidationError",
    "SubmissionConflict",
    "SubmissionReceipt",
    "add_field",
    "add_option",
    "add_section",
    "create_draft_version",
    "create_form",
    "publish_version",
    "submit_form",
]
