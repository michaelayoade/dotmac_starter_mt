"""Reusable learning records, preserving the Academy first-slice contracts.

Identity references are deliberately source-specific: only learning events
require a genuine tenant-scoped person. Product ERP delivery acknowledgements
remain outside the completion aggregate.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base as KernelBase
from dotmac_kernel.models import Party, PartyPerson, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("learning")

# Assessment policy vocabulary remains owned by learning.
ASSESSMENT_MODES = ("practice", "graded", "exam")


def tenant_id_column() -> Mapped[UUID]:
    return mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )


# The complete, closed set of event kinds. ``session_attended`` is defined now
# but dormant until roadmap P4 (attendance) introduces its emitter.
KIND_COURSE_VIEWED = "course_viewed"
KIND_CHAPTER_VIEWED = "chapter_viewed"
KIND_CHAPTER_COMPLETED = "chapter_completed"
KIND_ACTIVITY_STARTED = "activity_started"
KIND_SUBMISSION_MADE = "submission_made"
KIND_WORK_GRADED = "work_graded"
KIND_LAB_LAUNCHED = "lab_launched"
KIND_LAB_CHECK_PASSED = "lab_check_passed"
KIND_LAB_CHECK_FAILED = "lab_check_failed"
KIND_SESSION_ATTENDED = "session_attended"
KIND_CERTIFICATE_EARNED = "certificate_earned"

EVENT_KINDS: tuple[str, ...] = (
    KIND_COURSE_VIEWED,
    KIND_CHAPTER_VIEWED,
    KIND_CHAPTER_COMPLETED,
    KIND_ACTIVITY_STARTED,
    KIND_SUBMISSION_MADE,
    KIND_WORK_GRADED,
    KIND_LAB_LAUNCHED,
    KIND_LAB_CHECK_PASSED,
    KIND_LAB_CHECK_FAILED,
    KIND_SESSION_ATTENDED,
    KIND_CERTIFICATE_EARNED,
)


class Course(KernelBase, TimestampMixin):
    __tablename__ = "courses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_courses_tenant_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_courses_tenant_id_id"),
        CheckConstraint(
            "prerequisite_composition IN ('all', 'any')",
            name="ck_courses_prerequisite_composition",
        ),
        CheckConstraint(
            "completion_fraction > 0 AND completion_fraction <= 1",
            name="ck_courses_completion_fraction",
        ),
        CheckConstraint(
            "certificate_validity_days IS NULL OR certificate_validity_days >= 1",
            name="ck_courses_certificate_validity_days",
        ),
        Index("ix_courses_tenant_id", "tenant_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    slug: Mapped[str] = mapped_column(String(63), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    discipline: Mapped[str] = mapped_column(String(40), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(120), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Authoring lifecycle (Slice 5/#8). Draft courses are hidden from learners
    # even when offered+entitled; instructors publish them when ready.
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Public-catalog visibility: the one canonical selector for the anonymous
    # catalog projection. Internal material (instructor guide, entrance shells,
    # internal disciplines) stays unlisted regardless of publish status.
    listed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Prerequisite condition-group composition (HM-01): "all" requires every
    # group satisfied (the historical implicit rule), "any" requires one.
    prerequisite_composition: Mapped[str] = mapped_column(
        String(8), nullable=False, default="all", server_default="all"
    )
    # Completion threshold (HM-03): fraction of activities that must pass.
    # The historical rule is 1.0 (every activity); read inside recompute.
    completion_fraction: Mapped[float] = mapped_column(
        Float, nullable=False, default=1.0, server_default="1.0"
    )
    # Certificate policy (HM-04): auto-issue on first completion, and the
    # validity window stamped at issue time (NULL validity = never expires).
    certificate_auto_issue: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    certificate_validity_days: Mapped[int | None] = mapped_column(Integer)


class Activity(KernelBase, TimestampMixin):
    __tablename__ = "activities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_activities_tenant_id_id"),
        CheckConstraint(
            "scoring_policy IN ('best', 'latest', 'first')",
            name="ck_activities_scoring_policy",
        ),
        CheckConstraint(
            "min_attempt_interval_seconds >= 0 "
            "AND min_attempt_interval_seconds <= 2592000",
            name="ck_activities_min_attempt_interval",
        ),
        Index("ix_activities_tenant_id", "tenant_id"),
        Index("ix_activities_course_id", "course_id"),
        Index("ix_activities_bank_id", "bank_id"),
        Index("ix_activities_bank_version_id", "bank_version_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    course_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    chapter_number: Mapped[int | None] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    bank_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    # Published bank version this activity serves (logical reference, like
    # bank_id); set only by explicit repoint or backfill, never by publish.
    bank_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    pass_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, server_default="0.0"
    )
    # Assessment policy (Slice 4). Null = unlimited attempts.
    max_attempts: Mapped[int | None] = mapped_column(Integer)
    # Winning-score selection (HM-06): "best" is the historical rule,
    # "latest"/"first" pick by (attempt_no, score time). Read at projection.
    scoring_policy: Mapped[str] = mapped_column(
        String(10), nullable=False, default="best", server_default="best"
    )
    # Authored final flag (HM-10). lr_0009 backfills it from the historical
    # heuristic (chapter_number IS NULL AND pass_threshold > 0).
    is_final: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Minimum seconds between attempts (HM-13); 0 disables the cooldown.
    min_attempt_interval_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Assessment mode bundles the scoring + answer-reveal policy by purpose:
    #   practice — best-of, immediate feedback (formative)
    #   graded   — best-of, answers withheld until pass or attempts exhausted
    #   exam     — best-of, score only (never reveal per-question feedback)
    # Scoring is best-of (highest attempt) in every mode; pass is sticky.
    assessment_mode: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="graded"
    )
    # "auto" grades on submit; "manual" leaves submissions for instructor grading.
    grading: Mapped[str] = mapped_column(String(10), nullable=False)
    # Weighted gradebook (lms-gaps F9): per-activity weight in the course grade.
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    # Random pool: show this many randomly-drawn questions from the bank (null = all).
    question_count: Mapped[int | None] = mapped_column(Integer)


class Submission(KernelBase, TimestampMixin):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_submissions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "activity_id",
            "person_id",
            "attempt_no",
            name="uq_submissions_person_activity_attempt",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "activity_id"],
            [f"{SCHEMA}.activities.tenant_id", f"{SCHEMA}.activities.id"],
            ondelete="CASCADE",
            name="fk_submissions_tenant_activity",
        ),
        Index("ix_submissions_tenant_id", "tenant_id"),
        Index("ix_submissions_activity_id", "activity_id"),
        Index("ix_submissions_person_id", "person_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    activity_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    answers: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Score(KernelBase, TimestampMixin):
    __tablename__ = "scores"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "submission_id"],
            [f"{SCHEMA}.submissions.tenant_id", f"{SCHEMA}.submissions.id"],
            ondelete="CASCADE",
            name="fk_scores_tenant_submission",
        ),
        Index("ix_scores_tenant_id", "tenant_id"),
        Index("ix_scores_submission_id", "submission_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    submission_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    max_score: Mapped[float] = mapped_column(Float, nullable=False)
    fraction: Mapped[float] = mapped_column(Float, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    per_item: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    source: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")
    override_reason: Mapped[str | None] = mapped_column(Text)


class CourseCompletion(KernelBase, TimestampMixin):
    """A learner's completion state for a course.

    ``pct`` is the fraction of the course's activities with a passing score.
    ``status`` flips to ``completed`` (and stamps ``completed_at`` once) when pct
    reaches the course's completion fraction (1.0 historically). Recomputed
    whenever a score is written for the person/course.
    """

    __tablename__ = "course_completions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_course_completions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "person_id",
            "course_id",
            name="uq_course_completions_person_course",
        ),
        # course_id is FK-constrained (referential integrity + cascade on course
        # delete). person_id is intentionally FK-less, matching the existing
        # submissions/scores ledger convention.
        ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            [f"{SCHEMA}.courses.tenant_id", f"{SCHEMA}.courses.id"],
            ondelete="CASCADE",
            name="fk_course_completions_tenant_course",
        ),
        Index("ix_course_completions_tenant_id", "tenant_id"),
        Index("ix_course_completions_person_id", "person_id"),
        Index("ix_course_completions_course_id", "course_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    course_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_progress"
    )
    pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Certificate(KernelBase, TimestampMixin):
    """A credential issued when a learner completes a course.

    One per (person, course). ``serial`` is a tenant-unique, human-readable code
    printed on the PDF for verification.
    """

    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_certificates_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "person_id",
            "course_id",
            name="uq_certificates_person_course",
        ),
        UniqueConstraint("tenant_id", "serial", name="uq_certificates_tenant_serial"),
        # course_id FK-constrained; person_id FK-less (matches submissions/scores).
        ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            [f"{SCHEMA}.courses.tenant_id", f"{SCHEMA}.courses.id"],
            ondelete="CASCADE",
            name="fk_certificates_tenant_course",
        ),
        Index("ix_certificates_tenant_id", "tenant_id"),
        Index("ix_certificates_person_id", "person_id"),
        Index("ix_certificates_course_id", "course_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = tenant_id_column()
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    course_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    serial: Mapped[str] = mapped_column(String(32), nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Expiry stamped at issue from the course validity (HM-04); NULL = never.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LearningEvent(KernelBase, TimestampMixin):
    __tablename__ = "learning_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            [Party.__table__.c.tenant_id, Party.__table__.c.id],
            ondelete="CASCADE",
            name="fk_learning_events_tenant_person",
        ),
        ForeignKeyConstraint(
            ["person_id"],
            [PartyPerson.__table__.c.party_id],
            ondelete="CASCADE",
            name="fk_learning_events_party_person",
        ),
        Index(
            "ix_learning_events_person_time", "tenant_id", "person_id", "occurred_at"
        ),
        Index("ix_learning_events_course_kind", "tenant_id", "course_id", "kind"),
        Index("ix_learning_events_kind_time", "tenant_id", "kind", "occurred_at"),
        # Academy revision 0047 guards these two once-per-subject kinds.
        # Day-throttled view events and NULL-subject semantics are unchanged.
        Index(
            "uq_learning_events_once_per_subject",
            "tenant_id",
            "person_id",
            "kind",
            "subject_id",
            unique=True,
            postgresql_where=text(
                "kind IN ('chapter_completed', 'certificate_earned')"
            ),
        ),
        Index("ix_learning_events_tenant_id", "tenant_id"),
        Index("ix_learning_events_person_id", "person_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    course_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # The event's primary subject (chapter, activity, submission, session or
    # course id depending on kind); extra facts go in ``detail``.
    subject_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    detail: Mapped[dict[str, object]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=dict
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


TENANT_MODELS = (
    Course,
    Activity,
    Submission,
    Score,
    CourseCompletion,
    Certificate,
    LearningEvent,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "ASSESSMENT_MODES",
    "Activity",
    "Certificate",
    "Course",
    "CourseCompletion",
    "EVENT_KINDS",
    "KIND_ACTIVITY_STARTED",
    "KIND_CERTIFICATE_EARNED",
    "KIND_CHAPTER_COMPLETED",
    "KIND_CHAPTER_VIEWED",
    "KIND_COURSE_VIEWED",
    "KIND_LAB_CHECK_FAILED",
    "KIND_LAB_CHECK_PASSED",
    "KIND_LAB_LAUNCHED",
    "KIND_SESSION_ATTENDED",
    "KIND_SUBMISSION_MADE",
    "KIND_WORK_GRADED",
    "LearningEvent",
    "SCHEMA",
    "Score",
    "Submission",
    "TENANT_MODELS",
    "TENANT_TABLES",
]
