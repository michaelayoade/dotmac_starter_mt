"""Create usage-rating rules and pre-tax obligations.

Revision ID: ur_0001_usage_rating
Revises: (lineage root)
Create Date: 2026-08-20
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ur_0001_usage_rating"
down_revision = None
branch_labels = ("usage_rating",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_usage_rate"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_usage_rate;")
    op.execute("REVOKE ALL ON SCHEMA mod_usage_rate FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_usage_rate TO app_user, app_admin;")
    op.create_table(
        "rating_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("meter_code", sa.String(80), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("unit_price", sa.Numeric(24, 6), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_rating_rules_tenant",
        ),
        sa.CheckConstraint("unit_price >= 0", name="ck_rating_rules_unit_price"),
        sa.CheckConstraint(
            "char_length(currency) = 3", name="ck_rating_rules_currency"
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_rating_rules_effective_window",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_rating_rules_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_rating_rules_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_rating_rules_tenant_meter_effective",
        "rating_rules",
        ["tenant_id", "meter_code", "effective_from"],
        schema=_SCHEMA,
    )
    op.create_table(
        "rated_usage_obligations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("usage_reference", sa.String(180), nullable=False),
        sa.Column("service_reference", sa.String(160), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(24, 6), nullable=False),
        sa.Column("net_amount", sa.Numeric(24, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("usage_occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rated_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_rated_usage_obligations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["mod_usage_rate.rating_rules.tenant_id", "mod_usage_rate.rating_rules.id"],
            name="fk_rated_usage_obligations_tenant_rule",
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_rated_usage_quantity"),
        sa.CheckConstraint("unit_price >= 0", name="ck_rated_usage_unit_price"),
        sa.CheckConstraint("net_amount >= 0", name="ck_rated_usage_net_amount"),
        sa.CheckConstraint("char_length(currency) = 3", name="ck_rated_usage_currency"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_rated_usage_obligations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "usage_reference",
            "rule_id",
            name="uq_rated_usage_obligations_tenant_usage_rule",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_rated_usage_obligations_tenant_service_time",
        "rated_usage_obligations",
        ["tenant_id", "service_reference", "usage_occurred_at"],
        schema=_SCHEMA,
    )
    op.execute("ALTER TABLE mod_usage_rate.rating_rules ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_usage_rate.rating_rules FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY rating_rules_tenant_isolation ON mod_usage_rate.rating_rules USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_usage_rate.rating_rules TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_usage_rate.rated_usage_obligations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_usage_rate.rated_usage_obligations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY rated_usage_obligations_tenant_isolation ON mod_usage_rate.rated_usage_obligations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_usage_rate.rated_usage_obligations TO app_user;"
    )


def downgrade() -> None:
    op.drop_table("rated_usage_obligations", schema=_SCHEMA)
    op.drop_table("rating_rules", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_usage_rate;")
