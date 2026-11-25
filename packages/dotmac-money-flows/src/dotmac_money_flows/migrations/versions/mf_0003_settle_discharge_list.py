"""Add the list of withholding-discharge references to the Settle instance (C2b).

Revision ID: mf_0003_settle_discharge_list
Revises: mf_0002_funded_charge_instances

A confirmation may now carry several deductions, so the instance keeps every
discharge reference as a JSON array of opaque strings, in acceptance order. The
single ``withholding_discharge_ref`` column stays and holds the first one, so
existing readers and rows are unchanged. No backfill: terminal rows are
immutable by trigger, and a row without the list reports its single legacy
reference. Opaque references only; no amount, currency or foreign key.
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "mf_0003_settle_discharge_list"
down_revision = "mf_0002_funded_charge_instances"
branch_labels = None

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_mflows"
_TABLE = "settle_instances"
_COLUMN = "withholding_discharge_list_ref"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.add_column(
        "settle_instances",
        sa.Column("withholding_discharge_list_ref", sa.JSON(), nullable=True),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    # The list is the only record of a second or later discharge reference.
    op.execute(
        f"SET LOCAL row_security = off; LOCK TABLE {_SCHEMA}.{_TABLE} IN ACCESS EXCLUSIVE MODE;"
    )
    evidence = (
        op.get_bind()
        .execute(
            sa.text(
                f"SELECT 1 FROM {_SCHEMA}.{_TABLE} "  # noqa: S608
                f"WHERE {_COLUMN} IS NOT NULL "
                f"AND json_array_length({_COLUMN}) > 1 LIMIT 1"
            )
        )
        .first()
    )
    if evidence is not None:
        raise RuntimeError(
            "cannot downgrade money flows while an instance holds several "
            "discharge references"
        )
    op.drop_column(_TABLE, _COLUMN, schema=_SCHEMA)
