"""Create the tenant Forms owner.

Revision ID: fm_0001_forms
Revises: (lineage root)
Create Date: 2026-08-21

Every table carries tenant_id NOT NULL and UNIQUE (tenant_id, id).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from alembic import op
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

revision = "fm_0001_forms"
down_revision = None
branch_labels = ("forms",)

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_forms"
_TABLES = (
    "forms",
    "form_versions",
    "form_sections",
    "form_fields",
    "form_field_options",
    "form_submissions",
    "form_answers",
)


def _identity() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
    )


def _tenant_constraints(name: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id"),
    )


def _timestamps() -> tuple[sa.Column[Any], ...]:
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


def _secure_tenant_tables(tables: Iterable[str]) -> None:
    for table in tables:
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {_SCHEMA}.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.{table} TO app_user;"
        )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_forms;")
    op.execute("REVOKE ALL ON SCHEMA mod_forms FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_forms TO app_user, app_admin;")

    op.create_table(
        "forms",
        *_identity(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("form_type", sa.String(80), nullable=False),
        sa.Column("owner_ref", sa.String(255), nullable=True),
        sa.Column("published_version_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        *_tenant_constraints("forms"),
        schema=_SCHEMA,
    )
    op.create_index("ix_forms_tenant_type", "forms", ["tenant_id", "form_type"], schema=_SCHEMA)

    op.create_table(
        "form_versions",
        *_identity(),
        sa.Column("form_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("content_digest", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant_constraints("form_versions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "form_id"],
            ["mod_forms.forms.tenant_id", "mod_forms.forms.id"],
            ondelete="CASCADE",
            name="fk_form_versions_form",
        ),
        sa.UniqueConstraint(
            "tenant_id", "form_id", "version_number", name="uq_form_versions_tenant_form_number"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')", name="ck_form_versions_status"
        ),
        sa.CheckConstraint("version_number > 0", name="ck_form_versions_number"),
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_forms_published_version",
        "forms",
        "form_versions",
        ["tenant_id", "published_version_id"],
        ["tenant_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "form_sections",
        *_identity(),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        *_tenant_constraints("form_sections"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["mod_forms.form_versions.tenant_id", "mod_forms.form_versions.id"],
            ondelete="CASCADE",
            name="fk_form_sections_version",
        ),
        sa.UniqueConstraint(
            "tenant_id", "version_id", "id", name="uq_form_sections_tenant_version_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "version_id", "key", name="uq_form_sections_version_key"
        ),
        sa.UniqueConstraint(
            "tenant_id", "version_id", "position", name="uq_form_sections_version_position"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "form_fields",
        *_identity(),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("section_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("label", sa.String(240), nullable=False),
        sa.Column("field_type", sa.String(32), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("help_text", sa.Text(), nullable=True),
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("validation", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("position", sa.Integer(), nullable=False),
        *_tenant_constraints("form_fields"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id"],
            ["mod_forms.form_versions.tenant_id", "mod_forms.form_versions.id"],
            ondelete="CASCADE",
            name="fk_form_fields_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "version_id", "section_id"],
            [
                "mod_forms.form_sections.tenant_id",
                "mod_forms.form_sections.version_id",
                "mod_forms.form_sections.id",
            ],
            ondelete="CASCADE",
            name="fk_form_fields_section",
        ),
        sa.UniqueConstraint(
            "tenant_id", "version_id", "key", name="uq_form_fields_version_key"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "version_id",
            "section_id",
            "position",
            name="uq_form_fields_section_position",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "form_field_options",
        *_identity(),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("value", sa.String(160), nullable=False),
        sa.Column("label", sa.String(240), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_tenant_constraints("form_field_options"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "field_id"],
            ["mod_forms.form_fields.tenant_id", "mod_forms.form_fields.id"],
            ondelete="CASCADE",
            name="fk_form_field_options_field",
        ),
        sa.UniqueConstraint(
            "tenant_id", "field_id", "value", name="uq_form_field_options_field_value"
        ),
        sa.UniqueConstraint(
            "tenant_id", "field_id", "position", name="uq_form_field_options_field_position"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "form_submissions",
        *_identity(),
        sa.Column("submission_key", sa.String(255), nullable=False),
        sa.Column("form_version_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=True),
        sa.Column("submitted_by_ref", sa.String(255), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="submitted"),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("form_submissions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "form_version_id"],
            ["mod_forms.form_versions.tenant_id", "mod_forms.form_versions.id"],
            ondelete="RESTRICT",
            name="fk_form_submissions_version",
        ),
        sa.UniqueConstraint(
            "tenant_id", "submission_key", name="uq_form_submissions_tenant_key"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_form_submissions_tenant_subject",
        "form_submissions",
        ["tenant_id", "subject_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "form_answers",
        *_identity(),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("field_id", sa.Uuid(), nullable=False),
        sa.Column("field_key_snapshot", sa.String(80), nullable=False),
        sa.Column("field_label_snapshot", sa.String(240), nullable=False),
        sa.Column("field_type_snapshot", sa.String(32), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("display_value", sa.Text(), nullable=True),
        sa.Column("file_ref", sa.String(500), nullable=True),
        *_tenant_constraints("form_answers"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "submission_id"],
            ["mod_forms.form_submissions.tenant_id", "mod_forms.form_submissions.id"],
            ondelete="CASCADE",
            name="fk_form_answers_submission",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "field_id"],
            ["mod_forms.form_fields.tenant_id", "mod_forms.form_fields.id"],
            ondelete="RESTRICT",
            name="fk_form_answers_field",
        ),
        sa.UniqueConstraint(
            "tenant_id", "submission_id", "field_id", name="uq_form_answers_submission_field"
        ),
        schema=_SCHEMA,
    )

    _secure_tenant_tables(_TABLES)


def downgrade() -> None:
    op.drop_constraint(
        "fk_forms_published_version", "forms", schema=_SCHEMA, type_="foreignkey"
    )
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_forms;")
