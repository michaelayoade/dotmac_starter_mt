"""Expand campaign revisions with an optional immutable delivery deadline.

Revision ID: ca_0002_delivery_deadline
Revises: ca_0001_campaigns
Create Date: 2026-11-23
"""

import sqlalchemy as sa

from alembic import op

revision = "ca_0002_delivery_deadline"
down_revision = "ca_0001_campaigns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows stay unbounded. The existing revision snapshot trigger,
    # tenant-composite keys, grants and forced RLS continue to own this column.
    op.add_column(
        "campaign_revisions",
        sa.Column("delivery_expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="mod_campaigns",
    )


def downgrade() -> None:
    raise RuntimeError("Removing a campaign delivery deadline would widen permission")
