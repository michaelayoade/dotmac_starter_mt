"""domain_setting_history records who made the change.

Kernel lineage continuation (0016 -> 0017).

The table recorded what a setting became and deliberately not who changed it,
on the reasoning that `write_audit_event` owns who-did-what. That was wrong
twice over. Who changed a setting is intrinsic to the settings-change record —
the audit trail is a cross-cutting index that may carry a copy, which is a
projection rather than a competing owner. And splitting them was not workable:
answering "who turned this off" meant joining two tables on timestamp
PROXIMITY, and it cost adopting products a capability they already had.

All five columns are nullable. A seed, a migration or a CLI genuinely has no
actor, and existing rows have none to backfill — recording "unknown" honestly
beats inventing one.

`changed_by_party_id` is `ON DELETE SET NULL`, not CASCADE: deleting a person
must not delete the record that a setting changed.

Revision ID: 0017_history_actor
Revises: 0016_setting_scope_depth
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0017_history_actor"
down_revision = "0016_setting_scope_depth"
branch_labels = None
depends_on = None

_TABLE = "domain_setting_history"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "changed_by_party_id",
            sa.Uuid(),
            sa.ForeignKey("parties.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(_TABLE, sa.Column("change_reason", sa.Text(), nullable=True))
    # 45 characters holds an IPv6 address with an embedded IPv4 suffix.
    op.add_column(_TABLE, sa.Column("ip_address", sa.String(45), nullable=True))
    op.add_column(_TABLE, sa.Column("user_agent", sa.String(500), nullable=True))
    op.add_column(_TABLE, sa.Column("request_id", sa.String(128), nullable=True))
    op.create_index("ix_domain_setting_history_actor", _TABLE, ["changed_by_party_id"])


def downgrade() -> None:
    """Drops the actor. DESTRUCTIVE: who made each change is not recoverable
    from anywhere else once these columns are gone."""
    op.drop_index("ix_domain_setting_history_actor", table_name=_TABLE)
    for column in (
        "request_id",
        "user_agent",
        "ip_address",
        "change_reason",
        "changed_by_party_id",
    ):
        op.drop_column(_TABLE, column)
