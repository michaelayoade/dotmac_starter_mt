"""Create service-access inputs and desired decisions.

Revision ID: sa_0001_access_policy
Revises: (lineage root)
Create Date: 2026-08-20
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "sa_0001_access_policy"
down_revision = None
branch_labels = ("service_access_policy",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_svc_access"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_svc_access;")
    op.execute("REVOKE ALL ON SCHEMA mod_svc_access FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_svc_access TO app_user, app_admin;")
    op.create_table(
        "service_access_inputs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("service_reference", sa.String(160), nullable=False),
        sa.Column("signal", sa.String(40), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("source_reference", sa.String(160), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_access_inputs_tenant",
        ),
        sa.CheckConstraint(
            "signal IN ('FUP_EXHAUSTED', 'PREPAID_DEPLETED', "
            "'COLLECTIONS_HOLD', 'ADMIN_HOLD')",
            name="ck_service_access_inputs_signal",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_service_access_inputs_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "service_reference",
            "signal",
            name="uq_service_access_inputs_tenant_service_signal",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_access_inputs_tenant_service",
        "service_access_inputs",
        ["tenant_id", "service_reference"],
        schema=_SCHEMA,
    )
    op.create_table(
        "desired_access_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("service_reference", sa.String(160), nullable=False),
        sa.Column("desired_access", sa.String(20), nullable=False),
        sa.Column("reason_code", sa.String(60), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_desired_access_decisions_tenant",
        ),
        sa.CheckConstraint(
            "desired_access IN ('ALLOW', 'RESTRICT', 'DENY')",
            name="ck_desired_access_decisions_state",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_desired_access_decisions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "service_reference",
            name="uq_desired_access_decisions_tenant_service",
        ),
        schema=_SCHEMA,
    )
    op.execute(
        "ALTER TABLE mod_svc_access.service_access_inputs ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_access.service_access_inputs FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY service_access_inputs_tenant_isolation ON mod_svc_access.service_access_inputs USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_access.service_access_inputs TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_svc_access.desired_access_decisions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_access.desired_access_decisions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY desired_access_decisions_tenant_isolation ON mod_svc_access.desired_access_decisions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_access.desired_access_decisions TO app_user;"
    )


def downgrade() -> None:
    op.drop_table("desired_access_decisions", schema=_SCHEMA)
    op.drop_table("service_access_inputs", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_svc_access;")
