"""Tenant entitlements (WS2) — the one entitlement grant store.

Creates `tenant_entitlement_grants`, a TENANT-scoped, RLS-protected table (the
0001/0008 tenant-table pattern: `tenant_id NOT NULL` + composite unique + ENABLE
+ FORCE RLS + a tenant-isolation policy + online-role grants). It is the single
entitlement authority — grants reference declared WS1 capability codes; there is
no parallel `tenant_module_entitlements` table.

Revision ID: 0010_tenant_entitlements
Revises: 0009_platform_audit_inbox
Create Date: 2026-07-31
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0010_tenant_entitlements"
down_revision = "0009_platform_audit_inbox"
branch_labels = None
depends_on = None

_TABLE = "tenant_entitlement_grants"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("capability_code", sa.String(length=200), nullable=False),
        sa.Column(
            "granted", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "limits", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("source", sa.String(length=120), nullable=True),
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
            name="fk_entitlement_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "capability_code", name="uq_entitlement_tenant_code"
        ),
    )
    op.create_index("ix_tenant_entitlement_grants_tenant_id", _TABLE, ["tenant_id"])

    # Tenant isolation (0001/0008 pattern): ENABLE + FORCE RLS + policy.
    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_user;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE};")
    op.drop_index("ix_tenant_entitlement_grants_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
