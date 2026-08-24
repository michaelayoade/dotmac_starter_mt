"""Create the versioned tenant technical service catalogue.

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
        "plan_families",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_plan_families_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_plan_families_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_plan_families_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "plan_family_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_family_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_code", sa.String(120), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_plan_family_versions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "plan_family_id"],
            ["mod_svc_cat.plan_families.tenant_id", "mod_svc_cat.plan_families.id"],
            ondelete="CASCADE",
            name="fk_plan_family_versions_family",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_plan_family_versions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "plan_family_id",
            name="uq_plan_family_versions_tenant_id_family",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "plan_family_id",
            "version",
            name="uq_plan_family_versions_identity",
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_plan_family_versions_command"
        ),
        sa.CheckConstraint("version > 0", name="ck_plan_family_versions_version"),
        sa.CheckConstraint(
            "source_version > 0", name="ck_plan_family_versions_source_version"
        ),
        sa.CheckConstraint(
            "state IN ('published', 'superseded', 'withdrawn')",
            name="ck_plan_family_versions_state",
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_plan_family_versions_interval",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_plan_family_versions_effective",
        "plan_family_versions",
        ["tenant_id", "plan_family_id", "effective_from"],
        schema=_SCHEMA,
    )
    op.create_table(
        "service_specifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("plan_family_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_specifications_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "plan_family_id"],
            ["mod_svc_cat.plan_families.tenant_id", "mod_svc_cat.plan_families.id"],
            ondelete="RESTRICT",
            name="fk_service_specifications_family",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_service_specifications_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "plan_family_id",
            name="uq_service_specifications_tenant_id_family",
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_service_specifications_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_specifications_tenant_family",
        "service_specifications",
        ["tenant_id", "plan_family_id"],
        schema=_SCHEMA,
    )
    op.create_table(
        "service_specification_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("specification_id", sa.Uuid(), nullable=False),
        sa.Column("plan_family_id", sa.Uuid(), nullable=False),
        sa.Column("plan_family_version_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_code", sa.String(120), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_specification_versions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "specification_id", "plan_family_id"],
            [
                "mod_svc_cat.service_specifications.tenant_id",
                "mod_svc_cat.service_specifications.id",
                "mod_svc_cat.service_specifications.plan_family_id",
            ],
            ondelete="CASCADE",
            name="fk_service_specification_versions_specification",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "plan_family_version_id", "plan_family_id"],
            [
                "mod_svc_cat.plan_family_versions.tenant_id",
                "mod_svc_cat.plan_family_versions.id",
                "mod_svc_cat.plan_family_versions.plan_family_id",
            ],
            ondelete="RESTRICT",
            name="fk_service_specification_versions_family_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_service_specification_versions_tenant_id_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "specification_id",
            name="uq_service_specification_versions_tenant_id_specification",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "specification_id",
            "version",
            name="uq_service_specification_versions_identity",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "command_id",
            name="uq_service_specification_versions_command",
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_service_specification_versions_version"
        ),
        sa.CheckConstraint(
            "source_version > 0",
            name="ck_service_specification_versions_source_version",
        ),
        sa.CheckConstraint(
            "state IN ('published', 'superseded', 'withdrawn')",
            name="ck_service_specification_versions_state",
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_service_specification_versions_interval",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_service_specification_versions_effective",
        "service_specification_versions",
        ["tenant_id", "specification_id", "effective_from"],
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
            "id",
            "specification_id",
            name="uq_characteristic_definitions_tenant_id_specification",
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
        "service_specification_characteristics",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("specification_version_id", sa.Uuid(), nullable=False),
        sa.Column("specification_id", sa.Uuid(), nullable=False),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("string_value", sa.Text(), nullable=True),
        sa.Column("integer_value", sa.Integer(), nullable=True),
        sa.Column("decimal_value", sa.Numeric(20, 6), nullable=True),
        sa.Column("boolean_value", sa.Boolean(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_service_specification_characteristics_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "specification_version_id", "specification_id"],
            [
                "mod_svc_cat.service_specification_versions.tenant_id",
                "mod_svc_cat.service_specification_versions.id",
                "mod_svc_cat.service_specification_versions.specification_id",
            ],
            ondelete="CASCADE",
            name="fk_service_specification_characteristics_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "definition_id", "specification_id"],
            [
                "mod_svc_cat.characteristic_definitions.tenant_id",
                "mod_svc_cat.characteristic_definitions.id",
                "mod_svc_cat.characteristic_definitions.specification_id",
            ],
            ondelete="RESTRICT",
            name="fk_service_specification_characteristics_definition",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_service_specification_characteristics_tenant_id_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "specification_version_id",
            "definition_id",
            name="uq_service_specification_characteristics_definition",
        ),
        sa.CheckConstraint(
            "(CASE WHEN string_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN integer_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN decimal_value IS NULL THEN 0 ELSE 1 END + "
            "CASE WHEN boolean_value IS NULL THEN 0 ELSE 1 END) = 1",
            name="ck_service_specification_characteristics_one_value",
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

    op.execute("ALTER TABLE mod_svc_cat.plan_families ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_svc_cat.plan_families FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY plan_families_tenant_isolation ON mod_svc_cat.plan_families TO app_user USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.plan_families TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.plan_family_versions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_svc_cat.plan_family_versions FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY plan_family_versions_tenant_isolation ON mod_svc_cat.plan_family_versions TO app_user USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.plan_family_versions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.service_specifications ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.service_specifications FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY service_specifications_tenant_isolation ON mod_svc_cat.service_specifications TO app_user USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.service_specifications TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.service_specification_versions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.service_specification_versions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY service_specification_versions_tenant_isolation ON mod_svc_cat.service_specification_versions TO app_user USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.service_specification_versions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.characteristic_definitions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.characteristic_definitions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY characteristic_definitions_tenant_isolation ON mod_svc_cat.characteristic_definitions TO app_user USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.characteristic_definitions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.service_specification_characteristics ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.service_specification_characteristics FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY service_specification_characteristics_tenant_isolation ON mod_svc_cat.service_specification_characteristics TO app_user USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.service_specification_characteristics TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.eligibility_input_definitions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_svc_cat.eligibility_input_definitions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY eligibility_input_definitions_tenant_isolation ON mod_svc_cat.eligibility_input_definitions TO app_user USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_svc_cat.eligibility_input_definitions TO app_user;"
    )


def downgrade() -> None:
    for table in (
        "eligibility_input_definitions",
        "service_specification_characteristics",
        "characteristic_definitions",
        "service_specification_versions",
        "service_specifications",
        "plan_family_versions",
        "plan_families",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_svc_cat;")
