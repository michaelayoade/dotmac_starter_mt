"""Create service instances and lifecycle events.

Revision ID: se_0001_service_lifecycle
Revises: (lineage root)
Create Date: 2026-08-20
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "se_0001_service_lifecycle"
down_revision = None
branch_labels = ("services",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_services"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_services;")
    op.execute("REVOKE ALL ON SCHEMA mod_services FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_services TO app_user, app_admin;")
    op.create_table(
        "service_instances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("customer_reference", sa.String(160), nullable=False),
        sa.Column("specification_reference", sa.String(160), nullable=False),
        sa.Column("qualification_reference", sa.String(160), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_instances_tenant",
        ),
        sa.CheckConstraint(
            "status IN ('ORDERED', 'ACTIVE', 'SUSPENDED', 'TERMINATED')",
            name="ck_service_instances_status",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_service_instances_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_instances_tenant_customer",
        "service_instances",
        ["tenant_id", "customer_reference"],
        schema=_SCHEMA,
    )
    op.create_table(
        "service_lifecycle_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("service_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=False),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_lifecycle_events_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "service_id"],
            [
                "mod_services.service_instances.tenant_id",
                "mod_services.service_instances.id",
            ],
            ondelete="CASCADE",
            name="fk_service_lifecycle_events_tenant_service",
        ),
        sa.CheckConstraint(
            "from_status IN ('ORDERED', 'ACTIVE', 'SUSPENDED', 'TERMINATED')",
            name="ck_service_lifecycle_events_from_status",
        ),
        sa.CheckConstraint(
            "to_status IN ('ORDERED', 'ACTIVE', 'SUSPENDED', 'TERMINATED')",
            name="ck_service_lifecycle_events_to_status",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_service_lifecycle_events_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_lifecycle_events_tenant_service_time",
        "service_lifecycle_events",
        ["tenant_id", "service_id", "occurred_at"],
        schema=_SCHEMA,
    )
    op.execute("ALTER TABLE mod_services.service_instances ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_services.service_instances FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY service_instances_tenant_isolation ON mod_services.service_instances USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_services.service_instances TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_services.service_lifecycle_events ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_services.service_lifecycle_events FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY service_lifecycle_events_tenant_isolation ON mod_services.service_lifecycle_events USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_services.service_lifecycle_events TO app_user;"
    )


def downgrade() -> None:
    op.drop_table("service_lifecycle_events", schema=_SCHEMA)
    op.drop_table("service_instances", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_services;")
