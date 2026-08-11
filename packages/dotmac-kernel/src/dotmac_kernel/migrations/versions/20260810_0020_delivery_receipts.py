"""Delivery receipts, and the bounce→consent loop (ADR-0006 § 5c).

Creates `communication_deliveries`: what the PROVIDER said about an outbound
message. The kernel outbox already records that we dispatched; nothing recorded
the verdict that came back, which is why the consent ledger added in `0019` had
no automated writer — exactly as in Sub, where `DeliveryStatus.bounced` is
declared and never assigned.

Deliberately NOT a queue. Sub's `Notification` table is
`dotmac_kernel.messaging`'s `OutboxEvent` built a second time (status/attempts/
backoff/lease-reclaim/dead-letter all appear twice); porting it would install the
duplicate permanently. Evidence: `docs/inventories/delivery-outbox-sources.md`.

The unique index is PARTIAL on `provider_message_id IS NOT NULL` and includes
`status`: it makes a redelivered copy of one webhook safe while preserving the
normal accepted→delivered/bounced progression of one provider message. A plain
unique index would make every id-less receipt collide with the last one.

`dispatch_id` is the product/outbox identity created before the provider call;
all status receipts for the same outbound message share it. The optional
fingerprint prevents that identity being silently reused for different content.

Tenant-scoped with RLS in the same migration (hard rule 11). A delivery receipt
names an address and a verdict about it — the same disclosure risk as the consent
ledger it feeds.

Revision ID: 0020_delivery_receipts
Revises: 0019_communication_consent
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0020_delivery_receipts"
down_revision = "0019_communication_consent"
branch_labels = None
depends_on = None

_TABLE = "communication_deliveries"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=True),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("address", sa.String(320), nullable=False),
        sa.Column("provider", sa.String(120), nullable=False),
        sa.Column("provider_message_id", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("response_code", sa.String(60), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_communication_deliveries_tenant",
        ),
        sa.CheckConstraint(
            "status IN ('accepted', 'delivered', 'failed', 'rejected', "
            "'bounced', 'complaint')",
            name="ck_communication_deliveries_status",
        ),
    )
    op.create_index("ix_communication_deliveries_tenant_id", _TABLE, ["tenant_id"])
    op.create_index(
        "ix_communication_deliveries_dispatch",
        _TABLE,
        ["tenant_id", "dispatch_id"],
    )
    op.create_index(
        "ix_communication_deliveries_address",
        _TABLE,
        ["tenant_id", "channel", "address"],
    )
    op.create_index(
        "uq_communication_deliveries_provider_message_status",
        _TABLE,
        ["tenant_id", "provider", "provider_message_id", "status"],
        unique=True,
        postgresql_where=sa.text("provider_message_id IS NOT NULL"),
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
    op.drop_index(
        "uq_communication_deliveries_provider_message_status", table_name=_TABLE
    )
    op.drop_index("ix_communication_deliveries_address", table_name=_TABLE)
    op.drop_index("ix_communication_deliveries_dispatch", table_name=_TABLE)
    op.drop_index("ix_communication_deliveries_tenant_id", table_name=_TABLE)
    op.drop_table(_TABLE)
