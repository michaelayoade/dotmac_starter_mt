"""settings table

Creates `domain_settings` — tenant-scoped configuration keyed by
`(domain, key)`, with `tenant_id IS NULL` rows acting as platform-level
defaults every tenant can read.

RLS on this table is special (see `.superpowers/sdd/task-3-brief.md`):
tenants may SELECT their own rows AND platform (NULL-tenant) rows, but may
only INSERT/UPDATE/DELETE rows they own. `platform_api` gets its own policy
(`domain_settings_platform_all`) restricted to NULL-tenant rows — without it,
`platform_api` would have table grants but no `app.current_tenant` set, so the
tenant-comparison write policies would reject even its own NULL-tenant writes.

Revision ID: 0002_settings_table
Revises: 0001_initial_tenant_schema
Create Date: 2026-07-17

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0002_settings_table"
down_revision = "0001_initial_tenant_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    _create_domain_settings_table()
    _create_partial_unique_indexes()
    _apply_rls()
    _grant_roles()


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS domain_settings_platform_all ON domain_settings;")
    op.execute("DROP POLICY IF EXISTS domain_settings_write_del ON domain_settings;")
    op.execute("DROP POLICY IF EXISTS domain_settings_write_upd ON domain_settings;")
    op.execute("DROP POLICY IF EXISTS domain_settings_write_ins ON domain_settings;")
    op.execute("DROP POLICY IF EXISTS domain_settings_read ON domain_settings;")
    op.drop_table("domain_settings")


# ─────────────────────────────────────────────────────────────────────────────
# Table
# ─────────────────────────────────────────────────────────────────────────────


def _create_domain_settings_table() -> None:
    op.create_table(
        "domain_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("domain", sa.String(20), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column(
            "value_type",
            sa.String(20),
            nullable=False,
            server_default="string",
        ),
        sa.Column("value_text", sa.Text),
        sa.Column("value_json", postgresql.JSONB),
        sa.Column("is_secret", sa.Boolean, nullable=False, server_default=sa.false()),
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
            "domain IN ('auth', 'audit', 'branding', 'custom_fields')",
            name="ck_domain_settings_domain",
        ),
        sa.CheckConstraint(
            "value_type IN ('string', 'integer', 'boolean', 'json')",
            name="ck_domain_settings_value_type",
        ),
        sa.CheckConstraint(
            "(value_type = 'json' AND value_json IS NOT NULL AND value_text IS NULL) "
            "OR (value_type != 'json' AND value_text IS NOT NULL)",
            name="ck_domain_settings_value_alignment",
        ),
    )
    op.create_index("ix_domain_settings_tenant_id", "domain_settings", ["tenant_id"])


def _create_partial_unique_indexes() -> None:
    """Composite `UniqueConstraint` can't span NULLs the way we need it to —
    Postgres treats every NULL as distinct, so a plain `(tenant_id, domain, key)`
    unique constraint would let unlimited platform rows collide on
    `(domain, key)`. Two partial unique indexes instead: one scoped to platform
    rows, one to tenant rows.
    """
    op.create_index(
        "uq_domain_settings_platform",
        "domain_settings",
        ["domain", "key"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
    )
    op.create_index(
        "uq_domain_settings_tenant",
        "domain_settings",
        ["tenant_id", "domain", "key"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# RLS
# ─────────────────────────────────────────────────────────────────────────────


def _apply_rls() -> None:
    op.execute("ALTER TABLE domain_settings ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE domain_settings FORCE ROW LEVEL SECURITY;")

    # Tenants read their own rows AND platform (NULL-tenant) defaults.
    op.execute(
        """
        CREATE POLICY domain_settings_read ON domain_settings FOR SELECT
          USING (tenant_id = app_current_tenant_id() OR tenant_id IS NULL);
        """
    )
    # Tenants write (insert/update/delete) only rows they own.
    op.execute(
        """
        CREATE POLICY domain_settings_write_ins ON domain_settings FOR INSERT
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(
        """
        CREATE POLICY domain_settings_write_upd ON domain_settings FOR UPDATE
          USING (tenant_id = app_current_tenant_id())
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(
        """
        CREATE POLICY domain_settings_write_del ON domain_settings FOR DELETE
          USING (tenant_id = app_current_tenant_id());
        """
    )
    # platform_api never has app.current_tenant set, so app_current_tenant_id()
    # is NULL for it and the tenant-comparison policies above would reject even
    # its own NULL-tenant writes (`NULL = NULL` is not true in SQL). Give it a
    # dedicated policy restricted to platform rows only — it manages platform
    # defaults, never a tenant's own settings.
    op.execute(
        """
        CREATE POLICY domain_settings_platform_all ON domain_settings
          TO platform_api
          USING (tenant_id IS NULL)
          WITH CHECK (tenant_id IS NULL);
        """
    )


def _grant_roles() -> None:
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON domain_settings TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON domain_settings TO platform_api;"
    )
