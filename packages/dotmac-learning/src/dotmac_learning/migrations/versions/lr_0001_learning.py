"""Create the first seven canonical learning tables.

Revision ID: lr_0001_learning
Revises: (lineage root)

Source: Academy's course/assessment/completion/certificate/event migrations,
through 0061. Product ERP acknowledgement state and product worker grants are
owned by the adopting assembly, not this canonical storage lineage.
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

revision = "lr_0001_learning"
down_revision = None
branch_labels = ("learning",)
REQUIRES = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    PARTY_PERSON_CATALOG_V1.name,
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_learning"
_TABLES = (
    "courses",
    "activities",
    "submissions",
    "scores",
    "course_completions",
    "certificates",
    "learning_events",
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


def _course_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "course_id"],
        ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
        ondelete="CASCADE",
        name=name,
    )


def _indexes(table: str, columns: tuple[str, ...]) -> None:
    for column in columns:
        op.create_index(f"ix_{table}_{column}", table, [column], schema=_SCHEMA)


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA mod_learning")
    op.execute("REVOKE ALL ON SCHEMA mod_learning FROM PUBLIC")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_learning TO app_admin, app_user, platform_api"
    )

    op.create_table(
        "courses",
        *_identity(),
        sa.Column("slug", sa.String(63), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("discipline", sa.String(40), nullable=False),
        sa.Column("source_ref", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(20), nullable=False, server_default="published"),
        sa.Column("listed", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_courses_tenant_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_courses_tenant_id_id"),
        schema=_SCHEMA,
    )
    _indexes("courses", ("tenant_id",))

    op.create_table(
        "activities",
        *_identity(),
        # These remain plain source UUID references; no course/bank FK is inferred.
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(16), nullable=False, server_default="mcq_test"),
        sa.Column("bank_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("pass_threshold", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("max_attempts", sa.Integer(), nullable=True),
        sa.Column("grading", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("question_count", sa.Integer(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "assessment_mode", sa.String(10), nullable=False, server_default="graded"
        ),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_activities_tenant_id_id"),
        schema=_SCHEMA,
    )
    _indexes("activities", ("tenant_id", "course_id", "bank_id"))

    op.create_table(
        "submissions",
        *_identity(),
        sa.Column("activity_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column(
            "answers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("attempt_no", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_submissions_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "activity_id",
            "person_id",
            "attempt_no",
            name="uq_submissions_person_activity_attempt",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "activity_id"],
            ["mod_learning.activities.tenant_id", "mod_learning.activities.id"],
            ondelete="CASCADE",
            name="fk_submissions_tenant_activity",
        ),
        schema=_SCHEMA,
    )
    _indexes("submissions", ("tenant_id", "activity_id", "person_id"))

    op.create_table(
        "scores",
        *_identity(),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("max_score", sa.Float(), nullable=False),
        sa.Column("fraction", sa.Float(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column(
            "per_item",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("source", sa.String(10), nullable=False, server_default="auto"),
        sa.Column("override_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id", "submission_id"],
            ["mod_learning.submissions.tenant_id", "mod_learning.submissions.id"],
            ondelete="CASCADE",
            name="fk_scores_tenant_submission",
        ),
        schema=_SCHEMA,
    )
    _indexes("scores", ("tenant_id", "submission_id"))

    op.create_table(
        "course_completions",
        *_identity(),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status", sa.String(20), nullable=False, server_default="in_progress"
        ),
        sa.Column("pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_course_completions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "person_id",
            "course_id",
            name="uq_course_completions_person_course",
        ),
        _course_fk("fk_course_completions_tenant_course"),
        schema=_SCHEMA,
    )
    _indexes("course_completions", ("tenant_id", "person_id", "course_id"))

    op.create_table(
        "certificates",
        *_identity(),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("serial", sa.String(32), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_certificates_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "person_id", "course_id", name="uq_certificates_person_course"
        ),
        sa.UniqueConstraint(
            "tenant_id", "serial", name="uq_certificates_tenant_serial"
        ),
        _course_fk("fk_certificates_tenant_course"),
        schema=_SCHEMA,
    )
    _indexes("certificates", ("tenant_id", "person_id", "course_id"))

    op.create_table(
        "learning_events",
        *_identity(),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column(
            "detail",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        # Party carries tenancy; PartyPerson carries person-subtype existence.
        # PartyPerson has neither an id nor a tenant_id column.
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="CASCADE",
            name="fk_learning_events_tenant_person",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["public.party_persons.party_id"],
            ondelete="CASCADE",
            name="fk_learning_events_person_subtype",
        ),
        schema=_SCHEMA,
    )
    _indexes("learning_events", ("tenant_id", "person_id"))
    for name, columns in (
        ("person_time", ["tenant_id", "person_id", "occurred_at"]),
        ("course_kind", ["tenant_id", "course_id", "kind"]),
        ("kind_time", ["tenant_id", "kind", "occurred_at"]),
    ):
        op.create_index(
            f"ix_learning_events_{name}", "learning_events", columns, schema=_SCHEMA
        )
    op.create_index(
        "uq_learning_events_once_per_subject",
        "learning_events",
        ["tenant_id", "person_id", "kind", "subject_id"],
        unique=True,
        postgresql_where=sa.text("kind IN ('chapter_completed', 'certificate_earned')"),
        schema=_SCHEMA,
    )

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
            if table == "learning_events"
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(
            f"GRANT {privileges} ON mod_learning.{table} TO app_user, platform_api"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA mod_learning")
