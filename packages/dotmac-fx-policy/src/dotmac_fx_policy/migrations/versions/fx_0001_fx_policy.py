"""Create effective FX observation and policy tables.

Revision ID: fx_0001_fx_policy
Revises: (lineage root)
Create Date: 2026-08-21
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "fx_0001_fx_policy"
down_revision = None
branch_labels = ("fx_policy",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_fx_policy"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_fx_policy;")
    op.execute("REVOKE ALL ON SCHEMA mod_fx_policy FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_fx_policy TO app_user, app_admin;")
    op.create_table(
        "fx_rate_types",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_fx_rate_types_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_fx_rate_types_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fx_rate_types_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "fx_rate_sources",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_fx_rate_sources_tenant",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_fx_rate_sources_priority"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_fx_rate_sources_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fx_rate_sources_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "fx_selection_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("rate_type_id", sa.Uuid(), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preferred_source_id", sa.Uuid(), nullable=True),
        sa.Column("allow_inverse", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_fx_selection_policies_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rate_type_id"],
            ["mod_fx_policy.fx_rate_types.tenant_id", "mod_fx_policy.fx_rate_types.id"],
            ondelete="CASCADE",
            name="fk_fx_selection_policies_tenant_rate_type",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "preferred_source_id"],
            [
                "mod_fx_policy.fx_rate_sources.tenant_id",
                "mod_fx_policy.fx_rate_sources.id",
            ],
            name="fk_fx_selection_policies_tenant_source",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_fx_selection_policies_window",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fx_selection_policies_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "rate_type_id",
            "base_currency",
            "quote_currency",
            "effective_from",
            name="uq_fx_selection_policy_tenant_pair_effective",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fx_selection_policies_tenant_lookup",
        "fx_selection_policies",
        [
            "tenant_id",
            "rate_type_id",
            "base_currency",
            "quote_currency",
            "effective_from",
        ],
        schema=_SCHEMA,
    )
    op.create_table(
        "fx_rate_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("rate_type_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(38, 18), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_event_reference", sa.String(180), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_fx_rate_observations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rate_type_id"],
            ["mod_fx_policy.fx_rate_types.tenant_id", "mod_fx_policy.fx_rate_types.id"],
            name="fk_fx_rate_observations_tenant_rate_type",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            [
                "mod_fx_policy.fx_rate_sources.tenant_id",
                "mod_fx_policy.fx_rate_sources.id",
            ],
            name="fk_fx_rate_observations_tenant_source",
        ),
        sa.CheckConstraint("rate > 0", name="ck_fx_rate_observations_positive"),
        sa.CheckConstraint(
            "base_currency <> quote_currency",
            name="ck_fx_rate_observations_distinct_pair",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fx_rate_observations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "source_event_reference",
            name="uq_fx_rate_observations_tenant_source_event",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fx_rate_observations_tenant_lookup",
        "fx_rate_observations",
        [
            "tenant_id",
            "rate_type_id",
            "base_currency",
            "quote_currency",
            "effective_at",
        ],
        schema=_SCHEMA,
    )
    op.create_table(
        "fx_rate_determinations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_reference", sa.String(180), nullable=False),
        sa.Column("rate_type_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("quote_currency", sa.String(3), nullable=False),
        sa.Column("rate", sa.Numeric(38, 18), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("inverted", sa.Boolean(), nullable=False),
        sa.Column("determined_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_fx_rate_determinations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rate_type_id"],
            ["mod_fx_policy.fx_rate_types.tenant_id", "mod_fx_policy.fx_rate_types.id"],
            name="fk_fx_rate_determinations_tenant_rate_type",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "mod_fx_policy.fx_selection_policies.tenant_id",
                "mod_fx_policy.fx_selection_policies.id",
            ],
            name="fk_fx_rate_determinations_tenant_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                "mod_fx_policy.fx_rate_observations.tenant_id",
                "mod_fx_policy.fx_rate_observations.id",
            ],
            name="fk_fx_rate_determinations_tenant_observation",
        ),
        sa.CheckConstraint("rate > 0", name="ck_fx_rate_determinations_positive"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fx_rate_determinations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "request_reference",
            name="uq_fx_rate_determinations_tenant_request",
        ),
        schema=_SCHEMA,
    )
    op.execute("ALTER TABLE mod_fx_policy.fx_rate_types ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_fx_policy.fx_rate_types FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY fx_rate_types_tenant_isolation ON mod_fx_policy.fx_rate_types USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_fx_policy.fx_rate_types TO app_user;"
    )
    op.execute("ALTER TABLE mod_fx_policy.fx_rate_sources ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_fx_policy.fx_rate_sources FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY fx_rate_sources_tenant_isolation ON mod_fx_policy.fx_rate_sources USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_fx_policy.fx_rate_sources TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_fx_policy.fx_selection_policies ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_fx_policy.fx_selection_policies FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY fx_selection_policies_tenant_isolation ON mod_fx_policy.fx_selection_policies USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_fx_policy.fx_selection_policies TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_fx_policy.fx_rate_observations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_fx_policy.fx_rate_observations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY fx_rate_observations_tenant_isolation ON mod_fx_policy.fx_rate_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_fx_policy.fx_rate_observations TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_fx_policy.fx_rate_determinations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_fx_policy.fx_rate_determinations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY fx_rate_determinations_tenant_isolation ON mod_fx_policy.fx_rate_determinations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_fx_policy.fx_rate_determinations TO app_user;"
    )


def downgrade() -> None:
    op.drop_table("fx_rate_determinations", schema=_SCHEMA)
    op.drop_table("fx_rate_observations", schema=_SCHEMA)
    op.drop_table("fx_selection_policies", schema=_SCHEMA)
    op.drop_table("fx_rate_sources", schema=_SCHEMA)
    op.drop_table("fx_rate_types", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_fx_policy;")
