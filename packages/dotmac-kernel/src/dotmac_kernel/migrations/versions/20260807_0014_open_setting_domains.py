"""Open setting domains, and the setting change-history table.

Kernel lineage continuation (0013 -> 0014). Two changes, one revision, because
both are the settings subsystem's shape and splitting them would give an
operator two windows in which the code and the schema disagree.

`ck_domain_settings_domain` pinned the column to this repo's own five domains,
so any product consuming the kernel would have needed a kernel migration to
store a setting of its own. Which domains are real is now a runtime
declaration on module manifests, validated by
`dotmac_kernel.setting_domains.SettingDomainRegistry` at boot and at every
write, so the constraint is dropped and the column widened to match `key`.

`domain_setting_history` records value transitions. Its tenancy mirrors
`domain_settings` exactly, including that table's documented exception to the
tenant_id-NOT-NULL rule — `NULL` is a platform-scope change every tenant reads
and none writes — so the RLS split below is copied from migration 0002/0013
deliberately rather than reinvented.

Downgrade is lossy by necessity: rows outside the original five domains cannot
satisfy the restored constraint, so they are deleted, and the history table is
dropped with everything in it. That is the honest cost of narrowing a column
products have since written to.

Revision ID: 0014_open_setting_domains
Revises: 0013_feature_flag_overrides
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0014_open_setting_domains"
down_revision = "0013_feature_flag_overrides"
branch_labels = None
depends_on = None


_HISTORY = "domain_setting_history"


def upgrade() -> None:
    op.drop_constraint("ck_domain_settings_domain", "domain_settings", type_="check")
    op.alter_column(
        "domain_settings",
        "domain",
        existing_type=sa.String(20),
        type_=sa.String(120),
        existing_nullable=False,
    )
    _create_history_table()


def _create_history_table() -> None:
    op.create_table(
        _HISTORY,
        sa.Column("id", sa.Uuid(), primary_key=True),
        # NULL = a platform-scope change, mirroring `domain_settings`.
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        # Denormalised from the parent so history survives its deletion.
        sa.Column("domain", sa.String(120), nullable=False),
        sa.Column("key", sa.String(120), nullable=False),
        sa.Column(
            "setting_id",
            sa.Uuid(),
            sa.ForeignKey("domain_settings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(20), nullable=False),
        # NULL for a secret: see the model docstring — a history table must not
        # become the place a rotated credential outlives its rotation.
        sa.Column("value_before", sa.Text),
        sa.Column("value_after", sa.Text),
        sa.Column(
            "secret_changed", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_check_constraint(
        "ck_domain_setting_history_action",
        _HISTORY,
        "action IN ('create', 'update', 'delete')",
    )
    op.create_index("ix_domain_setting_history_tenant_id", _HISTORY, ["tenant_id"])
    op.create_index("ix_domain_setting_history_setting_id", _HISTORY, ["setting_id"])
    op.create_index(
        "ix_domain_setting_history_lookup", _HISTORY, ["tenant_id", "domain", "key"]
    )
    op.create_index("ix_domain_setting_history_changed_at", _HISTORY, ["changed_at"])

    op.execute("ALTER TABLE domain_setting_history ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE domain_setting_history FORCE ROW LEVEL SECURITY;")
    # Read own history AND platform-scope history.
    op.execute(
        """
        CREATE POLICY domain_setting_history_read ON domain_setting_history
          FOR SELECT
          USING (tenant_id = app_current_tenant_id() OR tenant_id IS NULL);
        """
    )
    # Write only own rows. There is no UPDATE or DELETE policy on purpose: a
    # history row is append-only, and a tenant that could rewrite it could erase
    # the record of the change it is meant to explain.
    op.execute(
        """
        CREATE POLICY domain_setting_history_write_ins ON domain_setting_history
          FOR INSERT
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    # `platform_api` records platform-scope changes only.
    op.execute(
        """
        CREATE POLICY domain_setting_history_platform_all ON domain_setting_history
          TO platform_api
          USING (tenant_id IS NULL)
          WITH CHECK (tenant_id IS NULL);
        """
    )
    # No UPDATE/DELETE grant: append-only, enforced by privilege as well as by
    # the absent policies above.
    op.execute("GRANT SELECT, INSERT ON domain_setting_history TO app_user;")
    op.execute("GRANT SELECT, INSERT ON domain_setting_history TO platform_api;")


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS domain_setting_history_platform_all "
        "ON domain_setting_history;"
    )
    op.execute(
        "DROP POLICY IF EXISTS domain_setting_history_write_ins "
        "ON domain_setting_history;"
    )
    op.execute(
        "DROP POLICY IF EXISTS domain_setting_history_read ON domain_setting_history;"
    )
    op.drop_table(_HISTORY)
    op.execute(
        "DELETE FROM domain_settings WHERE domain NOT IN "
        "('auth', 'audit', 'branding', 'custom_fields', 'display')"
    )
    op.alter_column(
        "domain_settings",
        "domain",
        existing_type=sa.String(120),
        type_=sa.String(20),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_domain_settings_domain",
        "domain_settings",
        "domain IN ('auth', 'audit', 'branding', 'custom_fields', 'display')",
    )
