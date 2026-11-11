"""Add nullable display copy on each explicitly selected brand-profile plane.

Revision ID: bp_0002_tagline
Revises: bp_0001_brand_profiles
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.planes import ModulePlane, selected_module_planes

from alembic import op

revision = "bp_0002_tagline"
down_revision = "bp_0001_brand_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    planes = selected_module_planes("brand_profiles")
    if ModulePlane.TENANT in planes:
        op.add_column(
            "brand_profiles",
            sa.Column("tagline", sa.String(255), nullable=True),
            schema="mod_brand",
        )
    if ModulePlane.PLATFORM in planes:
        op.add_column(
            "platform_brand_profiles",
            sa.Column("tagline", sa.String(255), nullable=True),
            schema="mod_brand",
        )


def downgrade() -> None:
    planes = selected_module_planes("brand_profiles")
    if ModulePlane.PLATFORM in planes:
        op.drop_column("platform_brand_profiles", "tagline", schema="mod_brand")
    if ModulePlane.TENANT in planes:
        op.drop_column("brand_profiles", "tagline", schema="mod_brand")
