"""Add typed prerequisite conditions and track gating (HM-01, HM-02).

Server defaults materialize every existing edge as
(course_completed, {}, g0), every course as composition 'all' and every
track as gating 'previous', so access evaluation is unchanged until an
author writes a non-default condition.

Downgrade restores the historical schema only; it is not recovery of
post-upgrade facts. Non-default condition rows are deleted so the
historical unique key can be restored, matching the lineage posture of
earlier destructive-to-new-facts downgrades.
"""

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

revision = "lr_0008_prerequisite_conditions"
down_revision = "lr_0007_enrollment_state_policy"
branch_labels = None
REQUIRES = (
    TENANT_SCOPE_CATALOG_V1.name,
    MODULE_DATABASE_ROLES_V1.name,
    PARTY_PERSON_CATALOG_V1.name,
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_learning"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.add_column(
        "course_prerequisites",
        sa.Column(
            "condition_kind",
            sa.String(length=32),
            server_default="course_completed",
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.add_column(
        "course_prerequisites",
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.add_column(
        "course_prerequisites",
        sa.Column(
            "group_key",
            sa.String(length=32),
            server_default="g0",
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_course_prerequisites_condition_kind",
        "course_prerequisites",
        "condition_kind IN ('course_completed', 'certificate_held', "
        "'course_grade_at_least', 'activity_passed', "
        "'attempt_number_at_least')",
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "uq_course_prerequisites_pair", "course_prerequisites", schema=_SCHEMA
    )
    op.create_unique_constraint(
        "uq_course_prerequisites_pair",
        "course_prerequisites",
        [
            "tenant_id",
            "course_id",
            "requires_course_id",
            "condition_kind",
            "group_key",
        ],
        schema=_SCHEMA,
    )
    op.add_column(
        "courses",
        sa.Column(
            "prerequisite_composition",
            sa.String(length=8),
            server_default="all",
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_courses_prerequisite_composition",
        "courses",
        "prerequisite_composition IN ('all', 'any')",
        schema=_SCHEMA,
    )
    op.add_column(
        "tracks",
        sa.Column(
            "gating_mode",
            sa.String(length=16),
            server_default="previous",
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_tracks_gating_mode",
        "tracks",
        "gating_mode IN ('none', 'previous', 'all_previous')",
        schema=_SCHEMA,
    )


def downgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.drop_constraint("ck_tracks_gating_mode", "tracks", schema=_SCHEMA)
    op.drop_column("tracks", "gating_mode", schema=_SCHEMA)
    op.drop_constraint("ck_courses_prerequisite_composition", "courses", schema=_SCHEMA)
    op.drop_column("courses", "prerequisite_composition", schema=_SCHEMA)
    op.execute(
        "DELETE FROM mod_learning.course_prerequisites "
        "WHERE condition_kind <> 'course_completed' OR group_key <> 'g0'"
    )
    op.drop_constraint(
        "uq_course_prerequisites_pair", "course_prerequisites", schema=_SCHEMA
    )
    op.create_unique_constraint(
        "uq_course_prerequisites_pair",
        "course_prerequisites",
        ["tenant_id", "course_id", "requires_course_id"],
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "ck_course_prerequisites_condition_kind",
        "course_prerequisites",
        schema=_SCHEMA,
    )
    op.drop_column("course_prerequisites", "group_key", schema=_SCHEMA)
    op.drop_column("course_prerequisites", "params", schema=_SCHEMA)
    op.drop_column("course_prerequisites", "condition_kind", schema=_SCHEMA)
