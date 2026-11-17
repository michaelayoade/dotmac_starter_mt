"""Add the optional html part to a template revision (ADR-0006 § 5h).

`template_versions.body` stays the TEXT part. `html_body` is a nullable TEXT
column: NULL means "this revision has no html part", which is exactly what every
existing row is, so there is no backfill and the change is safe on a populated
table.

## Tenancy

No tenancy DDL is needed or wanted here. `ts_0001` ENABLEd and FORCEd row level
security on `template_versions` and attached a table-level policy
(`tenant_id = public.app_current_tenant_id()`) plus table-level grants; both
cover a column added later, because neither names columns. The new column is
therefore tenant-isolated by the policy that already guards its row. The RLS
canary in `tests/test_template_studio_isolation.py` proves it.

Fully schema-qualified; the downgrade drops the column (and any html authored
since), which is the honest inverse of an additive nullable column.

Revision ID: ts_0004_html_body
Revises: ts_0003_tenant_audit
Create Date: 2026-11-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ts_0004_html_body"
down_revision = "ts_0003_tenant_audit"
branch_labels = None
depends_on = None

# A literal, for the reason `ts_0002` states.
_SCHEMA = "mod_tstudio"
_VERSIONS = "template_versions"


def upgrade() -> None:
    op.add_column(
        _VERSIONS,
        sa.Column("html_body", sa.Text(), nullable=True),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(_VERSIONS, "html_body", schema=_SCHEMA)
