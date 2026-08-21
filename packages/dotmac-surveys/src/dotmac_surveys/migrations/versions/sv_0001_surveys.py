"""Create the tenant-only Surveys owner in ``mod_surveys``.

Revision ID: sv_0001_surveys
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "sv_0001_surveys"
down_revision = None
branch_labels = ("surveys",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_surveys"


def _timestamps() -> tuple[sa.Column[Any], sa.Column[Any]]:
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


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["public.tenants.id"],
        name=name,
        ondelete="CASCADE",
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_surveys;")
    op.execute("GRANT USAGE ON SCHEMA mod_surveys TO app_user, platform_api;")

    op.create_table(
        "surveys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "questions", sa.JSON(), nullable=False, server_default=sa.text("'[]'")
        ),
        sa.Column("public_slug", sa.String(120), nullable=True),
        sa.Column("thank_you_message", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("total_invited", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_responses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_rating", sa.Numeric(6, 2), nullable=True),
        sa.Column("nps_score", sa.Numeric(6, 2), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_surveys_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_surveys_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "public_slug", name="uq_surveys_tenant_public_slug"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'closed')",
            name="ck_surveys_status",
        ),
        sa.CheckConstraint("total_invited >= 0", name="ck_surveys_invited_nonnegative"),
        sa.CheckConstraint(
            "total_responses >= 0", name="ck_surveys_responses_nonnegative"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_surveys_tenant_status", "surveys", ["tenant_id", "status"], schema=_SCHEMA
    )

    op.create_table(
        "survey_invitations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("survey_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_ref", sa.String(200), nullable=False),
        sa.Column("token", sa.String(80), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(200), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_survey_invitations_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "survey_id"],
            ["mod_surveys.surveys.tenant_id", "mod_surveys.surveys.id"],
            name="fk_survey_invitations_survey",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_survey_invitations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "survey_id",
            "id",
            name="uq_survey_invitations_survey_id_id",
        ),
        sa.UniqueConstraint(
            "tenant_id", "token", name="uq_survey_invitations_tenant_token"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "survey_id",
            "recipient_ref",
            "source_owner",
            "source_event_id",
            name="uq_survey_invitations_source_recipient",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'expired')",
            name="ck_survey_invitations_status",
        ),
        sa.CheckConstraint(
            "length(trim(token)) > 0", name="ck_survey_invitations_token_not_blank"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_survey_invitations_tenant_survey",
        "survey_invitations",
        ["tenant_id", "survey_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "survey_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("survey_id", sa.Uuid(), nullable=False),
        sa.Column("invitation_id", sa.Uuid(), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("nps_value", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        _tenant_fk("fk_survey_responses_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "survey_id"],
            ["mod_surveys.surveys.tenant_id", "mod_surveys.surveys.id"],
            name="fk_survey_responses_survey",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "survey_id", "invitation_id"],
            [
                "mod_surveys.survey_invitations.tenant_id",
                "mod_surveys.survey_invitations.survey_id",
                "mod_surveys.survey_invitations.id",
            ],
            name="fk_survey_responses_invitation",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_survey_responses_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "invitation_id",
            name="uq_survey_responses_tenant_invitation",
        ),
        sa.CheckConstraint(
            "rating IS NULL OR (rating >= 1 AND rating <= 5)",
            name="ck_survey_responses_rating",
        ),
        sa.CheckConstraint(
            "nps_value IS NULL OR (nps_value >= 0 AND nps_value <= 10)",
            name="ck_survey_responses_nps",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_survey_responses_tenant_survey",
        "survey_responses",
        ["tenant_id", "survey_id"],
        schema=_SCHEMA,
    )

    op.execute("ALTER TABLE mod_surveys.surveys ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_surveys.surveys FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY surveys_tenant_isolation ON mod_surveys.surveys
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_surveys.surveys TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_surveys.surveys TO platform_api;"
    )

    op.execute("ALTER TABLE mod_surveys.survey_invitations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_surveys.survey_invitations FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY survey_invitations_tenant_isolation
            ON mod_surveys.survey_invitations
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_surveys.survey_invitations TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_surveys.survey_invitations TO platform_api;"
    )

    op.execute("ALTER TABLE mod_surveys.survey_responses ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_surveys.survey_responses FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY survey_responses_tenant_isolation
            ON mod_surveys.survey_responses
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_surveys.survey_responses TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_surveys.survey_responses TO platform_api;"
    )


def downgrade() -> None:
    op.drop_table("survey_responses", schema=_SCHEMA)
    op.drop_table("survey_invitations", schema=_SCHEMA)
    op.drop_table("surveys", schema=_SCHEMA)
    op.execute("DROP SCHEMA mod_surveys;")
