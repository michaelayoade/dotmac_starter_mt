"""Add neutral retention holds on position observations.

Released po_0001 bytes are unchanged. A hold pins one observation against the
retention sweep on behalf of an opaque holder reference. The foreign key to the
observation restricts deletion, so no sweep, cascade or direct delete can
remove a held row while the hold exists; releasing the hold deletes it.

Revision ID: po_0002_observation_holds
Revises: po_0001_positioning
Create Date: 2026-11-09
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "po_0002_observation_holds"
down_revision = "po_0001_positioning"
branch_labels = None

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_pos"


def _timestamps() -> tuple[sa.Column[Any], sa.Column[Any]]:
    return (
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
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.create_table(
        "observation_holds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("hold_ref", sa.String(128), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_observation_holds_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                "mod_pos.position_observations.tenant_id",
                "mod_pos.position_observations.id",
            ],
            ondelete="RESTRICT",
            name="fk_observation_holds_observation",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_observation_holds_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "observation_id",
            "hold_ref",
            name="uq_observation_holds_observation_ref",
        ),
        schema=_SCHEMA,
    )

    op.execute("ALTER TABLE mod_pos.observation_holds ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.observation_holds FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY observation_holds_tenant_isolation "
        "ON mod_pos.observation_holds "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_pos.observation_holds TO app_user;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_pos.observation_holds CASCADE;")
