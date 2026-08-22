"""Workforce persistence contract."""

from __future__ import annotations

from datetime import datetime
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
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

SCHEMA = module_schema("workforce")


class WorkforceTeam(Base, TimestampMixin):
    __tablename__ = "workforce_teams"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workforce_teams_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_workforce_teams_tenant_code"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)


class WorkforceSkill(Base, TimestampMixin):
    __tablename__ = "workforce_skills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workforce_skills_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_workforce_skills_tenant_code"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)


class TeamMembership(Base, TimestampMixin):
    __tablename__ = "team_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_team_memberships_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "team_id",
            "worker_reference",
            name="uq_team_memberships_tenant_team_worker",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            [f"{SCHEMA}.workforce_teams.tenant_id", f"{SCHEMA}.workforce_teams.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_tenant_team",
        ),
        Index("ix_team_memberships_tenant_worker", "tenant_id", "worker_reference"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    worker_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WorkerSkill(Base, TimestampMixin):
    __tablename__ = "worker_skills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_worker_skills_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "worker_reference",
            "skill_id",
            name="uq_worker_skills_tenant_worker_skill",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            [f"{SCHEMA}.workforce_skills.tenant_id", f"{SCHEMA}.workforce_skills.id"],
            ondelete="CASCADE",
            name="fk_worker_skills_tenant_skill",
        ),
        CheckConstraint(
            "proficiency BETWEEN 1 AND 5", name="ck_worker_skills_proficiency"
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    worker_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    skill_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    proficiency: Mapped[int] = mapped_column(Integer(), nullable=False)
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class WorkforceShift(Base, TimestampMixin):
    __tablename__ = "workforce_shifts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workforce_shifts_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            [f"{SCHEMA}.workforce_teams.tenant_id", f"{SCHEMA}.workforce_teams.id"],
            ondelete="CASCADE",
            name="fk_workforce_shifts_tenant_team",
        ),
        Index(
            "ix_workforce_shifts_tenant_team_start",
            "tenant_id",
            "team_id",
            "starts_at",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_workforce_shifts_window"),
        CheckConstraint("capacity > 0", name="ck_workforce_shifts_capacity"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    team_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer(), nullable=False)


class WorkforceAvailability(Base, TimestampMixin):
    __tablename__ = "workforce_availability"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_workforce_availability_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "worker_reference",
            "starts_at",
            "ends_at",
            name="uq_workforce_availability_tenant_worker_window",
        ),
        Index(
            "ix_workforce_availability_tenant_worker_start",
            "tenant_id",
            "worker_reference",
            "starts_at",
        ),
        CheckConstraint("ends_at > starts_at", name="ck_workforce_availability_window"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    worker_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(160), nullable=False)


class DispatchDecision(Base, TimestampMixin):
    __tablename__ = "dispatch_decisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_dispatch_decisions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "work_reference",
            name="uq_dispatch_decisions_tenant_work",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            [f"{SCHEMA}.workforce_teams.tenant_id", f"{SCHEMA}.workforce_teams.id"],
            name="fk_dispatch_decisions_tenant_team",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "shift_id"],
            [f"{SCHEMA}.workforce_shifts.tenant_id", f"{SCHEMA}.workforce_shifts.id"],
            name="fk_dispatch_decisions_tenant_shift",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "required_skill_id"],
            [f"{SCHEMA}.workforce_skills.tenant_id", f"{SCHEMA}.workforce_skills.id"],
            name="fk_dispatch_decisions_tenant_skill",
        ),
        Index(
            "ix_dispatch_decisions_tenant_worker_time",
            "tenant_id",
            "worker_reference",
            "scheduled_for",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    work_reference: Mapped[str] = mapped_column(String(180), nullable=False)
    team_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    worker_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    required_skill_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    shift_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False)


TENANT_TABLES = (
    "workforce_teams",
    "workforce_skills",
    "team_memberships",
    "worker_skills",
    "workforce_shifts",
    "workforce_availability",
    "dispatch_decisions",
)
_TABLES: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (
        WorkforceTeam,
        WorkforceSkill,
        TeamMembership,
        WorkerSkill,
        WorkforceShift,
        WorkforceAvailability,
        DispatchDecision,
    )
}


def metadata_table(name: str) -> sa.Table:
    return _TABLES[name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "DispatchDecision",
    "TeamMembership",
    "WorkerSkill",
    "WorkforceAvailability",
    "WorkforceShift",
    "WorkforceSkill",
    "WorkforceTeam",
    "metadata_table",
]
