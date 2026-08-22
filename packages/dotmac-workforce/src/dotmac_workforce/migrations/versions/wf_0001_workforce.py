"""Create workforce scheduling and dispatch tables.

Revision ID: wf_0001_workforce
Revises: (lineage root)
Create Date: 2026-08-20
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "wf_0001_workforce"
down_revision = None
branch_labels = ("workforce",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_workforce"


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
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


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_workforce;")
    op.execute("REVOKE ALL ON SCHEMA mod_workforce FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_workforce TO app_user, app_admin;")
    op.create_table(
        "workforce_teams",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_workforce_teams_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_workforce_teams_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_workforce_teams_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "workforce_skills",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_workforce_skills_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_workforce_skills_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_workforce_skills_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "team_memberships",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("worker_reference", sa.String(160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_team_memberships_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            [
                "mod_workforce.workforce_teams.tenant_id",
                "mod_workforce.workforce_teams.id",
            ],
            ondelete="CASCADE",
            name="fk_team_memberships_tenant_team",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_team_memberships_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "team_id",
            "worker_reference",
            name="uq_team_memberships_tenant_team_worker",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_team_memberships_tenant_worker",
        "team_memberships",
        ["tenant_id", "worker_reference"],
        schema=_SCHEMA,
    )
    op.create_table(
        "worker_skills",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("worker_reference", sa.String(160), nullable=False),
        sa.Column("skill_id", sa.Uuid(), nullable=False),
        sa.Column("proficiency", sa.Integer(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_worker_skills_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            [
                "mod_workforce.workforce_skills.tenant_id",
                "mod_workforce.workforce_skills.id",
            ],
            ondelete="CASCADE",
            name="fk_worker_skills_tenant_skill",
        ),
        sa.CheckConstraint(
            "proficiency BETWEEN 1 AND 5", name="ck_worker_skills_proficiency"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_worker_skills_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "worker_reference",
            "skill_id",
            name="uq_worker_skills_tenant_worker_skill",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "workforce_shifts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_workforce_shifts_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            [
                "mod_workforce.workforce_teams.tenant_id",
                "mod_workforce.workforce_teams.id",
            ],
            ondelete="CASCADE",
            name="fk_workforce_shifts_tenant_team",
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_workforce_shifts_window"),
        sa.CheckConstraint("capacity > 0", name="ck_workforce_shifts_capacity"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_workforce_shifts_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_workforce_shifts_tenant_team_start",
        "workforce_shifts",
        ["tenant_id", "team_id", "starts_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "workforce_availability",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("worker_reference", sa.String(160), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False),
        sa.Column("source_reference", sa.String(160), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_workforce_availability_tenant",
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name="ck_workforce_availability_window"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_workforce_availability_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "worker_reference",
            "starts_at",
            "ends_at",
            name="uq_workforce_availability_tenant_worker_window",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_workforce_availability_tenant_worker_start",
        "workforce_availability",
        ["tenant_id", "worker_reference", "starts_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "dispatch_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("work_reference", sa.String(180), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("worker_reference", sa.String(160), nullable=False),
        sa.Column("required_skill_id", sa.Uuid(), nullable=False),
        sa.Column("shift_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_dispatch_decisions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "team_id"],
            [
                "mod_workforce.workforce_teams.tenant_id",
                "mod_workforce.workforce_teams.id",
            ],
            name="fk_dispatch_decisions_tenant_team",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "shift_id"],
            [
                "mod_workforce.workforce_shifts.tenant_id",
                "mod_workforce.workforce_shifts.id",
            ],
            name="fk_dispatch_decisions_tenant_shift",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "required_skill_id"],
            [
                "mod_workforce.workforce_skills.tenant_id",
                "mod_workforce.workforce_skills.id",
            ],
            name="fk_dispatch_decisions_tenant_skill",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_dispatch_decisions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "work_reference",
            name="uq_dispatch_decisions_tenant_work",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_dispatch_decisions_tenant_worker_time",
        "dispatch_decisions",
        ["tenant_id", "worker_reference", "scheduled_for"],
        schema=_SCHEMA,
    )
    op.execute("ALTER TABLE mod_workforce.workforce_teams ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_workforce.workforce_teams FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY workforce_teams_tenant_isolation ON mod_workforce.workforce_teams USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_workforce.workforce_teams TO app_user;"
    )
    op.execute("ALTER TABLE mod_workforce.workforce_skills ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_workforce.workforce_skills FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY workforce_skills_tenant_isolation ON mod_workforce.workforce_skills USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_workforce.workforce_skills TO app_user;"
    )
    op.execute("ALTER TABLE mod_workforce.team_memberships ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_workforce.team_memberships FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY team_memberships_tenant_isolation ON mod_workforce.team_memberships USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_workforce.team_memberships TO app_user;"
    )
    op.execute("ALTER TABLE mod_workforce.worker_skills ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_workforce.worker_skills FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY worker_skills_tenant_isolation ON mod_workforce.worker_skills USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_workforce.worker_skills TO app_user;"
    )
    op.execute("ALTER TABLE mod_workforce.workforce_shifts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_workforce.workforce_shifts FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY workforce_shifts_tenant_isolation ON mod_workforce.workforce_shifts USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_workforce.workforce_shifts TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_workforce.workforce_availability ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_workforce.workforce_availability FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY workforce_availability_tenant_isolation ON mod_workforce.workforce_availability USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_workforce.workforce_availability TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_workforce.dispatch_decisions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_workforce.dispatch_decisions FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY dispatch_decisions_tenant_isolation ON mod_workforce.dispatch_decisions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_workforce.dispatch_decisions TO app_user;"
    )


def downgrade() -> None:
    op.drop_table("dispatch_decisions", schema=_SCHEMA)
    op.drop_table("workforce_availability", schema=_SCHEMA)
    op.drop_table("workforce_shifts", schema=_SCHEMA)
    op.drop_table("worker_skills", schema=_SCHEMA)
    op.drop_table("team_memberships", schema=_SCHEMA)
    op.drop_table("workforce_skills", schema=_SCHEMA)
    op.drop_table("workforce_teams", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_workforce;")
