"""Create the tenant editorial content plane.

Revision ID: ct_0001_content
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ct_0001_content"
down_revision = None
branch_labels = ("content",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_content"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_content;")
    op.execute("REVOKE ALL ON SCHEMA mod_content FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_content TO app_user, app_admin;")

    op.create_table(
        "content_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("starts_on", sa.Date(), nullable=True),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("created_by_ref", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_content_plans_tenant",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'paused', 'completed', 'archived')",
            name="ck_content_plans_status",
        ),
        sa.CheckConstraint(
            "ends_on IS NULL OR starts_on IS NULL OR ends_on >= starts_on",
            name="ck_content_plans_date_order",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_content_plans_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_content_plans_tenant_status",
        "content_plans",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "content_items",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("content_plan_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="draft"),
        sa.Column("planned_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_ref", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_content_items_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "content_plan_id"],
            ["mod_content.content_plans.tenant_id", "mod_content.content_plans.id"],
            ondelete="CASCADE",
            name="fk_content_items_tenant_plan",
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'ready', 'archived')",
            name="ck_content_items_state",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_content_items_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_content_items_tenant_plan",
        "content_items",
        ["tenant_id", "content_plan_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_content_items_tenant_state",
        "content_items",
        ["tenant_id", "state"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_content_items_tenant_planned",
        "content_items",
        ["tenant_id", "planned_for"],
        schema=_SCHEMA,
    )

    op.create_table(
        "content_variants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("content_item_id", sa.Uuid(), nullable=False),
        sa.Column("variant_key", sa.String(120), nullable=False),
        sa.Column("title_override", sa.String(300), nullable=True),
        sa.Column("body_override", sa.Text(), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_content_variants_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "content_item_id"],
            ["mod_content.content_items.tenant_id", "mod_content.content_items.id"],
            ondelete="CASCADE",
            name="fk_content_variants_tenant_item",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_content_variants_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "content_item_id",
            "variant_key",
            name="uq_content_variants_tenant_item_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_content_variants_tenant_item",
        "content_variants",
        ["tenant_id", "content_item_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "content_plan_creatives",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("content_plan_id", sa.Uuid(), nullable=False),
        sa.Column("file_ref", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(80), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("alt_text", sa.String(500), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_content_plan_creatives_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "content_plan_id"],
            ["mod_content.content_plans.tenant_id", "mod_content.content_plans.id"],
            ondelete="CASCADE",
            name="fk_content_plan_creatives_tenant_plan",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_content_plan_creatives_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "content_plan_id",
            "file_ref",
            "role",
            name="uq_content_plan_creatives_tenant_plan_file_role",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_content_plan_creatives_tenant_plan",
        "content_plan_creatives",
        ["tenant_id", "content_plan_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "content_item_creatives",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("content_item_id", sa.Uuid(), nullable=False),
        sa.Column("file_ref", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(80), nullable=False),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("alt_text", sa.String(500), nullable=True),
        sa.Column(
            "sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_content_item_creatives_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "content_item_id"],
            ["mod_content.content_items.tenant_id", "mod_content.content_items.id"],
            ondelete="CASCADE",
            name="fk_content_item_creatives_tenant_item",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_content_item_creatives_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "content_item_id",
            "file_ref",
            "role",
            name="uq_content_item_creatives_tenant_item_file_role",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_content_item_creatives_tenant_item",
        "content_item_creatives",
        ["tenant_id", "content_item_id"],
        schema=_SCHEMA,
    )

    op.execute("ALTER TABLE mod_content.content_plans ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_content.content_plans FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY content_plans_tenant_isolation "
        "ON mod_content.content_plans "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_content.content_plans TO app_user;"
    )
    op.execute("ALTER TABLE mod_content.content_items ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_content.content_items FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY content_items_tenant_isolation "
        "ON mod_content.content_items "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_content.content_items TO app_user;"
    )
    op.execute("ALTER TABLE mod_content.content_variants ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_content.content_variants FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY content_variants_tenant_isolation "
        "ON mod_content.content_variants "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_content.content_variants TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_content.content_plan_creatives " "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_content.content_plan_creatives " "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY content_plan_creatives_tenant_isolation "
        "ON mod_content.content_plan_creatives "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_content.content_plan_creatives TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_content.content_item_creatives " "ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_content.content_item_creatives " "FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY content_item_creatives_tenant_isolation "
        "ON mod_content.content_item_creatives "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_content.content_item_creatives TO app_user;"
    )


def downgrade() -> None:
    for table in (
        "content_item_creatives",
        "content_plan_creatives",
        "content_variants",
        "content_items",
        "content_plans",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_content;")
