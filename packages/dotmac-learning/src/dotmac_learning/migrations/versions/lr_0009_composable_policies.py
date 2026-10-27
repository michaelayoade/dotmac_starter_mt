"""Add composable learner-visible policy columns (Wave 1L columns).

Server defaults materialize every existing row at the historical rule:
completion fraction 1.0, certificate auto-issue on with no validity,
scoring ``best``, no attempt cooldown, pacing open by default, and
below-threshold admissions waitlisted. ``activities.is_final`` is
backfilled from the historical heuristic (chapter_number IS NULL AND
pass_threshold > 0) and the upgrade fails loudly if any row diverges,
so the authored flag cannot drift from the selection it replaces.

Downgrade restores the historical schema only; it is not recovery of
post-upgrade policy values, matching the lineage posture of earlier
destructive-to-new-facts downgrades.
"""

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_SCOPE_CATALOG_V1,
    resolve_depends_on,
)

from alembic import op

revision = "lr_0009_composable_policies"
down_revision = "lr_0008_prerequisite_conditions"
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
        "courses",
        sa.Column(
            "completion_fraction",
            sa.Float(),
            server_default="1.0",
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_courses_completion_fraction",
        "courses",
        "completion_fraction > 0 AND completion_fraction <= 1",
        schema=_SCHEMA,
    )
    op.add_column(
        "courses",
        sa.Column(
            "certificate_auto_issue",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.add_column(
        "courses",
        sa.Column("certificate_validity_days", sa.Integer(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_courses_certificate_validity_days",
        "courses",
        "certificate_validity_days IS NULL OR certificate_validity_days >= 1",
        schema=_SCHEMA,
    )
    op.add_column(
        "certificates",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        "activities",
        sa.Column(
            "scoring_policy",
            sa.String(length=10),
            server_default="best",
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_activities_scoring_policy",
        "activities",
        "scoring_policy IN ('best', 'latest', 'first')",
        schema=_SCHEMA,
    )
    op.add_column(
        "activities",
        sa.Column(
            "is_final",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.execute(
        "UPDATE mod_learning.activities SET is_final = true "
        "WHERE chapter_number IS NULL AND pass_threshold > 0"
    )
    divergent = op.get_bind().scalar(
        sa.text(
            "SELECT count(*) FROM mod_learning.activities "
            "WHERE is_final <> (chapter_number IS NULL AND pass_threshold > 0)"
        )
    )
    if divergent:
        raise RuntimeError(
            "is_final backfill diverged from the historical heuristic "
            f"on {divergent} activit(ies); refusing to continue"
        )
    op.add_column(
        "activities",
        sa.Column(
            "min_attempt_interval_seconds",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_activities_min_attempt_interval",
        "activities",
        "min_attempt_interval_seconds >= 0 "
        "AND min_attempt_interval_seconds <= 2592000",
        schema=_SCHEMA,
    )
    op.add_column(
        "course_offerings",
        sa.Column(
            "pacing_default_open",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.add_column(
        "cohorts",
        sa.Column(
            "auto_accept_below_action",
            sa.String(length=16),
            server_default="waitlisted",
            nullable=False,
        ),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_cohorts_auto_accept_below_action",
        "cohorts",
        "auto_accept_below_action IN ('waitlisted', 'none')",
        schema=_SCHEMA,
    )


def downgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.drop_constraint("ck_cohorts_auto_accept_below_action", "cohorts", schema=_SCHEMA)
    op.drop_column("cohorts", "auto_accept_below_action", schema=_SCHEMA)
    op.drop_column("course_offerings", "pacing_default_open", schema=_SCHEMA)
    op.drop_constraint(
        "ck_activities_min_attempt_interval", "activities", schema=_SCHEMA
    )
    op.drop_column("activities", "min_attempt_interval_seconds", schema=_SCHEMA)
    op.drop_column("activities", "is_final", schema=_SCHEMA)
    op.drop_constraint("ck_activities_scoring_policy", "activities", schema=_SCHEMA)
    op.drop_column("activities", "scoring_policy", schema=_SCHEMA)
    op.drop_column("certificates", "expires_at", schema=_SCHEMA)
    op.drop_constraint(
        "ck_courses_certificate_validity_days", "courses", schema=_SCHEMA
    )
    op.drop_column("courses", "certificate_validity_days", schema=_SCHEMA)
    op.drop_column("courses", "certificate_auto_issue", schema=_SCHEMA)
    op.drop_constraint("ck_courses_completion_fraction", "courses", schema=_SCHEMA)
    op.drop_column("courses", "completion_fraction", schema=_SCHEMA)
