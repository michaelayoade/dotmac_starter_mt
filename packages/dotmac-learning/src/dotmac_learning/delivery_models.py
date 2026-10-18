"""Academy 096 delivery/support storage, with genuine Kernel person translation.

Migrated constraints and defaults are authoritative. Logical instructor, offering,
cohort and support actor references remain unconstrained where the source did so.
Identity translation follows Enrollment/Event; it does not prove product adoption.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base as KernelBase
from dotmac_kernel.models import Party, PartyPerson, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import schema_table_args
from pydantic import JsonValue
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import SCHEMA

SESSION_TYPES = ("live_class", "lab", "lecture", "other")
SESSION_STATUSES = ("scheduled", "cancelled", "completed")

REASON_INACTIVITY = "inactivity"
REASON_OVERDUE_WORK = "overdue_work"
REASON_BELOW_PASSING = "below_passing"
REASON_FAILED_FINAL = "failed_final"
REASON_CERTIFICATE_BLOCKED = "certificate_blocked"
REASON_KINDS = (
    REASON_INACTIVITY,
    REASON_OVERDUE_WORK,
    REASON_BELOW_PASSING,
    REASON_FAILED_FINAL,
    REASON_CERTIFICATE_BLOCKED,
)
STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_RESOLVED = "resolved"


class OfferingActivity(KernelBase, TimestampMixin):
    """Per-offering pacing override for one activity.

    Optional. With no row, an activity follows the offering's own window. A future
    ``release_at`` hides/blocks the activity; a past ``due_at`` blocks *submission*
    (reading is still allowed). Enables "week 3 opens Monday / quiz due Friday".
    """

    __tablename__ = "offering_activities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_offering_activities_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "offering_id",
            "activity_id",
            name="uq_offering_activities_offering_activity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "offering_id"],
            [
                "mod_learning.course_offerings.tenant_id",
                "mod_learning.course_offerings.id",
            ],
            ondelete="CASCADE",
            name="fk_offering_activities_tenant_offering",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "activity_id"],
            ["mod_learning.activities.tenant_id", "mod_learning.activities.id"],
            ondelete="CASCADE",
            name="fk_offering_activities_tenant_activity",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    offering_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    activity_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassSession(KernelBase, TimestampMixin):
    __tablename__ = "class_sessions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_class_sessions_tenant_id_id"),
        # tenant-consistent composite FK to the owning cohort
        ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_class_sessions_tenant_cohort",
        ),
        Index("ix_class_sessions_cohort_start", "tenant_id", "cohort_id", "starts_at"),
        Index("ix_class_sessions_instructor", "tenant_id", "instructor_person_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cohort_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Optional link to a specific course run; NULL = a cohort-wide session
    # (orientation, review) not tied to one course.
    offering_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    session_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="live_class"
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The person teaching/running it (a role_in_cohort='instructor' enrollment).
    instructor_person_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    location: Mapped[str | None] = mapped_column(
        String(160), nullable=True
    )  # room / venue
    join_url: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )  # virtual link
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="scheduled"
    )


class Announcement(KernelBase, TimestampMixin):
    __tablename__ = "announcements"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_announcements_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_announcements_tenant_cohort",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "author_person_id"],
            [Party.__table__.c.tenant_id, Party.__table__.c.id],
            ondelete="CASCADE",
            name="fk_announcements_tenant_author",
        ),
        ForeignKeyConstraint(
            ["author_person_id"],
            [PartyPerson.__table__.c.party_id],
            ondelete="CASCADE",
            name="fk_announcements_party_person",
        ),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    cohort_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    author_person_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)


class SuccessQueueEntry(KernelBase, TimestampMixin):
    """Explainable intervention history; only open person/reason pairs are unique."""

    __tablename__ = "success_queue_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_success_queue_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            [Party.__table__.c.tenant_id, Party.__table__.c.id],
            ondelete="CASCADE",
            name="fk_success_queue_tenant_person",
        ),
        ForeignKeyConstraint(
            ["person_id"],
            [PartyPerson.__table__.c.party_id],
            ondelete="CASCADE",
            name="fk_success_queue_party_person",
        ),
        # Source 0046 DDL is authoritative over the incomplete source ORM.
        Index("ix_success_queue_status", "tenant_id", "status", "severity"),
        Index(
            "uq_success_queue_open_person_reason",
            "tenant_id",
            "person_id",
            "reason_kind",
            unique=True,
            postgresql_where=text("status = 'open'"),
            sqlite_where=text("status = 'open'"),
        ),
        Index("ix_success_queue_tenant_id", "tenant_id"),
        Index("ix_success_queue_person_id", "person_id"),
        Index("ix_success_queue_cohort_id", "cohort_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    cohort_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    reason_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    supporting_facts: Mapped[JsonValue] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=dict,
        # PostgreSQL supplies the implicit cast; the migration uses explicit ::jsonb.
        server_default=text("'{}'"),
    )
    severity: Mapped[str] = mapped_column(
        String(10), nullable=False, default="medium", server_default="medium"
    )
    # Freshness weight for ordering; refreshed by every sweep that re-confirms the rule.
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    assigned_to: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    recommended_action: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )
    status: Mapped[str] = mapped_column(
        String(15), nullable=False, default=STATUS_OPEN, server_default=STATUS_OPEN
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    # Kept for ordering heuristics; not a decision input.
    score_hint: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0, server_default="0"
    )


DELIVERY_MODELS = (OfferingActivity, ClassSession, Announcement, SuccessQueueEntry)
DELIVERY_TABLES = tuple(model.__tablename__ for model in DELIVERY_MODELS)

__all__ = [
    "DELIVERY_MODELS",
    "DELIVERY_TABLES",
    "SESSION_TYPES",
    "SESSION_STATUSES",
    "REASON_INACTIVITY",
    "REASON_OVERDUE_WORK",
    "REASON_BELOW_PASSING",
    "REASON_FAILED_FINAL",
    "REASON_CERTIFICATE_BLOCKED",
    "REASON_KINDS",
    "STATUS_OPEN",
    "STATUS_ACKNOWLEDGED",
    "STATUS_RESOLVED",
    "OfferingActivity",
    "ClassSession",
    "Announcement",
    "SuccessQueueEntry",
]
