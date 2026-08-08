"""domain_settings.value_type becomes an open, registry-validated string.

Kernel lineage continuation (0014 -> 0015).

`ck_domain_settings_value_type` pinned the column to four types — string,
integer, boolean, json — so a product needing a fifth needed a kernel migration.
This is the same closed-list defect migration `0014` removed from the `domain`
column, one column across, and it bit hardest against ADR-0003's exact-Money
rule: with no money type, a currency setting had to be a string that every
reader re-parsed with the currency recorded nowhere.

Which value types exist is now a declaration validated by
`dotmac_kernel.setting_value_types.SettingValueTypeRegistry`, and each type owns
how its values are stored and read back.

The kernel's own set gains `money`, stored as
`{"amount": "<decimal string>", "currency": "<ISO-4217>"}` in `value_json`. No
existing row can be of that type, so there is nothing to backfill.

Downgrade is lossy by necessity: rows whose type is not one of the original four
cannot satisfy the restored constraint, so they are deleted.

Revision ID: 0015_open_value_types
Revises: 0014_open_setting_domains
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0015_open_value_types"
down_revision = "0014_open_setting_domains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_domain_settings_value_type", "domain_settings", type_="check"
    )
    # The value-alignment CHECK named the types too — it permitted `value_json`
    # only when `value_type = 'json'`, so a new JSON-stored type such as `money`
    # could not be written at all. Replaced with the invariant that actually
    # holds and mentions no type: exactly one value column is populated.
    op.drop_constraint(
        "ck_domain_settings_value_alignment", "domain_settings", type_="check"
    )
    # Existing rows may hold the JSON text `null` where they mean "no JSON
    # value": SQLAlchemy's JSON type serialises Python `None` that way unless
    # `none_as_null=True`, which the model now sets. Those rows would fail the
    # new constraint, and they are also wrong on their own terms — `value_json
    # IS NULL` never matched them.
    op.execute(
        sa.text(
            "UPDATE domain_settings SET value_json = NULL "
            "WHERE value_json::text = 'null'"
        )
    )
    op.create_check_constraint(
        "ck_domain_settings_value_alignment",
        "domain_settings",
        "(value_text IS NOT NULL AND value_json IS NULL) "
        "OR (value_text IS NULL AND value_json IS NOT NULL)",
    )
    op.alter_column(
        "domain_settings",
        "value_type",
        existing_type=sa.String(20),
        type_=sa.String(40),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM domain_settings WHERE value_type NOT IN "
        "('string', 'integer', 'boolean', 'json')"
    )
    op.alter_column(
        "domain_settings",
        "value_type",
        existing_type=sa.String(40),
        type_=sa.String(20),
        existing_nullable=False,
    )
    op.drop_constraint(
        "ck_domain_settings_value_alignment", "domain_settings", type_="check"
    )
    op.create_check_constraint(
        "ck_domain_settings_value_alignment",
        "domain_settings",
        "(value_type = 'json' AND value_json IS NOT NULL AND value_text IS NULL) "
        "OR (value_type != 'json' AND value_text IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_domain_settings_value_type",
        "domain_settings",
        "value_type IN ('string', 'integer', 'boolean', 'json')",
    )
