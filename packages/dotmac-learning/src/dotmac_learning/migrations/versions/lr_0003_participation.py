"""Add seven fresh participation tables; Academy 096 actual migrated contracts.

No product backfill. Legacy People references explicitly translate to Kernel
Party plus PartyPerson with stable UUIDs and both source cascade semantics.
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

from alembic import op

revision = "lr_0003_participation"
down_revision = "lr_0002_curriculum_reading"
branch_labels = None
REQUIRES = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    PARTY_PERSON_CATALOG_V1.name,
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_learning"
_TABLES = (
    "cohorts",
    "tracks",
    "cohort_tracks",
    "track_courses",
    "enrollments",
    "course_offerings",
    "course_prerequisites",
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
    require_prerequisites(op.get_bind(), REQUIRES)
    op.create_table(
        "cohorts",
        *_identity(),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("discipline", sa.String(40), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column(
            "delivery_mode", sa.String(20), nullable=False, server_default="self_paced"
        ),
        sa.Column("entrance_bank_id", sa.Uuid(), nullable=True),
        sa.Column("entrance_time_limit_minutes", sa.Integer(), nullable=True),
        sa.Column("auto_accept_threshold", sa.Float(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cohorts_tenant_id_id"),
        schema=_SCHEMA,
    )
    _indexes("cohorts", ("tenant_id",))
    op.create_table(
        "tracks",
        *_identity(),
        sa.Column("slug", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "slug", name="uq_tracks_tenant_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tracks_tenant_id_id"),
        schema=_SCHEMA,
    )
    _indexes("tracks", ("tenant_id",))
    op.create_table(
        "cohort_tracks",
        *_identity(),
        sa.Column("cohort_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_cohort_tracks_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "cohort_id", "track_id", name="uq_cohort_tracks_cohort_track"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_cohort_tracks_tenant_cohort",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "track_id"],
            ["mod_learning.tracks.tenant_id", "mod_learning.tracks.id"],
            ondelete="CASCADE",
            name="fk_cohort_tracks_tenant_track",
        ),
        schema=_SCHEMA,
    )
    _indexes("cohort_tracks", ("tenant_id", "cohort_id", "track_id"))
    op.create_table(
        "track_courses",
        *_identity(),
        sa.Column("track_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "track_id", "course_id", name="uq_track_courses_track_course"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "track_id"],
            ["mod_learning.tracks.tenant_id", "mod_learning.tracks.id"],
            ondelete="CASCADE",
            name="fk_track_courses_tenant_track",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
            ondelete="CASCADE",
            name="fk_track_courses_tenant_course",
        ),
        schema=_SCHEMA,
    )
    _indexes("track_courses", ("tenant_id", "track_id", "course_id"))
    op.create_table(
        "enrollments",
        *_identity(),
        sa.Column("cohort_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("track_id", sa.Uuid(), nullable=True),
        sa.Column(
            "role_in_cohort", sa.String(20), nullable=False, server_default="student"
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("audience", sa.String(20), nullable=True),
        sa.Column("employee_ref", sa.String(64), nullable=True),
        sa.Column("access_ends_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "cohort_id", "person_id", name="uq_enrollments_member"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cohort_id", "track_id"],
            [
                "mod_learning.cohort_tracks.tenant_id",
                "mod_learning.cohort_tracks.cohort_id",
                "mod_learning.cohort_tracks.track_id",
            ],
            name="fk_enrollments_tenant_cohort_track",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_enrollments_tenant_cohort",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "person_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="CASCADE",
            name="fk_enrollments_tenant_person",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["public.party_persons.party_id"],
            ondelete="CASCADE",
            name="fk_enrollments_party_person",
        ),
        sa.CheckConstraint(
            "audience IS NULL OR audience IN ('staff', 'external')",
            name="ck_enrollments_audience",
        ),
        sa.CheckConstraint(
            "audience IS DISTINCT FROM 'staff' OR employee_ref IS NOT NULL",
            name="ck_enrollments_staff_has_employee_ref",
        ),
        schema=_SCHEMA,
    )
    _indexes("enrollments", ("tenant_id", "cohort_id", "person_id", "track_id"))
    op.create_index(
        "ix_enrollments_audience",
        "enrollments",
        ["tenant_id", "audience"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_enrollments_access_ends_at",
        "enrollments",
        ["tenant_id", "access_ends_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "course_offerings",
        *_identity(),
        sa.Column("cohort_id", sa.Uuid(), nullable=False),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint("tenant_id", "id", name="uq_course_offerings_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "cohort_id",
            "course_id",
            name="uq_course_offerings_cohort_course",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "cohort_id"],
            ["mod_learning.cohorts.tenant_id", "mod_learning.cohorts.id"],
            ondelete="CASCADE",
            name="fk_course_offerings_tenant_cohort",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
            ondelete="CASCADE",
            name="fk_course_offerings_tenant_course",
        ),
        schema=_SCHEMA,
    )
    _indexes("course_offerings", ("tenant_id", "cohort_id", "course_id"))
    op.create_table(
        "course_prerequisites",
        *_identity(),
        sa.Column("course_id", sa.Uuid(), nullable=False),
        sa.Column("requires_course_id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_course_prerequisites_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "course_id",
            "requires_course_id",
            name="uq_course_prerequisites_pair",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "course_id"],
            ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
            ondelete="CASCADE",
            name="fk_course_prerequisites_tenant_course",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requires_course_id"],
            ["mod_learning.courses.tenant_id", "mod_learning.courses.id"],
            ondelete="CASCADE",
            name="fk_course_prerequisites_tenant_requires",
        ),
        schema=_SCHEMA,
    )
    _indexes("course_prerequisites", ("tenant_id", "course_id", "requires_course_id"))
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_learning.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE mod_learning.{table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_learning.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id())"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_learning.{table} TO app_user, platform_api"
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
