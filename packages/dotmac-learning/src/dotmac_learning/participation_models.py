"""Academy 096 participation storage with explicit fresh Kernel identity references.

Enrollment preserves stable learner UUIDs through tenant-safe Party and genuine
PartyPerson existence, following the event owner. This is an identity translation,
not Academy identity adoption or literal legacy People DDL parity.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import (
    Base,
    Party,
    PartyPerson,
    Tenant,
    TimestampMixin,
    uuid_pk,
)
from dotmac_kernel.namespaces import schema_table_args
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import SCHEMA


class Cohort(Base, TimestampMixin):
    __tablename__ = "cohorts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_cohorts_tenant_id_id"),
        Index("ix_cohorts_tenant_id", "tenant_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    discipline: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    delivery_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="self_paced"
    )
    # Logical source UUID; entrance policy does not imply a bank foreign key.
    entrance_bank_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    entrance_time_limit_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    auto_accept_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)


class Enrollment(Base, TimestampMixin):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "cohort_id", "person_id", name="uq_enrollments_member"
        ),
        # Source default NO ACTION / MATCH SIMPLE; NULL track remains valid.
        ForeignKeyConstraint(
            ["tenant_id", "cohort_id", "track_id"],
            [
                "mod_learning.cohort_tracks.tenant_id",
                "mod_learning.cohort_tracks.cohort_id",
                "mod_learning.cohort_tracks.track_id",
            ],
            name="fk_enrollments_tenant_cohort_track",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_enrollments_tenant_cohort",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            [Party.__table__.c.tenant_id, Party.__table__.c.id],
            ondelete="CASCADE",
            name="fk_enrollments_tenant_person",
        ),
        ForeignKeyConstraint(
            ["person_id"],
            [PartyPerson.__table__.c.party_id],
            ondelete="CASCADE",
            name="fk_enrollments_party_person",
        ),
        CheckConstraint(
            "audience IS NULL OR audience IN ('staff', 'external')",
            name="ck_enrollments_audience",
        ),
        CheckConstraint(
            "audience IS DISTINCT FROM 'staff' OR employee_ref IS NOT NULL",
            name="ck_enrollments_staff_has_employee_ref",
        ),
        # Actual 0049/0050 migrated indexes override the incomplete source ORM.
        Index("ix_enrollments_audience", "tenant_id", "audience"),
        Index("ix_enrollments_access_ends_at", "tenant_id", "access_ends_at"),
        Index("ix_enrollments_tenant_id", "tenant_id"),
        Index("ix_enrollments_cohort_id", "cohort_id"),
        Index("ix_enrollments_person_id", "person_id"),
        Index("ix_enrollments_track_id", "track_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    cohort_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    track_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    role_in_cohort: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    audience: Mapped[str | None] = mapped_column(String(20), nullable=True)
    employee_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    access_ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Track(Base, TimestampMixin):
    """Reusable curriculum path made up of ordered courses."""

    __tablename__ = "tracks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_tracks_tenant_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_tracks_tenant_id_id"),
        Index("ix_tracks_tenant_id", "tenant_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )


class TrackCourse(Base, TimestampMixin):
    """Ordered course membership for a track."""

    __tablename__ = "track_courses"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "track_id", "course_id", name="uq_track_courses_track_course"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "track_id"],
            ["mod_learning.tracks.tenant_id", "mod_learning.tracks.id"],
            ondelete="CASCADE",
            name="fk_track_courses_tenant_track",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
            ondelete="CASCADE",
            name="fk_track_courses_tenant_course",
        ),
        Index("ix_track_courses_tenant_id", "tenant_id"),
        Index("ix_track_courses_track_id", "track_id"),
        Index("ix_track_courses_course_id", "course_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    track_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    course_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    order_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class CohortTrack(Base, TimestampMixin):
    """A track enabled for a cohort."""

    __tablename__ = "cohort_tracks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_cohort_tracks_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "cohort_id", "track_id", name="uq_cohort_tracks_cohort_track"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_cohort_tracks_tenant_cohort",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "track_id"],
            ["mod_learning.tracks.tenant_id", "mod_learning.tracks.id"],
            ondelete="CASCADE",
            name="fk_cohort_tracks_tenant_track",
        ),
        Index("ix_cohort_tracks_tenant_id", "tenant_id"),
        Index("ix_cohort_tracks_cohort_id", "cohort_id"),
        Index("ix_cohort_tracks_track_id", "track_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    cohort_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    track_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )


class CourseOffering(Base, TimestampMixin):
    """Explicit link: a Cohort studies a Course.

    Replaces discipline-string matching as the entitlement source. A person can
    access a course iff an active Enrollment ties them to a Cohort that has an
    active CourseOffering for that course. Slice 2 adds scheduling columns here.
    """

    __tablename__ = "course_offerings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_course_offerings_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "cohort_id",
            "course_id",
            name="uq_course_offerings_cohort_course",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_course_offerings_tenant_cohort",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
            ondelete="CASCADE",
            name="fk_course_offerings_tenant_course",
        ),
        Index("ix_course_offerings_tenant_id", "tenant_id"),
        Index("ix_course_offerings_cohort_id", "cohort_id"),
        Index("ix_course_offerings_course_id", "course_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    cohort_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    course_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoursePrerequisite(Base, TimestampMixin):
    """``course_id`` requires ``requires_course_id`` to be completed first."""

    __tablename__ = "course_prerequisites"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_course_prerequisites_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "course_id",
            "requires_course_id",
            name="uq_course_prerequisites_pair",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
            ondelete="CASCADE",
            name="fk_course_prerequisites_tenant_course",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requires_course_id"],
            ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
            ondelete="CASCADE",
            name="fk_course_prerequisites_tenant_requires",
        ),
        Index("ix_course_prerequisites_tenant_id", "tenant_id"),
        Index("ix_course_prerequisites_course_id", "course_id"),
        Index("ix_course_prerequisites_requires_course_id", "requires_course_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    course_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    requires_course_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )


PARTICIPATION_MODELS = (
    Cohort,
    Enrollment,
    Track,
    TrackCourse,
    CohortTrack,
    CourseOffering,
    CoursePrerequisite,
)
PARTICIPATION_TABLES = tuple(model.__tablename__ for model in PARTICIPATION_MODELS)

__all__ = [
    "PARTICIPATION_MODELS",
    "PARTICIPATION_TABLES",
    "Cohort",
    "Enrollment",
    "Track",
    "TrackCourse",
    "CohortTrack",
    "CourseOffering",
    "CoursePrerequisite",
]
