"""Qualification persistence contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_qualification.contracts import DecisionOutcome

SCHEMA = module_schema("qual")


class QualificationCase(Base, TimestampMixin):
    __tablename__ = "qualification_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_qualification_cases_tenant_id_id"),
        Index(
            "ix_qualification_cases_tenant_subject", "tenant_id", "subject_reference"
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    subject_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    specification_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class QualificationEvidence(Base, TimestampMixin):
    __tablename__ = "qualification_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_qualification_evidence_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                f"{SCHEMA}.qualification_cases.tenant_id",
                f"{SCHEMA}.qualification_cases.id",
            ],
            ondelete="CASCADE",
            name="fk_qualification_evidence_tenant_case",
        ),
        Index(
            "ix_qualification_evidence_tenant_case_valid",
            "tenant_id",
            "case_id",
            "valid_until",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    source_type: Mapped[str] = mapped_column(String(60), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    facts: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class QualificationDecision(Base, TimestampMixin):
    __tablename__ = "qualification_decisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_qualification_decisions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "case_id", name="uq_qualification_decisions_tenant_case"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                f"{SCHEMA}.qualification_cases.tenant_id",
                f"{SCHEMA}.qualification_cases.id",
            ],
            ondelete="CASCADE",
            name="fk_qualification_decisions_tenant_case",
        ),
        Index("ix_qualification_decisions_tenant_expiry", "tenant_id", "expires_at"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    outcome: Mapped[DecisionOutcome] = mapped_column(
        sa.Enum(
            DecisionOutcome,
            name="qualification_decision_outcome",
            native_enum=False,
            values_callable=lambda cls: [member.value for member in cls],
            create_constraint=True,
        ),
        nullable=False,
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)


TENANT_TABLES = (
    "qualification_cases",
    "qualification_evidence",
    "qualification_decisions",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (QualificationCase, QualificationEvidence, QualificationDecision)
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "QualificationCase",
    "QualificationDecision",
    "QualificationEvidence",
    "metadata_table",
]
