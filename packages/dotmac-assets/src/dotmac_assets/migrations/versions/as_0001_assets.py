"""Create the tenant-only durable-assets owner in ``mod_assets``.

Revision ID: as_0001_assets
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "as_0001_assets"
down_revision = None
branch_labels = ("assets",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_assets"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_assets;")
    op.execute("GRANT USAGE ON SCHEMA mod_assets TO app_user, platform_api;")

    op.create_table(
        "assets",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("serial_number", sa.String(120), nullable=True),
        sa.Column("tag", sa.String(120), nullable=True),
        sa.Column("manufacturer", sa.String(120), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("acquired_on", sa.Date(), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("condition", sa.String(24), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("source_ref", sa.String(240), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_assets_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_assets_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_assets_tenant_code"),
        sa.UniqueConstraint(
            "tenant_id", "serial_number", name="uq_assets_tenant_serial"
        ),
        sa.UniqueConstraint("tenant_id", "tag", name="uq_assets_tenant_tag"),
        sa.CheckConstraint(
            "state IN ('registered', 'in_service', 'out_of_service', 'retired', 'disposed')",
            name="ck_assets_state",
        ),
        sa.CheckConstraint(
            "condition IN ('new', 'good', 'fair', 'poor', 'damaged')",
            name="ck_assets_condition",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_assets_tenant_state", "assets", ["tenant_id", "state"], schema=_SCHEMA
    )
    op.create_index(
        "ix_assets_tenant_kind", "assets", ["tenant_id", "kind"], schema=_SCHEMA
    )

    op.create_table(
        "asset_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("custodian_id", sa.Uuid(), nullable=False),
        sa.Column("preceding_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("expected_return_on", sa.Date(), nullable=True),
        sa.Column("ended_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("condition_on_issue", sa.String(24), nullable=True),
        sa.Column("condition_on_return", sa.String(24), nullable=True),
        sa.Column("location_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("ended_by_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_asset_assignments_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["mod_assets.assets.tenant_id", "mod_assets.assets.id"],
            name="fk_asset_assignments_asset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "preceding_assignment_id"],
            [
                "mod_assets.asset_assignments.tenant_id",
                "mod_assets.asset_assignments.id",
            ],
            name="fk_asset_assignments_preceding",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_asset_assignments_tenant_id_id"
        ),
        sa.CheckConstraint(
            "preceding_assignment_id IS NULL OR preceding_assignment_id <> id",
            name="ck_asset_assignments_no_self_predecessor",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'returned', 'transferred', 'lost')",
            name="ck_asset_assignments_status",
        ),
        sa.CheckConstraint(
            "condition_on_issue IS NULL OR condition_on_issue IN ('new', 'good', 'fair', 'poor', 'damaged')",
            name="ck_asset_assignments_issue_condition",
        ),
        sa.CheckConstraint(
            "condition_on_return IS NULL OR condition_on_return IN ('new', 'good', 'fair', 'poor', 'damaged')",
            name="ck_asset_assignments_return_condition",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_asset_assignments_one_active",
        "asset_assignments",
        ["tenant_id", "asset_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_asset_assignments_custodian",
        "asset_assignments",
        ["tenant_id", "custodian_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "asset_maintenance",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("summary", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("scheduled_for", sa.Date(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_on", sa.Date(), nullable=True),
        sa.Column("asset_state_before", sa.String(24), nullable=True),
        sa.Column("work_performed", sa.Text(), nullable=True),
        sa.Column("next_due_on", sa.Date(), nullable=True),
        sa.Column("provider_ref", sa.String(240), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("completed_by_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_asset_maintenance_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["mod_assets.assets.tenant_id", "mod_assets.assets.id"],
            name="fk_asset_maintenance_asset",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_asset_maintenance_tenant_id_id"
        ),
        sa.CheckConstraint(
            "kind IN ('preventive', 'corrective', 'inspection', 'other')",
            name="ck_asset_maintenance_kind",
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'in_progress', 'completed', 'cancelled')",
            name="ck_asset_maintenance_status",
        ),
        sa.CheckConstraint(
            "asset_state_before IS NULL OR asset_state_before IN ('registered', 'in_service', 'out_of_service', 'retired', 'disposed')",
            name="ck_asset_maintenance_prior_state",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_asset_maintenance_one_in_progress",
        "asset_maintenance",
        ["tenant_id", "asset_id"],
        unique=True,
        postgresql_where=sa.text("status = 'in_progress'"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_asset_maintenance_due",
        "asset_maintenance",
        ["tenant_id", "status", "scheduled_for"],
        schema=_SCHEMA,
    )

    op.create_table(
        "asset_disposals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("requested_on", sa.Date(), nullable=False),
        sa.Column("requested_by_id", sa.Uuid(), nullable=False),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposed_on", sa.Date(), nullable=True),
        sa.Column("completed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("recipient_ref", sa.String(240), nullable=True),
        sa.Column("external_authorization_ref", sa.String(240), nullable=True),
        sa.Column("external_finance_ref", sa.String(240), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
        _tenant_fk("fk_asset_disposals_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["mod_assets.assets.tenant_id", "mod_assets.assets.id"],
            name="fk_asset_disposals_asset",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_asset_disposals_tenant_id_id"),
        sa.CheckConstraint(
            "method IN ('sale', 'scrap', 'donation', 'theft', 'insurance', 'trade_in', 'transfer')",
            name="ck_asset_disposals_method",
        ),
        sa.CheckConstraint(
            "status IN ('requested', 'approved', 'completed', 'cancelled')",
            name="ck_asset_disposals_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_asset_disposals_one_open",
        "asset_disposals",
        ["tenant_id", "asset_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'approved')"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_asset_disposals_status",
        "asset_disposals",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "asset_lifecycle_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("source_id", sa.Uuid(), nullable=True),
        sa.Column("previous_state", sa.String(24), nullable=True),
        sa.Column("new_state", sa.String(24), nullable=True),
        sa.Column("previous_custodian_id", sa.Uuid(), nullable=True),
        sa.Column("new_custodian_id", sa.Uuid(), nullable=True),
        sa.Column("previous_location_id", sa.Uuid(), nullable=True),
        sa.Column("new_location_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _tenant_fk("fk_asset_lifecycle_events_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["mod_assets.assets.tenant_id", "mod_assets.assets.id"],
            name="fk_asset_lifecycle_events_asset",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_asset_lifecycle_events_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_asset_lifecycle_events_order",
        "asset_lifecycle_events",
        ["tenant_id", "asset_id", "occurred_at", "id"],
        schema=_SCHEMA,
    )

    op.execute("ALTER TABLE mod_assets.assets ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_assets.assets FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY assets_tenant_isolation ON mod_assets.assets
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON mod_assets.assets TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_assets.assets TO platform_api;"
    )

    op.execute("ALTER TABLE mod_assets.asset_assignments ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_assets.asset_assignments FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY asset_assignments_tenant_isolation
            ON mod_assets.asset_assignments
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_assets.asset_assignments TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_assets.asset_assignments TO platform_api;"
    )

    op.execute("ALTER TABLE mod_assets.asset_maintenance ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_assets.asset_maintenance FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY asset_maintenance_tenant_isolation
            ON mod_assets.asset_maintenance
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_assets.asset_maintenance TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_assets.asset_maintenance TO platform_api;"
    )

    op.execute("ALTER TABLE mod_assets.asset_disposals ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_assets.asset_disposals FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY asset_disposals_tenant_isolation
            ON mod_assets.asset_disposals
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_assets.asset_disposals TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_assets.asset_disposals TO platform_api;"
    )

    op.execute(
        "ALTER TABLE mod_assets.asset_lifecycle_events ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_assets.asset_lifecycle_events FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY asset_lifecycle_events_tenant_isolation
            ON mod_assets.asset_lifecycle_events
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        """
        CREATE FUNCTION mod_assets.assets_refuse_lifecycle_rewrite()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'asset lifecycle evidence is append-only';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER assets_refuse_lifecycle_rewrite
        BEFORE UPDATE OR DELETE ON mod_assets.asset_lifecycle_events
        FOR EACH ROW EXECUTE FUNCTION mod_assets.assets_refuse_lifecycle_rewrite();
        """
    )
    op.execute("GRANT SELECT, INSERT ON mod_assets.asset_lifecycle_events TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT ON mod_assets.asset_lifecycle_events TO platform_api;"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS assets_refuse_lifecycle_rewrite "
        "ON mod_assets.asset_lifecycle_events;"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_assets.assets_refuse_lifecycle_rewrite();")
    op.drop_index(
        "ix_asset_lifecycle_events_order",
        table_name="asset_lifecycle_events",
        schema=_SCHEMA,
    )
    op.drop_table("asset_lifecycle_events", schema=_SCHEMA)
    op.drop_index(
        "ix_asset_disposals_status", table_name="asset_disposals", schema=_SCHEMA
    )
    op.drop_index(
        "uq_asset_disposals_one_open", table_name="asset_disposals", schema=_SCHEMA
    )
    op.drop_table("asset_disposals", schema=_SCHEMA)
    op.drop_index(
        "ix_asset_maintenance_due", table_name="asset_maintenance", schema=_SCHEMA
    )
    op.drop_index(
        "uq_asset_maintenance_one_in_progress",
        table_name="asset_maintenance",
        schema=_SCHEMA,
    )
    op.drop_table("asset_maintenance", schema=_SCHEMA)
    op.drop_index(
        "ix_asset_assignments_custodian",
        table_name="asset_assignments",
        schema=_SCHEMA,
    )
    op.drop_index(
        "uq_asset_assignments_one_active",
        table_name="asset_assignments",
        schema=_SCHEMA,
    )
    op.drop_table("asset_assignments", schema=_SCHEMA)
    op.drop_index("ix_assets_tenant_kind", table_name="assets", schema=_SCHEMA)
    op.drop_index("ix_assets_tenant_state", table_name="assets", schema=_SCHEMA)
    op.drop_table("assets", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_assets RESTRICT;")
