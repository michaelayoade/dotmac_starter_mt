"""Tenant-only persistence for reusable form definitions and submissions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("forms")


def tenant_id_column() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class Form(Base, TimestampMixin):
    __tablename__ = "forms"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_forms_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "published_version_id"],
            [f"{SCHEMA}.form_versions.tenant_id", f"{SCHEMA}.form_versions.id"],
            name="fk_forms_published_version",
            use_alter=True,
        ),
        Index("ix_forms_tenant_type", "tenant_id", "form_type"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    form_type: Mapped[str] = mapped_column(String(80), nullable=False)
    owner_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    published_version_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


class FormVersion(Base):
    __tablename__ = "form_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_form_versions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "form_id",
            "version_number",
            name="uq_form_versions_tenant_form_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "form_id"],
            [f"{SCHEMA}.forms.tenant_id", f"{SCHEMA}.forms.id"],
            ondelete="CASCADE",
            name="fk_form_versions_form",
        ),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_form_versions_status",
        ),
        CheckConstraint("version_number > 0", name="ck_form_versions_number"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    form_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    settings: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    content_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FormSection(Base):
    __tablename__ = "form_sections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_form_sections_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "version_id",
            "id",
            name="uq_form_sections_tenant_version_id",
        ),
        UniqueConstraint(
            "tenant_id", "version_id", "key", name="uq_form_sections_version_key"
        ),
        UniqueConstraint(
            "tenant_id",
            "version_id",
            "position",
            name="uq_form_sections_version_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [f"{SCHEMA}.form_versions.tenant_id", f"{SCHEMA}.form_versions.id"],
            ondelete="CASCADE",
            name="fk_form_sections_version",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class FormField(Base):
    __tablename__ = "form_fields"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_form_fields_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "version_id", "key", name="uq_form_fields_version_key"
        ),
        UniqueConstraint(
            "tenant_id",
            "version_id",
            "section_id",
            "position",
            name="uq_form_fields_section_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            [f"{SCHEMA}.form_versions.tenant_id", f"{SCHEMA}.form_versions.id"],
            ondelete="CASCADE",
            name="fk_form_fields_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "version_id", "section_id"],
            [
                f"{SCHEMA}.form_sections.tenant_id",
                f"{SCHEMA}.form_sections.version_id",
                f"{SCHEMA}.form_sections.id",
            ],
            ondelete="CASCADE",
            name="fk_form_fields_section",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    section_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    field_type: Mapped[str] = mapped_column(String(32), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    help_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    validation: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class FormFieldOption(Base):
    __tablename__ = "form_field_options"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_form_field_options_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "field_id",
            "value",
            name="uq_form_field_options_field_value",
        ),
        UniqueConstraint(
            "tenant_id",
            "field_id",
            "position",
            name="uq_form_field_options_field_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "field_id"],
            [f"{SCHEMA}.form_fields.tenant_id", f"{SCHEMA}.form_fields.id"],
            ondelete="CASCADE",
            name="fk_form_field_options_field",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    field_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    value: Mapped[str] = mapped_column(String(160), nullable=False)
    label: Mapped[str] = mapped_column(String(240), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class FormSubmission(Base):
    __tablename__ = "form_submissions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_form_submissions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "submission_key", name="uq_form_submissions_tenant_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "form_version_id"],
            [f"{SCHEMA}.form_versions.tenant_id", f"{SCHEMA}.form_versions.id"],
            ondelete="RESTRICT",
            name="fk_form_submissions_version",
        ),
        Index("ix_form_submissions_tenant_subject", "tenant_id", "subject_ref"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    submission_key: Mapped[str] = mapped_column(String(255), nullable=False)
    form_version_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    subject_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    submitted_by_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="submitted")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FormAnswer(Base):
    __tablename__ = "form_answers"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_form_answers_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "submission_id",
            "field_id",
            name="uq_form_answers_submission_field",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "submission_id"],
            [f"{SCHEMA}.form_submissions.tenant_id", f"{SCHEMA}.form_submissions.id"],
            ondelete="CASCADE",
            name="fk_form_answers_submission",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "field_id"],
            [f"{SCHEMA}.form_fields.tenant_id", f"{SCHEMA}.form_fields.id"],
            ondelete="RESTRICT",
            name="fk_form_answers_field",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    submission_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    field_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    field_key_snapshot: Mapped[str] = mapped_column(String(80), nullable=False)
    field_label_snapshot: Mapped[str] = mapped_column(String(240), nullable=False)
    field_type_snapshot: Mapped[str] = mapped_column(String(32), nullable=False)
    value_json: Mapped[object | None] = mapped_column(JSON, nullable=True)
    display_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)


TENANT_MODELS = (
    Form,
    FormVersion,
    FormSection,
    FormField,
    FormFieldOption,
    FormSubmission,
    FormAnswer,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "Form",
    "FormAnswer",
    "FormField",
    "FormFieldOption",
    "FormSection",
    "FormSubmission",
    "FormVersion",
]
