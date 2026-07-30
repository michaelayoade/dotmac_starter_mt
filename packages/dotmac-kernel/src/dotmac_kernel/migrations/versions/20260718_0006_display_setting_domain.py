"""display setting domain

Widens the `ck_domain_settings_domain` CHECK constraint on `domain_settings`
to accept the new `display` domain (tenant-configurable timezone/date-format
settings — see `app/features/settings/spec.py`). The ORM side
(`dotmac_kernel.settings_models.SettingDomain`) already derives its constraint
from the enum's members via `sa.Enum(..., native_enum=False)`, so that half
only matters for `create_all` (unit SQLite / fresh installs); a real
Postgres database still needs this migration to widen the existing
constraint.

Revision ID: 0006_display_setting_domain
Revises: 0005_single_email_authority
Create Date: 2026-07-18

"""

from __future__ import annotations

from alembic import op

revision = "0006_display_setting_domain"
down_revision = "0005_single_email_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_domain_settings_domain", "domain_settings", type_="check")
    op.create_check_constraint(
        "ck_domain_settings_domain",
        "domain_settings",
        "domain IN ('auth', 'audit', 'branding', 'custom_fields', 'display')",
    )


def downgrade() -> None:
    # Rows in the removed domain would violate the restored constraint.
    op.execute("DELETE FROM domain_settings WHERE domain = 'display'")
    op.drop_constraint("ck_domain_settings_domain", "domain_settings", type_="check")
    op.create_check_constraint(
        "ck_domain_settings_domain",
        "domain_settings",
        "domain IN ('auth', 'audit', 'branding', 'custom_fields')",
    )
