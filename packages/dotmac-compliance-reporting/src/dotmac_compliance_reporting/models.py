"""Tenant regulatory obligation, pack and filing evidence."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import JSON, Date, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

SCHEMA = module_schema("compliance")


def tenant_id_column() -> Mapped[UUID]:
    return mapped_column(Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False)


class ReportingObligation(Base, TimestampMixin):
    __tablename__ = "reporting_obligations"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_reporting_obligations_tenant_id_id"), UniqueConstraint("tenant_id", "code", name="uq_reporting_obligations_code"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column()
    code: Mapped[str] = mapped_column(String(120), nullable=False); jurisdiction: Mapped[str] = mapped_column(String(120), nullable=False); title: Mapped[str] = mapped_column(String(240), nullable=False); active: Mapped[bool] = mapped_column(nullable=False, default=True)


class ClassificationRevision(Base):
    __tablename__ = "classification_revisions"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_classification_revisions_tenant_id_id"), UniqueConstraint("tenant_id", "obligation_id", "version", name="uq_classification_revisions_version"), ForeignKeyConstraint(["tenant_id", "obligation_id"], [f"{SCHEMA}.reporting_obligations.tenant_id", f"{SCHEMA}.reporting_obligations.id"], ondelete="RESTRICT"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); obligation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False); section_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False); content_digest: Mapped[str] = mapped_column(String(64), nullable=False); effective_from: Mapped[date] = mapped_column(Date, nullable=False); published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EvidencePack(Base, TimestampMixin):
    __tablename__ = "evidence_packs"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_evidence_packs_tenant_id_id"), UniqueConstraint("tenant_id", "obligation_id", "period_start", "period_end", name="uq_evidence_packs_period"), ForeignKeyConstraint(["tenant_id", "obligation_id"], [f"{SCHEMA}.reporting_obligations.tenant_id", f"{SCHEMA}.reporting_obligations.id"], ondelete="RESTRICT"), ForeignKeyConstraint(["tenant_id", "classification_revision_id"], [f"{SCHEMA}.classification_revisions.tenant_id", f"{SCHEMA}.classification_revisions.id"], ondelete="RESTRICT"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); obligation_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False); classification_revision_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False); period_end: Mapped[date] = mapped_column(Date, nullable=False); pack_digest: Mapped[str] = mapped_column(String(64), nullable=False); status: Mapped[str] = mapped_column(String(24), nullable=False, default="assembled"); assembled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sections: Mapped[list[EvidenceSection]] = relationship(lambda: EvidenceSection, order_by=lambda: EvidenceSection.section_code)


class EvidenceSection(Base):
    __tablename__ = "evidence_sections"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_evidence_sections_tenant_id_id"), UniqueConstraint("tenant_id", "pack_id", "section_code", name="uq_evidence_sections_pack_code"), ForeignKeyConstraint(["tenant_id", "pack_id"], [f"{SCHEMA}.evidence_packs.tenant_id", f"{SCHEMA}.evidence_packs.id"], ondelete="CASCADE"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); pack_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    section_code: Mapped[str] = mapped_column(String(120), nullable=False); source_owner: Mapped[str] = mapped_column(String(120), nullable=False); state: Mapped[str] = mapped_column(String(20), nullable=False); evidence_ref: Mapped[str | None] = mapped_column(String(240)); evidence_digest: Mapped[str | None] = mapped_column(String(64)); unavailable_reason: Mapped[str | None] = mapped_column(Text)


class FilingSubmission(Base, TimestampMixin):
    __tablename__ = "filing_submissions"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_filing_submissions_tenant_id_id"), UniqueConstraint("tenant_id", "pack_id", name="uq_filing_submissions_pack"), UniqueConstraint("tenant_id", "submission_ref", name="uq_filing_submissions_ref"), ForeignKeyConstraint(["tenant_id", "pack_id"], [f"{SCHEMA}.evidence_packs.tenant_id", f"{SCHEMA}.evidence_packs.id"], ondelete="RESTRICT"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); pack_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False); submitted_pack_digest: Mapped[str] = mapped_column(String(64), nullable=False); submission_ref: Mapped[str] = mapped_column(String(240), nullable=False); status: Mapped[str] = mapped_column(String(24), nullable=False, default="submitted"); submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RegulatorAcknowledgement(Base):
    __tablename__ = "regulator_acknowledgements"
    __table_args__ = (UniqueConstraint("tenant_id", "id", name="uq_regulator_acknowledgements_tenant_id_id"), UniqueConstraint("tenant_id", "acknowledgement_key", name="uq_regulator_acknowledgements_key"), ForeignKeyConstraint(["tenant_id", "submission_id"], [f"{SCHEMA}.filing_submissions.tenant_id", f"{SCHEMA}.filing_submissions.id"], ondelete="RESTRICT"), schema_table_args(SCHEMA))
    id: Mapped[UUID] = uuid_pk(); tenant_id: Mapped[UUID] = tenant_id_column(); submission_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False); acknowledgement_key: Mapped[str] = mapped_column(String(200), nullable=False); outcome: Mapped[str] = mapped_column(String(24), nullable=False); acknowledged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False); evidence_ref: Mapped[str] = mapped_column(String(240), nullable=False)


TENANT_MODELS = (ReportingObligation, ClassificationRevision, EvidencePack, EvidenceSection, FilingSubmission, RegulatorAcknowledgement)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)
__all__ = ["ClassificationRevision", "EvidencePack", "EvidenceSection", "FilingSubmission", "RegulatorAcknowledgement", "ReportingObligation", "SCHEMA", "TENANT_MODELS", "TENANT_TABLES"]
