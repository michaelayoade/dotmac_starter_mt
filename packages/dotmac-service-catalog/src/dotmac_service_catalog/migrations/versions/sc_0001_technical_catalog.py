"""Create the tenant technical service catalogue.

Revision ID: sc_0001_technical_catalog
Revises: (lineage root)
Create Date: 2026-08-20
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "sc_0001_technical_catalog"
down_revision = None
branch_labels = ("service_catalog",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_svc_cat"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_svc_cat;")
    op.execute("REVOKE ALL ON SCHEMA mod_svc_cat FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_svc_cat TO app_user, app_admin;")
    op.create_table(
        "service_specifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_specifications_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_service_specifications_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_service_specifications_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "plan_families",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("specification_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_plan_families_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "specification_id"],
            [
                "mod_svc_cat.service_specifications.tenant_id",
                "mod_svc_cat.service_specifications.id",
            ],
            ondelete="RESTRICT",
            name="fk_plan_families_tenant_specification",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_plan_families_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_plan_families_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_plan_families_tenant_specification",
        "plan_families",
        ["tenant_id", "specification_id"],
        schema=_SCHEMA,
    )
    op.create_table(
        "characteristic_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("specification_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unit", sa.String(32), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_characteristic_definitions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "specification_id"],
            [
                "mod_svc_cat.service_specifications.tenant_id",
                "mod_svc_cat.service_specifications.id",
            ],
            ondelete="CASCADE",
            name="fk_characteristic_definitions_tenant_specification",
        ),
        sa.CheckConstraint(
            "kind IN ('STRING', 'INTEGER', 'DECIMAL', 'BOOLEAN')",
            name="ck_characteristic_definitions_kind",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_characteristic_definitions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "specification_id",
            "code",
            name="uq_characteristic_definitions_tenant_spec_code",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "eligibility_input_definitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("specification_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_eligibility_input_definitions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "specification_id"],
            [
                "mod_svc_cat.service_specifications.tenant_id",
                "mod_svc_cat.service_specifications.id",
            ],
            ondelete="CASCADE",
            name="fk_eligibility_input_definitions_tenant_specification",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_eligibility_input_definitions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "specification_id",
            "code",
            name="uq_eligibility_input_definitions_tenant_spec_code",
        ),
        schema=_SCHEMA,
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.service_specifications ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.service_specifications FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY service_specifications_tenant_isolation ON mod_svc_cat.service_specifications USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.service_specifications TO app_user;"
    )
    op.execute("ALTER TABLE mod_svc_cat.plan_families ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_svc_cat.plan_families FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY plan_families_tenant_isolation ON mod_svc_cat.plan_families USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.plan_families TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.characteristic_definitions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.characteristic_definitions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY characteristic_definitions_tenant_isolation ON mod_svc_cat.characteristic_definitions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.characteristic_definitions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.eligibility_input_definitions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.eligibility_input_definitions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY eligibility_input_definitions_tenant_isolation ON mod_svc_cat.eligibility_input_definitions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.eligibility_input_definitions TO app_user;"
    )


def downgrade() -> None:
    for table in (
        "eligibility_input_definitions",
        "characteristic_definitions",
        "plan_families",
        "service_specifications",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_svc_cat;")
