"""Add the opaque service-location ref to service orders.

Released so_0001 bytes are unchanged. The column is nullable because orders
opened through V1 never carried one; the V2 open command requires it for
install, reconnect and change orders. It holds an opaque token only: no address
text and no coordinates.

Revision ID: so_0002_service_location_ref
Revises: so_0001_service_delivery_orders
Create Date: 2026-11-09
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "so_0002_service_location_ref"
down_revision = "so_0001_service_delivery_orders"
branch_labels = None
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_serviceorders"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.add_column(
        "service_orders",
        sa.Column("service_location_ref", sa.String(160), nullable=True),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("service_orders", "service_location_ref", schema=_SCHEMA)
