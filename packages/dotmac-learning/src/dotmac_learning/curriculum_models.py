"""Curriculum and reading records from Academy 0968881.

References retain the source's individual contracts: bank course references
and reading person IDs are logical UUIDs; read/annotation chapter references
are simple ID FKs, not tenant composite constraints.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.models import Base as KernelBase
from dotmac_kernel.models import Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import schema_table_args
from sqlalchemy import (
    JSON,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
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
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
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
        schema_table_args(SCHEMA),
    )
    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    course_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    chapter_number: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # chapter|mid|final
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class Question(KernelBase, TimestampMixin):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "bank_id", "ext_id", name="uq_questions_bank_ext"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "bank_id"],
            [f"{SCHEMA}.question_banks.tenant_id", f"{SCHEMA}.question_banks.id"],
            ondelete="CASCADE",
            name="fk_questions_tenant_bank",
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
    bank_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
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
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class ChapterRead(KernelBase, TimestampMixin):
    __tablename__ = "chapter_reads"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "person_id",
            "chapter_id",
            name="uq_chapter_reads_person_chapter",
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
    person_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    course_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
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
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    person_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    course_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.courses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")


CURRICULUM_MODELS = (
    Chapter,
    QuestionBank,
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
    "Question",
    "ChapterRead",
    "SubtopicRead",
    "ChapterBookmark",
    "ChapterNote",
]
