"""Add the delivery retention clock `terminal_at` (additive, nullable).

Expand-only: one nullable column, no default, no constraint, no index. Old code
ignores it; new code stamps it in the same UPDATE as every transition into
`delivered`, `dead_letter` or `reconciliation_required`.

Backfill (terminal rows only, in this migration):

* `delivered` rows take `delivered_at`, falling back to `created_at`;
* `dead_letter` / `reconciliation_required` rows have NO recorded terminal
  time (the table has no `updated_at`), so they take the migration time. That
  starts their repair window now rather than guessing from `created_at`, which
  could redact a row that only just failed. The retention sweep has never
  redacted these states before this release, so nothing is shortened.

Live rows stay NULL. The downgrade drops the column; nothing else reads it.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0019_delivery_terminal_at"
down_revision = "ig_0018_dispatch_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "delivery_attempts",
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        schema="mod_intg",
    )
    op.execute("""
        UPDATE mod_intg.delivery_attempts
           SET terminal_at = COALESCE(delivered_at, created_at)
         WHERE state = 'delivered' AND terminal_at IS NULL
    """)
    op.execute("""
        UPDATE mod_intg.delivery_attempts
           SET terminal_at = now()
         WHERE state IN ('dead_letter', 'reconciliation_required')
           AND terminal_at IS NULL
    """)


def downgrade() -> None:
    op.drop_column("delivery_attempts", "terminal_at", schema="mod_intg")
