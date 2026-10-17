"""Add seven curriculum and reading tables to the canonical learning lineage.

Revision ID: lr_0002_curriculum_reading
Revises: lr_0001_learning

Source: immutable Academy 0968881, revisions 0002, 0004, 0019, 0024,
0030, 0043 and 0048. Read/annotation chapter FKs are simple source ID
references. Product foreign-table rebinding and role grants belong to Academy.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    resolve_depends_on,
)
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "lr_0002_curriculum_reading"
down_revision = "lr_0001_learning"
branch_labels = None
REQUIRES = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    PARTY_PERSON_CATALOG_V1.name,
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_learning"
_TABLES = (
    "chapters",
    "question_banks",
    "questions",
    "chapter_reads",
    "subtopic_reads",
    "chapter_bookmarks",
    "chapter_notes",
)


def _identity() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("public.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )


def _timestamps() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column], schema=_SCHEMA)


def upgrade() -> None:
    # Same logical prerequisites as the lineage root, verified before any DDL.
    require_prerequisites(op.get_bind(), REQUIRES)
    op.create_table(
        "chapters",
        *_identity(),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("part", sa.String(20), nullable=False, server_default=""),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=""),
        sa.Column("body_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "course_id", "number", name="uq_chapters_tenant_course_number"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
            ondelete="CASCADE",
            name="fk_chapters_tenant_course",
        ),
        schema=_SCHEMA,
    )
    _indexes("chapters", ("tenant_id", "course_id"))
    op.create_table(
        "question_banks",
        *_identity(),
        # Logical source references, deliberately not inferred foreign keys.
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_question_banks_tenant_id_id"),
        schema=_SCHEMA,
    )
    _indexes("question_banks", ("tenant_id", "course_id"))
    op.create_table(
        "questions",
        *_identity(),
        sa.Column("bank_id", sa.Uuid(), nullable=False),
        sa.Column("ext_id", sa.String(60), nullable=False),
        sa.Column("stem", sa.Text(), nullable=False),
        sa.Column("type", sa.String(12), nullable=False),
        sa.Column(
            "options",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "correct",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("rubric_category", sa.String(12), nullable=False),
        sa.Column("category", sa.String(40), nullable=True),
        sa.Column("explanation", sa.Text(), nullable=False, server_default=""),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "bank_id", "ext_id", name="uq_questions_bank_ext"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "bank_id"],
            ["mod_learning.question_banks.tenant_id", "mod_learning.question_banks.id"],
            ondelete="CASCADE",
            name="fk_questions_tenant_bank",
        ),
        schema=_SCHEMA,
    )
    _indexes("questions", ("tenant_id", "bank_id"))
    op.create_table(
        "chapter_reads",
        *_identity(),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column(
            "chapter_id",
            sa.Uuid(),
            sa.ForeignKey("mod_learning.chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id",
            "person_id",
            "chapter_id",
            name="uq_chapter_reads_person_chapter",
        ),
        schema=_SCHEMA,
    )
    _indexes("chapter_reads", ("tenant_id", "person_id", "chapter_id"))
    op.create_table(
        "subtopic_reads",
        *_identity(),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column(
            "chapter_id",
            sa.Uuid(),
            sa.ForeignKey("mod_learning.chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("subtopic_slug", sa.String(64), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id",
            "person_id",
            "chapter_id",
            "subtopic_slug",
            name="uq_subtopic_reads_person_subtopic",
        ),
        schema=_SCHEMA,
    )
    _indexes("subtopic_reads", ("tenant_id", "person_id", "chapter_id"))
    op.create_table(
        "chapter_bookmarks",
        *_identity(),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column(
            "course_id",
            sa.Uuid(),
            sa.ForeignKey("mod_learning.courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            sa.Uuid(),
            sa.ForeignKey("mod_learning.chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id",
            "person_id",
            "chapter_id",
            name="uq_chapter_bookmarks_person_chapter",
        ),
        schema=_SCHEMA,
    )
    _indexes("chapter_bookmarks", ("tenant_id", "person_id", "course_id", "chapter_id"))
    op.create_table(
        "chapter_notes",
        *_identity(),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column(
            "course_id",
            sa.Uuid(),
            sa.ForeignKey("mod_learning.courses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "chapter_id",
            sa.Uuid(),
            sa.ForeignKey("mod_learning.chapters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id",
            "person_id",
            "chapter_id",
            name="uq_chapter_notes_person_chapter",
        ),
        schema=_SCHEMA,
    )
    _indexes("chapter_notes", ("tenant_id", "person_id", "course_id", "chapter_id"))
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_learning.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE mod_learning.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_learning.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id())"
        )
        privileges = (
            "SELECT, INSERT"
            if table == "subtopic_reads"
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(
            f"GRANT {privileges} ON mod_learning.{table} TO app_user, platform_api"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
