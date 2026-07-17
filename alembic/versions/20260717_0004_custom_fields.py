"""custom fields

Creates `custom_field_definitions` — per-tenant field metadata for
registrable entities (Task 8, port of `ERP:app/models/finance/automation/
custom_field.py`'s `CustomFieldDefinition`). Standard tenant-scoped RLS
shape: a single `USING/WITH CHECK (tenant_id = app_current_tenant_id())`
policy covering all four commands — same as `parties`/`roles`, not the split
read/write shape `domain_settings` uses (this table has no platform-level
rows).

Also adds `Party.custom_fields JSONB NOT NULL DEFAULT '{}'` — the column
field *values* live in, keyed by `field_code`; it rides on `parties`'
existing RLS policy from the Task 6 migration (no new policy needed here).

Revision ID: 0004_custom_fields
Revises: 0003_party_identity
Create Date: 2026-07-17

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_custom_fields"
down_revision = "0003_party_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _create_custom_field_definitions_table()
    _apply_rls()
    _grant_roles()
    _add_party_custom_fields_column()


def downgrade() -> None:
    _drop_party_custom_fields_column()
    op.execute("REVOKE ALL ON custom_field_definitions FROM app_user, platform_api;")
    op.execute(
        "DROP POLICY IF EXISTS custom_field_definitions_tenant_isolation "
        "ON custom_field_definitions;"
    )
    op.drop_table("custom_field_definitions")


# ─────────────────────────────────────────────────────────────────────────────
# Table
# ─────────────────────────────────────────────────────────────────────────────


def _create_custom_field_definitions_table() -> None:
    op.create_table(
        "custom_field_definitions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("field_code", sa.String(50), nullable=False),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("field_type", sa.String(20), nullable=False),
        sa.Column("field_options", postgresql.JSONB),
        sa.Column("is_required", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("default_value", sa.String(500)),
        sa.Column("validation_regex", sa.String(500)),
        sa.Column("validation_message", sa.String(200)),
        sa.Column("min_value", sa.String(50)),
        sa.Column("max_value", sa.String(50)),
        sa.Column("max_length", sa.Integer),
        sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("section_name", sa.String(100)),
        sa.Column("placeholder", sa.String(200)),
        sa.Column("help_text", sa.String(500)),
        sa.Column(
            "show_in_list", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column("show_in_form", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "show_in_detail", sa.Boolean, nullable=False, server_default=sa.true()
        ),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
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
        sa.CheckConstraint(
            "field_type IN ("
            "'TEXT', 'TEXTAREA', 'NUMBER', 'DECIMAL', 'DATE', 'DATETIME', "
            "'BOOLEAN', 'SELECT', 'MULTISELECT', 'EMAIL', 'URL', 'PHONE', "
            "'CURRENCY'"
            ")",
            name="ck_custom_field_definitions_field_type",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "entity_type",
            "field_code",
            name="uq_custom_field_definitions_tenant_entity_code",
        ),
    )
    op.create_index(
        "ix_custom_field_definitions_tenant_id",
        "custom_field_definitions",
        ["tenant_id"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# RLS
# ─────────────────────────────────────────────────────────────────────────────


def _apply_rls() -> None:
    op.execute("ALTER TABLE custom_field_definitions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE custom_field_definitions FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY custom_field_definitions_tenant_isolation
            ON custom_field_definitions
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )


def _grant_roles() -> None:
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON custom_field_definitions TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON custom_field_definitions "
        "TO platform_api;"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Party.custom_fields
# ─────────────────────────────────────────────────────────────────────────────


def _add_party_custom_fields_column() -> None:
    op.add_column(
        "parties",
        sa.Column(
            "custom_fields",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def _drop_party_custom_fields_column() -> None:
    op.drop_column("parties", "custom_fields")
