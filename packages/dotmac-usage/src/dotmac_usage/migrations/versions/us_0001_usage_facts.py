"""Create normalized usage observations, corrections, and aggregates.

Revision ID: us_0001_usage_facts
Revises: (lineage root)
Create Date: 2026-08-20
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "us_0001_usage_facts"
down_revision = None
branch_labels = ("usage",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_usage"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_usage;")
    op.execute("REVOKE ALL ON SCHEMA mod_usage FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_usage TO app_user, app_admin;")
    op.create_table(
        "usage_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("service_reference", sa.String(160), nullable=False),
        sa.Column("meter_code", sa.String(80), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("source_reference", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(180), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_usage_observations_tenant",
        ),
        sa.CheckConstraint(
            "period_end > period_start", name="ck_usage_observations_period"
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_usage_observations_quantity"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_usage_observations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_reference",
            "source_event_id",
            name="uq_usage_observations_tenant_source_event",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_usage_observations_tenant_service_period",
        "usage_observations",
        ["tenant_id", "service_reference", "period_start"],
        schema=_SCHEMA,
    )
    op.create_table(
        "usage_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("delta_quantity", sa.Numeric(24, 6), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_usage_corrections_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                "mod_usage.usage_observations.tenant_id",
                "mod_usage.usage_observations.id",
            ],
            ondelete="CASCADE",
            name="fk_usage_corrections_tenant_observation",
        ),
        sa.CheckConstraint("delta_quantity <> 0", name="ck_usage_corrections_nonzero"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_usage_corrections_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_usage_corrections_tenant_observation",
        "usage_corrections",
        ["tenant_id", "observation_id"],
        schema=_SCHEMA,
    )
    op.create_table(
        "usage_aggregates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("service_reference", sa.String(160), nullable=False),
        sa.Column("meter_code", sa.String(80), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 6), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_usage_aggregates_tenant",
        ),
        sa.CheckConstraint(
            "window_end > window_start", name="ck_usage_aggregates_window"
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_usage_aggregates_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "service_reference",
            "meter_code",
            "window_start",
            "window_end",
            name="uq_usage_aggregates_tenant_window",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_usage_aggregates_tenant_service_window",
        "usage_aggregates",
        ["tenant_id", "service_reference", "window_start"],
        schema=_SCHEMA,
    )
    op.execute("ALTER TABLE mod_usage.usage_observations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_usage.usage_observations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY usage_observations_tenant_isolation ON mod_usage.usage_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_usage.usage_observations TO app_user;"
    )
    op.execute("ALTER TABLE mod_usage.usage_corrections ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_usage.usage_corrections FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY usage_corrections_tenant_isolation ON mod_usage.usage_corrections USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_usage.usage_corrections TO app_user;"
    )
    op.execute("ALTER TABLE mod_usage.usage_aggregates ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_usage.usage_aggregates FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY usage_aggregates_tenant_isolation ON mod_usage.usage_aggregates USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_usage.usage_aggregates TO app_user;"
    )


def downgrade() -> None:
    op.drop_table("usage_aggregates", schema=_SCHEMA)
    op.drop_table("usage_corrections", schema=_SCHEMA)
    op.drop_table("usage_observations", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_usage;")
