"""Curriculum and reading records from Academy 0968881.

References retain the source's individual contracts: bank course references
and reading person IDs are logical UUIDs; read/annotation chapter references
are simple ID FKs, not tenant composite constraints.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from dotmac_kernel.models import Base as KernelBase
from dotmac_kernel.models import Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import schema_table_args
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
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

from .models import SCHEMA


class Chapter(KernelBase, TimestampMixin):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "course_id", "number", name="uq_chapters_tenant_course_number"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            [f"{SCHEMA}.courses.tenant_id", f"{SCHEMA}.courses.id"],
            ondelete="CASCADE",
            name="fk_chapters_tenant_course",
        ),
        Index("ix_chapters_tenant_id", "tenant_id"),
        Index("ix_chapters_course_id", "course_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    course_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    part: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    body_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Editable markdown source for in-app authoring (Slice 5/#8); body_html is the
    # rendered output. Empty for filesystem-imported chapters.
    body_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class QuestionBank(KernelBase, TimestampMixin):
    __tablename__ = "question_banks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_question_banks_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "current_version_id"],
            [
                f"{SCHEMA}.question_bank_versions.tenant_id",
                f"{SCHEMA}.question_bank_versions.id",
            ],
            name="fk_question_banks_tenant_current_version",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        Index(
            "uq_question_banks_natural_key",
            "tenant_id",
            "course_id",
            "chapter_number",
            "kind",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_question_banks_tenant_id", "tenant_id"),
        Index("ix_question_banks_course_id", "course_id"),
        Index("ix_question_banks_current_version_id", "current_version_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    course_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    chapter_number: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # chapter|mid|final
    # Legacy last-published mirror: the version_no most recently
    # published for this bank (the initial declared 1 before the first
    # publish). No longer author-controlled, and retiring the current
    # version does not rewrite it; current_version_id is authoritative.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class QuestionBankVersion(KernelBase, TimestampMixin):
    """One immutable published (or single mutable draft) state of a bank."""

    __tablename__ = "question_bank_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_question_bank_versions_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "bank_id",
            "version_no",
            name="uq_question_bank_versions_bank_no",
        ),
        CheckConstraint(
            "state IN ('draft', 'published', 'retired')",
            name="ck_question_bank_versions_state",
        ),
        CheckConstraint(
            "origin IN ('ui', 'yaml_import', 'backfill')",
            name="ck_question_bank_versions_origin",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "bank_id"],
            [f"{SCHEMA}.question_banks.tenant_id", f"{SCHEMA}.question_banks.id"],
            ondelete="CASCADE",
            name="fk_question_bank_versions_tenant_bank",
        ),
        Index(
            "uq_question_bank_versions_one_draft",
            "tenant_id",
            "bank_id",
            unique=True,
            postgresql_where=text("state = 'draft'"),
            sqlite_where=text("state = 'draft'"),
        ),
        Index("ix_question_bank_versions_tenant_id", "tenant_id"),
        Index("ix_question_bank_versions_bank_id", "bank_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    bank_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # Allocated by Learning at publish (max+1 under the bank-row lock); a
    # draft carries NULL so discarded drafts never consume a number.
    version_no: Mapped[int | None] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(10), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, default="", server_default=""
    )
    lint_violations: Mapped[list[object]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"),
        nullable=False,
        default=list,
        server_default=text("'[]'"),
    )
    base_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    draft_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_by_actor: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    published_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retire_reason: Mapped[str | None] = mapped_column(Text)
    superseded_by_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class Question(KernelBase, TimestampMixin):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "bank_version_id",
            "ext_id",
            name="uq_questions_bank_version_ext",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "bank_id"],
            [f"{SCHEMA}.question_banks.tenant_id", f"{SCHEMA}.question_banks.id"],
            ondelete="CASCADE",
            name="fk_questions_tenant_bank",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "bank_version_id"],
            [
                f"{SCHEMA}.question_bank_versions.tenant_id",
                f"{SCHEMA}.question_bank_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_questions_tenant_bank_version",
        ),
        Index("ix_questions_tenant_id", "tenant_id"),
        Index("ix_questions_bank_id", "bank_id"),
        Index("ix_questions_bank_version_id", "bank_version_id"),
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    bank_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # NULL only for pre-lr_0010 rows materialized by frozen V1-V5 imports;
    # the migration backfills every live row onto its published v1.
    bank_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    ext_id: Mapped[str] = mapped_column(String(60), nullable=False)
    stem: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(
        String(12), nullable=False
    )  # single|multi|truefalse
    options: Mapped[list[object]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    correct: Mapped[list[object]] = mapped_column(
        JSONB().with_variant(JSON(), "sqlite"), nullable=False, default=list
    )
    rubric_category: Mapped[str] = mapped_column(
        String(12), nullable=False
    )  # recall|application|analysis
    # Optional skill/topic domain (e.g. numeracy, safety) — distinct from the
    # cognitive rubric_category; the entrance-assessment profile groups by this.
    category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    explanation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    weight: Mapped[int] = mapped_column(Integer, nullable=False)


class ChapterRead(KernelBase, TimestampMixin):
    __tablename__ = "chapter_reads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "person_id",
            "chapter_id",
            name="uq_chapter_reads_person_chapter",
        ),
        Index("ix_chapter_reads_tenant_id", "tenant_id"),
        Index("ix_chapter_reads_person_id", "person_id"),
        Index("ix_chapter_reads_chapter_id", "chapter_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    chapter_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.chapters.id", ondelete="CASCADE"),
        nullable=False,
    )


class SubtopicRead(KernelBase, TimestampMixin):
    """One completed subtopic within a chapter, for one learner.

    ``subtopic_slug`` is the slug ``learn._chapter_subtopics`` derives from the
    chapter's headings. It is content-derived rather than a foreign key, so an
    edited heading orphans its row instead of breaking: the learner sees that
    subtopic as incomplete again, which is the honest outcome when the material
    has changed under them.
    """

    __tablename__ = "subtopic_reads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "person_id",
            "chapter_id",
            "subtopic_slug",
            name="uq_subtopic_reads_person_subtopic",
        ),
        Index("ix_subtopic_reads_tenant_id", "tenant_id"),
        Index("ix_subtopic_reads_person_id", "person_id"),
        Index("ix_subtopic_reads_chapter_id", "chapter_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    chapter_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Matches the 64-char cap in ``learn._slugify_heading``.
    subtopic_slug: Mapped[str] = mapped_column(String(64), nullable=False)


class ChapterBookmark(KernelBase, TimestampMixin):
    """A learner's bookmark on a chapter — presence is the whole fact."""

    __tablename__ = "chapter_bookmarks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "person_id",
            "chapter_id",
            name="uq_chapter_bookmarks_person_chapter",
        ),
        Index("ix_chapter_bookmarks_tenant_id", "tenant_id"),
        Index("ix_chapter_bookmarks_person_id", "person_id"),
        Index("ix_chapter_bookmarks_course_id", "course_id"),
        Index("ix_chapter_bookmarks_chapter_id", "chapter_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    course_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.courses.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.chapters.id", ondelete="CASCADE"),
        nullable=False,
    )


class ChapterNote(KernelBase, TimestampMixin):
    """A learner's private note on a chapter.

    ``body`` is the learner's own words, stored raw and always rendered
    escaped — never as HTML.
    """

    __tablename__ = "chapter_notes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "person_id",
            "chapter_id",
            name="uq_chapter_notes_person_chapter",
        ),
        Index("ix_chapter_notes_tenant_id", "tenant_id"),
        Index("ix_chapter_notes_person_id", "person_id"),
        Index("ix_chapter_notes_course_id", "course_id"),
        Index("ix_chapter_notes_chapter_id", "chapter_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
    )
    person_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    course_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.courses.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")


CURRICULUM_MODELS = (
    Chapter,
    QuestionBank,
    QuestionBankVersion,
    Question,
    ChapterRead,
    SubtopicRead,
    ChapterBookmark,
    ChapterNote,
)
CURRICULUM_TABLES = tuple(model.__tablename__ for model in CURRICULUM_MODELS)

__all__ = [
    "CURRICULUM_MODELS",
    "CURRICULUM_TABLES",
    "Chapter",
    "QuestionBank",
    "QuestionBankVersion",
    "Question",
    "ChapterRead",
    "SubtopicRead",
    "ChapterBookmark",
    "ChapterNote",
]
