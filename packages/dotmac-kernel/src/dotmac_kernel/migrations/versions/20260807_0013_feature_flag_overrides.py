"""feature_flag_overrides — deployment- and tenant-scope flag overrides.

Kernel lineage continuation (0012 → 0013), module control-plane directive step 5.

Tenancy mirrors `domain_settings` (migration 0002) exactly, because it is the
same shape and the same documented exception to hard rule 11: `tenant_id` is
NULLABLE, `NULL` meaning a DEPLOYMENT-scope row that every tenant reads and no
tenant writes. The RLS split below is copied deliberately rather than
reinvented — read own-or-platform, write own-only, plus a dedicated policy for
`platform_api`, which never has `app.current_tenant` set and would otherwise be
rejected by its own NULL-tenant writes (`NULL = NULL` is not true in SQL).

Uniqueness is two PARTIAL indexes, not one composite constraint: Postgres treats
NULL as distinct from every other NULL, so without the partial index any number
of deployment-scope rows could collide on the same flag code.

Revision ID: 0013_feature_flag_overrides
Revises: 0012_platform_outbox
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0013_feature_flag_overrides"
down_revision = "0012_platform_outbox"
branch_labels = None
depends_on = None

_TABLE = "feature_flag_overrides"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        # NULL = deployment scope.
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("flag_code", sa.String(200), nullable=False),
        sa.Column(
            "value",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("rollout_percentage", sa.Integer(), nullable=True),
        sa.Column(
            "kill_switch", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_flag_overrides_tenant",
        ),
        sa.CheckConstraint(
            "rollout_percentage IS NULL OR "
            "(rollout_percentage >= 0 AND rollout_percentage <= 100)",
            name="ck_flag_overrides_rollout_range",
        ),
        # An override that sets nothing would sit in the precedence chain
        # deciding nothing while reading as configuration.
        sa.CheckConstraint(
            "value IS NOT NULL OR rollout_percentage IS NOT NULL OR kill_switch",
            name="ck_flag_overrides_not_empty",
        ),
    )
    op.create_index(
        "uq_flag_overrides_platform",
        _TABLE,
        ["flag_code"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
    )
    op.create_index(
        "uq_flag_overrides_tenant",
        _TABLE,
        ["tenant_id", "flag_code"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"),
    )
    op.create_index("ix_flag_overrides_tenant_id", _TABLE, ["tenant_id"])

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    # Read own rows AND deployment-scope defaults.
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_read ON {_TABLE} FOR SELECT
          USING (tenant_id = app_current_tenant_id() OR tenant_id IS NULL);
        """
    )
    # Write only own rows — a tenant may not set a deployment-wide flag.
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_write_ins ON {_TABLE} FOR INSERT
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_write_upd ON {_TABLE} FOR UPDATE
          USING (tenant_id = app_current_tenant_id())
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_write_del ON {_TABLE} FOR DELETE
          USING (tenant_id = app_current_tenant_id());
        """
    )
    # `platform_api` manages deployment-scope rows only — see the docstring.
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_platform_all ON {_TABLE}
          TO platform_api
          USING (tenant_id IS NULL)
          WITH CHECK (tenant_id IS NULL);
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_user;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")


def downgrade() -> None:
    op.drop_index("ix_flag_overrides_tenant_id", table_name=_TABLE)
    op.drop_index("uq_flag_overrides_tenant", table_name=_TABLE)
    op.drop_index("uq_flag_overrides_platform", table_name=_TABLE)
    op.drop_table(_TABLE)
