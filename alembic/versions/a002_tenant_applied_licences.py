"""tenant_applied_licences — the WS8 receiver's durable replay record.

Assembly lineage continuation (a001 → a002). Creates the tenant-scoped,
RLS-protected `tenant_applied_licences` table (hard rule 11: `tenant_id NOT
NULL` + composite unique + ENABLE/FORCE RLS + tenant-isolation policy +
online-role grants, all in this one migration — the 0010 tenant-table pattern).

The kernel WS8 verifier owns no storage; THIS table is the receiver-owned
`AppliedLicence` record `verify_licence` uses as its replay/rollback guard.

Revision ID: a002_applied_licences
Revises: a001_adopt_cfd
Create Date: 2026-08-01
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision id kept ≤32 chars (alembic_version.version_num is varchar(32)).
revision = "a002_applied_licences"
down_revision = "a001_adopt_cfd"
branch_labels = None
depends_on = None

_TABLE = "tenant_applied_licences"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("licence_id", sa.String(length=200), nullable=False),
        sa.Column("licence_version", sa.Integer(), nullable=False),
        sa.Column("digest", sa.String(length=128), nullable=False),
        sa.Column("validity", sa.String(length=20), nullable=False),
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
            name="fk_applied_licence_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "licence_id", name="uq_applied_licence_tenant_lineage"
        ),
    )
    op.create_index("ix_tenant_applied_licences_tenant_id", _TABLE, ["tenant_id"])

    # Tenant isolation (0001/0008/0010 pattern): ENABLE + FORCE RLS + policy.
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
    op.drop_index("ix_tenant_applied_licences_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
