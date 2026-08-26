"""Persist the product-owned capability contract beside its port descriptor.

Descriptor v3 carries the declaration that ``CapabilityContract`` requires:
typed schemas or a dated ``SchemaGrace``.  The Integrator validates the exact
document before appending the destination revision; persisting it keeps the
immutable routing snapshot self-contained and lets later resolution re-prove
the descriptor digest without reaching the product.

Legacy v1/v2 rows remain unchanged and must not acquire a contract document.
New v3 rows must carry one plus the independently versioned product wire that
the assembly renders.  The constraint makes the protocol-to-storage
relationship explicit rather than inferring it in Python only.

Revision ID: ig_0015_descriptor_contract
Revises: ig_0014_polling_evidence
Create Date: 2026-08-26
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ig_0015_descriptor_contract"
down_revision = "ig_0014_polling_evidence"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_TABLE = "capability_destination_revisions"
_V3 = "dotmac.io/product-port-descriptor/v3"
_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column("product_wire_schema_version", sa.String(100), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _TABLE,
        sa.Column("descriptor_contract_json", _JSON, nullable=True),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_capability_destination_contract_v3",
        _TABLE,
        "(descriptor_contract_json IS NULL AND "
        "product_wire_schema_version IS NULL AND "
        f"COALESCE(descriptor_schema_version, '') <> '{_V3}') OR "
        "(descriptor_contract_json IS NOT NULL AND "
        "product_wire_schema_version IS NOT NULL AND "
        f"descriptor_schema_version = '{_V3}')",
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_capability_destination_contract_v3",
        _TABLE,
        type_="check",
        schema=_SCHEMA,
    )
    op.drop_column(_TABLE, "descriptor_contract_json", schema=_SCHEMA)
    op.drop_column(_TABLE, "product_wire_schema_version", schema=_SCHEMA)
