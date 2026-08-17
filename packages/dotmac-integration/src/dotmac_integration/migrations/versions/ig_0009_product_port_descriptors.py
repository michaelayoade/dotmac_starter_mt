"""Store immutable product-owned destination descriptor snapshots.

The destination application owns its binding identity, capability meaning,
port paths and local stream scope. The Integrator reconciles that authenticated
declaration into the existing append-only destination revision instead of
permanently transcribing a second configuration map.

Revision ID: ig_0009_product_port_desc
Revises: ig_0008_platform_audit_log
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0009_product_port_desc"
down_revision = "ig_0008_platform_audit_log"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_TABLE = "capability_destination_revisions"
_COLUMNS = (
    "descriptor_schema_version",
    "descriptor_owner_module",
    "descriptor_capability_summary",
    "product_binding_id",
    "delivery_path",
    "mirror_path",
    "product_activation_state",
    "descriptor_source_revision",
    "descriptor_digest",
)


def upgrade() -> None:
    op.add_column(
        _TABLE, sa.Column("descriptor_schema_version", sa.String(80)), schema=_SCHEMA
    )
    op.add_column(
        _TABLE, sa.Column("descriptor_owner_module", sa.String(160)), schema=_SCHEMA
    )
    op.add_column(
        _TABLE,
        sa.Column("descriptor_capability_summary", sa.String(500)),
        schema=_SCHEMA,
    )
    op.add_column(_TABLE, sa.Column("product_binding_id", sa.Uuid()), schema=_SCHEMA)
    op.add_column(_TABLE, sa.Column("delivery_path", sa.String(500)), schema=_SCHEMA)
    op.add_column(_TABLE, sa.Column("mirror_path", sa.String(500)), schema=_SCHEMA)
    op.add_column(
        _TABLE, sa.Column("product_activation_state", sa.String(32)), schema=_SCHEMA
    )
    op.add_column(
        _TABLE, sa.Column("descriptor_source_revision", sa.String(64)), schema=_SCHEMA
    )
    op.add_column(_TABLE, sa.Column("descriptor_digest", sa.String(64)), schema=_SCHEMA)
    op.create_check_constraint(
        "ck_capability_destination_descriptor_complete",
        _TABLE,
        "(descriptor_digest IS NULL AND "
        + " AND ".join(f"{name} IS NULL" for name in _COLUMNS[:-1])
        + ") OR (descriptor_digest IS NOT NULL AND "
        + " AND ".join(f"{name} IS NOT NULL" for name in _COLUMNS[:-1])
        + ")",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_capability_destination_descriptor_hashes",
        _TABLE,
        "(descriptor_digest IS NULL) OR ("
        "descriptor_digest ~ '^[0-9a-f]{64}$' AND "
        "descriptor_source_revision ~ '^[0-9a-f]{64}$')",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_capability_destination_activation",
        _TABLE,
        "product_activation_state IS NULL OR product_activation_state IN "
        "('configured_disabled', 'enabled', 'quarantined', 'retired')",
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_capability_destination_descriptor_digest",
        _TABLE,
        ["descriptor_digest"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_capability_destination_descriptor_digest",
        table_name=_TABLE,
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "ck_capability_destination_activation",
        _TABLE,
        type_="check",
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "ck_capability_destination_descriptor_hashes",
        _TABLE,
        type_="check",
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "ck_capability_destination_descriptor_complete",
        _TABLE,
        type_="check",
        schema=_SCHEMA,
    )
    for name in reversed(_COLUMNS):
        op.drop_column(_TABLE, name, schema=_SCHEMA)
