"""Create response policies, targets, clocks, pause intervals and observations.

Revision ID: ro_0001_response_obligations
Revises: (lineage root)
Create Date: 2026-08-24

Five tenant tables on one forced-RLS plane. The extraction source (Sub's
`sla_policies`/`sla_targets`/`sla_clocks`/`sla_breaches`) has no tenant column
at all, so tenancy, composite parent keys and forced RLS exist in revision 1
rather than being retrofitted later.

Two partial unique indexes carry rules a plain constraint cannot express:
`uq_sla_targets_default_per_kind` makes "one default target per policy and
kind" real despite PostgreSQL permitting many NULLs in a UNIQUE, and
`uq_sla_clocks_live_subject_kind` stops a subject acquiring two live clocks of
the same kind — a second one would be measured from a later instant and breach
on its own schedule.

`sla_observations` is append-only, enforced by trigger. A breach that can be
edited afterwards is not evidence, and whether it deserves an escalation is
`dotmac-operational-escalations`' answer, not a column here.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ro_0001_response_obligations"
down_revision = None
branch_labels = ("response_obligations",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_sla"

_KINDS = ("FIRST_RESPONSE", "NEXT_RESPONSE", "QUEUE_WAIT", "RESOLUTION")
_CLOCK_STATUSES = ("RUNNING", "PAUSED", "MET", "BREACHED", "CANCELLED")
_PAUSE_REASONS = (
    "WAITING_ON_CUSTOMER",
    "WAITING_ON_THIRD_PARTY",
    "OUTSIDE_BUSINESS_HOURS",
    "SUSPENDED_BY_OPERATOR",
)
_OBSERVATIONS = ("WARNING", "BREACH")
_TENANT_TABLES = (
    "sla_policies",
    "sla_targets",
    "sla_clocks",
    "sla_clock_pauses",
    "sla_observations",
)


def _values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_sla;")
    op.execute("REVOKE ALL ON SCHEMA mod_sla FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_sla TO app_user, app_admin;")

    op.create_table(
        "sla_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("subject_type", sa.String(60), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_sla_policies_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sla_policies_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_sla_policies_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sla_policies_tenant_subject_type",
        "sla_policies",
        ["tenant_id", "subject_type"],
        schema=_SCHEMA,
    )

    op.create_table(
        "sla_targets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("priority", sa.String(40), nullable=True),
        sa.Column("target_seconds", sa.Integer(), nullable=False),
        sa.Column("warning_seconds", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_sla_targets_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["mod_sla.sla_policies.tenant_id", "mod_sla.sla_policies.id"],
            ondelete="CASCADE",
            name="fk_sla_targets_tenant_policy",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sla_targets_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "policy_id",
            "kind",
            "priority",
            name="uq_sla_targets_tenant_policy_kind_priority",
        ),
        sa.CheckConstraint(f"kind IN ({_values(_KINDS)})", name="ck_sla_targets_kind"),
        sa.CheckConstraint("target_seconds > 0", name="ck_sla_targets_target_positive"),
        sa.CheckConstraint(
            "warning_seconds IS NULL OR "
            "(warning_seconds > 0 AND warning_seconds < target_seconds)",
            name="ck_sla_targets_warning_inside_target",
        ),
        schema=_SCHEMA,
    )
    # A plain UNIQUE permits many NULLs, so "one default per policy and kind"
    # has to be said as a partial index or it is not said at all.
    op.create_index(
        "uq_sla_targets_default_per_kind",
        "sla_targets",
        ["tenant_id", "policy_id", "kind"],
        schema=_SCHEMA,
        unique=True,
        postgresql_where=sa.text("priority IS NULL"),
    )

    op.create_table(
        "sla_clocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(60), nullable=False),
        sa.Column("subject_reference", sa.String(180), nullable=False),
        sa.Column("dedup_key", sa.String(180), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("priority", sa.String(40), nullable=True),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("warn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "total_paused_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_reason", sa.Text(), nullable=True),
        sa.Column("breached_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_sla_clocks_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            ["mod_sla.sla_policies.tenant_id", "mod_sla.sla_policies.id"],
            name="fk_sla_clocks_tenant_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "target_id"],
            ["mod_sla.sla_targets.tenant_id", "mod_sla.sla_targets.id"],
            name="fk_sla_clocks_tenant_target",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sla_clocks_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "dedup_key", name="uq_sla_clocks_tenant_dedup_key"
        ),
        sa.CheckConstraint(f"kind IN ({_values(_KINDS)})", name="ck_sla_clocks_kind"),
        sa.CheckConstraint(
            f"status IN ({_values(_CLOCK_STATUSES)})", name="ck_sla_clocks_status"
        ),
        sa.CheckConstraint(
            "total_paused_seconds >= 0", name="ck_sla_clocks_paused_not_negative"
        ),
        sa.CheckConstraint(
            "(status = 'PAUSED') = (paused_at IS NOT NULL)",
            name="ck_sla_clocks_paused_coherence",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_sla_clocks_live_subject_kind",
        "sla_clocks",
        ["tenant_id", "subject_reference", "kind"],
        schema=_SCHEMA,
        unique=True,
        postgresql_where=sa.text("status IN ('RUNNING', 'PAUSED')"),
    )
    # The sweep reads the front of this index, never the table.
    op.create_index(
        "ix_sla_clocks_tenant_status_due",
        "sla_clocks",
        ["tenant_id", "status", "due_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sla_clocks_tenant_subject_started",
        "sla_clocks",
        ["tenant_id", "subject_reference", "started_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "sla_clock_pauses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("clock_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(30), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_reference", sa.String(160), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_sla_clock_pauses_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "clock_id"],
            ["mod_sla.sla_clocks.tenant_id", "mod_sla.sla_clocks.id"],
            ondelete="CASCADE",
            name="fk_sla_clock_pauses_tenant_clock",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sla_clock_pauses_tenant_id_id"),
        sa.CheckConstraint(
            f"reason IN ({_values(_PAUSE_REASONS)})",
            name="ck_sla_clock_pauses_reason",
        ),
        sa.CheckConstraint(
            "resumed_at IS NULL OR resumed_at >= paused_at",
            name="ck_sla_clock_pauses_ordered",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_sla_clock_pauses_open_clock",
        "sla_clock_pauses",
        ["tenant_id", "clock_id"],
        schema=_SCHEMA,
        unique=True,
        postgresql_where=sa.text("resumed_at IS NULL"),
    )

    op.create_table(
        "sla_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("clock_id", sa.Uuid(), nullable=False),
        sa.Column("dedup_key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_sla_observations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "clock_id"],
            ["mod_sla.sla_clocks.tenant_id", "mod_sla.sla_clocks.id"],
            ondelete="CASCADE",
            name="fk_sla_observations_tenant_clock",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_sla_observations_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "dedup_key", name="uq_sla_observations_tenant_dedup_key"
        ),
        sa.CheckConstraint(
            f"kind IN ({_values(_OBSERVATIONS)})", name="ck_sla_observations_kind"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_sla_observations_tenant_clock_time",
        "sla_observations",
        ["tenant_id", "clock_id", "observed_at"],
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_sla.refuse_observation_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'response obligation observations are append-only'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER sla_observations_append_only "
        "BEFORE UPDATE OR DELETE ON mod_sla.sla_observations "
        "FOR EACH ROW EXECUTE FUNCTION mod_sla.refuse_observation_mutation();"
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_sla.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_sla.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_sla.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON mod_sla.{table} TO app_user;"
        )


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_sla.refuse_observation_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_sla;")
