"""party custom-fields value column

Reduced to the KERNEL half only (kernel-boundary Task 1c). This revision keeps
its id (`0004_custom_fields`) and its `down_revision` (`0003_party_identity`)
UNCHANGED — existing v0.8 databases already at head `0007` traversed it and
never re-run it, so the graph is untouched for them.

Originally this migration ALSO created the `custom_field_definitions` table
(an assembly/feature concern). That half moved to the assembly lineage's
`a001_adopt_custom_field_definitions` revision. What remains here is the one
KERNEL-owned change: the `parties.custom_fields` JSONB column, declared on the
kernel `Party` model (`dotmac_kernel.models.Party`), which holds custom-field
*values* keyed by `field_code`. On a fresh empty-assembly database (kernel base
only) this column exists but `custom_field_definitions` does not — exactly the
empty-assembly boot target.

Revision ID: 0004_custom_fields
Revises: 0003_party_identity
Create Date: 2026-07-17

"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0004_custom_fields"
down_revision = "0003_party_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "parties",
        sa.Column(
            "custom_fields",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("parties", "custom_fields")
