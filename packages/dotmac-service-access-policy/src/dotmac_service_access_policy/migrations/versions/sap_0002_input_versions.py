"""Add source and policy versions to access inputs.

Existing rows keep NULL versions (written by the V1 contract, treated as older
than every versioned input). No backfill and no default: a version is asserted
by a source, never invented by the schema.

Revision ID: sap_0002_input_versions
Revises: sap_0001_access_policy
Create Date: 2026-11-03
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "sap_0002_input_versions"
down_revision = "sap_0001_access_policy"
branch_labels = None
depends_on = None
_SCHEMA = "mod_serviceaccess"
_TABLE = "service_access_inputs"
_CHECK = "ck_service_access_inputs_version_pair"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("source_version", sa.BigInteger(), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("policy_version", sa.String(60), nullable=True),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        _CHECK,
        _TABLE,
        "(source_version IS NULL) = (policy_version IS NULL) "
        "AND (source_version IS NULL OR source_version >= 0)",
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, schema=_SCHEMA, type_="check")
    op.drop_column(_TABLE, "policy_version", schema=_SCHEMA)
    op.drop_column(_TABLE, "source_version", schema=_SCHEMA)
