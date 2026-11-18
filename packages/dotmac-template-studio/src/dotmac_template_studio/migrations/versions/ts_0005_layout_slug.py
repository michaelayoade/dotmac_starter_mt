"""Add the optional layout opt-in to a template revision (TS-5).

`template_versions.layout_slug` is a nullable VARCHAR(100): the slug of a
template on channel `email_layout`. NULL means "no layout", which is exactly what
every existing row is, so there is no backfill and the change is safe on a
populated table. The layout is an ordinary template row; no new table.

## Tenancy

No tenancy DDL is needed: `ts_0001` ENABLEd and FORCEd row level security on
`template_versions` with a table-level policy and grants, which cover a column
added later. The RLS canary in `tests/test_template_studio_isolation.py` proves it.

Fully schema-qualified; the downgrade drops the column (and any opt-in authored
since), the honest inverse of an additive nullable column.

Revision ID: ts_0005_layout_slug
Revises: ts_0004_html_body
Create Date: 2026-11-18
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ts_0005_layout_slug"
down_revision = "ts_0004_html_body"
branch_labels = None
depends_on = None

_SCHEMA = "mod_tstudio"
_VERSIONS = "template_versions"


def upgrade() -> None:
    op.add_column(
        _VERSIONS,
        sa.Column("layout_slug", sa.String(length=100), nullable=True),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(_VERSIONS, "layout_slug", schema=_SCHEMA)
