"""Add the opaque service-location ref to referrals.

Released rf_0001 bytes are unchanged. The column is nullable because V1
captures never carried one and a V2 capture may not know the place yet. It holds
an opaque token only: no address text and no coordinates.

Revision ID: rf_0002_service_location_ref
Revises: rf_0001_referrals
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "rf_0002_service_location_ref"
down_revision = "rf_0001_referrals"
branch_labels = None

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "outbox_relay.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_referrals"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.add_column(
        "referrals",
        sa.Column("service_location_ref", sa.String(160), nullable=True),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("referrals", "service_location_ref", schema=_SCHEMA)
