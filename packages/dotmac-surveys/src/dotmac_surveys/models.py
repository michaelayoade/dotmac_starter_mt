"""Tenant-scoped persistence for reusable feedback mechanics."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("surveys")


class Survey(Base, TimestampMixin):
    __tablename__ = "surveys"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_surveys_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "public_slug", name="uq_surveys_tenant_public_slug"
        ),
        Index("ix_surveys_tenant_status", "tenant_id", "status"),
        CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'closed')",
            name="ck_surveys_status",
        ),
        CheckConstraint("total_invited >= 0", name="ck_surveys_invited_nonnegative"),
        CheckConstraint(
            "total_responses >= 0", name="ck_surveys_responses_nonnegative"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    questions: Mapped[list[dict[str, object]]] = mapped_column(
        JSON, nullable=False, default=list
    )
    public_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)
    thank_you_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    total_invited: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_responses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_rating: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    nps_score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)


class SurveyInvitation(Base, TimestampMixin):
    __tablename__ = "survey_invitations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "survey_id"],
            [f"{SCHEMA}.surveys.tenant_id", f"{SCHEMA}.surveys.id"],
            name="fk_survey_invitations_survey",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_survey_invitations_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "survey_id",
            "id",
            name="uq_survey_invitations_survey_id_id",
        ),
        UniqueConstraint(
            "tenant_id", "token", name="uq_survey_invitations_tenant_token"
        ),
        UniqueConstraint(
            "tenant_id",
            "survey_id",
            "recipient_ref",
            "source_owner",
            "source_event_id",
            name="uq_survey_invitations_source_recipient",
        ),
        Index(
            "ix_survey_invitations_tenant_survey",
            "tenant_id",
            "survey_id",
            "status",
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'expired')",
            name="ck_survey_invitations_status",
        ),
        CheckConstraint(
            "length(trim(token)) > 0", name="ck_survey_invitations_token_not_blank"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    survey_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    recipient_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    token: Mapped[str] = mapped_column(String(80), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(200), nullable=False)
    subject_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SurveyResponse(Base, TimestampMixin):
    __tablename__ = "survey_responses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "survey_id"],
            [f"{SCHEMA}.surveys.tenant_id", f"{SCHEMA}.surveys.id"],
            name="fk_survey_responses_survey",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "survey_id", "invitation_id"],
            [
                f"{SCHEMA}.survey_invitations.tenant_id",
                f"{SCHEMA}.survey_invitations.survey_id",
                f"{SCHEMA}.survey_invitations.id",
            ],
            name="fk_survey_responses_invitation",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_survey_responses_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "invitation_id",
            name="uq_survey_responses_tenant_invitation",
        ),
        Index("ix_survey_responses_tenant_survey", "tenant_id", "survey_id"),
        CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_survey_responses_rating",
        ),
        CheckConstraint(
            "nps_value IS NULL OR (nps_value >= 0 AND nps_value <= 10)",
            name="ck_survey_responses_nps",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    survey_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    invitation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    answers: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nps_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_MODELS = (Survey, SurveyInvitation, SurveyResponse)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "Survey",
    "SurveyInvitation",
    "SurveyResponse",
]
