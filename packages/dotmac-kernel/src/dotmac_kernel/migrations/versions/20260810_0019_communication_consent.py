"""The do-not-contact ledger (ADR-0006 § 5c — consent, sequenced before delivery).

Creates `communication_suppressions`: the one table that answers *may we contact
this address on this channel?* Ported from Sub's `communication_suppressions`,
which is single-tenant and therefore has neither `tenant_id` nor RLS.

Both are added here and are not optional. A consent ledger is the worst table in
the system to leak across tenants: a cross-tenant read would let one tenant
enumerate another's unsubscribes and bounces, and a cross-tenant write would let
one tenant silence another's invoices. Hard rule 11 in one migration —
`tenant_id NOT NULL`, a composite unique that includes it, RLS ENABLEd *and*
FORCEd, an isolation policy, and the online-role grants. FORCE matters: without
it the table owner (which migrations run as) bypasses its own policy.

`scope` and `reason` are CHECK-constrained rather than native enums. The
2026-08-10 Template Studio audit found ERP's 43-member native
`document_template_type` to be the same ADR-0008 non-conformance the settings
cutover is repairing, and a native enum needs an `ALTER TYPE` dance to amend.

Revision ID: 0019_communication_consent
Revises: 0018_idempotency_one_owner
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0019_communication_consent"
down_revision = "0018_idempotency_one_owner"
branch_labels = None
depends_on = None

_TABLE = "communication_suppressions"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("raw_address", sa.String(320), nullable=True),
        sa.Column("party_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(120), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_communication_suppressions_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "channel",
            "address",
            name="uq_communication_suppressions_tenant_channel_address",
        ),
        sa.CheckConstraint(
            "scope IN ('marketing', 'all')",
            name="ck_communication_suppressions_scope",
        ),
        sa.CheckConstraint(
            "reason IN ('unsubscribe', 'bounce', 'complaint', 'manual', 'erasure')",
            name="ck_communication_suppressions_reason",
        ),
    )
    op.create_index("ix_communication_suppressions_tenant_id", _TABLE, ["tenant_id"])
    # The read path is always (tenant, channel, address) — single check and the
    # bulk `IN` form both ride this.
    op.create_index(
        "ix_communication_suppressions_lookup",
        _TABLE,
        ["tenant_id", "channel", "address"],
    )

    op.execute(f"ALTER TABLE {_TABLE} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {_TABLE} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {_TABLE}_tenant_isolation ON {_TABLE}
          USING (tenant_id = app_current_tenant_id())
          WITH CHECK (tenant_id = app_current_tenant_id());
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO app_user;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLE} TO platform_api;")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_TABLE}_tenant_isolation ON {_TABLE};")
    op.drop_index("ix_communication_suppressions_lookup", table_name=_TABLE)
    op.drop_index("ix_communication_suppressions_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
