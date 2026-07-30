"""Outbox/inbox subsystem tables (kernel WS3).

Creates the two tenant-scoped, RLS-protected tables backing
`dotmac_kernel.messaging`:
- `inbox_records` — idempotency ledger (unique per tenant+command_id).
- `outbox_events` — transactional outbox (pending events drained by the relay).

Both follow the standard kernel tenant-table pattern (0001): tenant_id FK to
tenants, ENABLE + FORCE row-level security with a `<table>_tenant_isolation`
policy keyed on `app_current_tenant_id()`, and explicit grants to `app_user` and
`platform_api`. Extends the KERNEL lineage (head was 0007).

Revision ID: 0008_outbox_inbox
Revises: 0007_platform_identity
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0008_outbox_inbox"
down_revision = "0007_platform_identity"
branch_labels = None
depends_on = None

_TABLES = ("inbox_records", "outbox_events")


def upgrade() -> None:
    _create_inbox_records_table()
    _create_outbox_events_table()
    _apply_rls()
    _grant_roles()


def _create_inbox_records_table() -> None:
    op.create_table(
        "inbox_records",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_id", sa.String(length=200), nullable=False),
        sa.Column("command_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("correlation_id", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_records_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_inbox_records_tenant_command_id"
        ),
    )
    op.create_index("ix_inbox_records_tenant_id", "inbox_records", ["tenant_id"])


def _create_outbox_events_table() -> None:
    op.create_table(
        "outbox_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
            name="fk_outbox_events_tenant",
        ),
    )
    op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])
    op.create_index(
        "ix_outbox_events_status_available_at",
        "outbox_events",
        ["status", "available_at"],
    )


def _apply_rls() -> None:
    """Enable RLS with a tenant-isolation policy on each table (0001 pattern)."""
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
                USING (tenant_id = app_current_tenant_id())
                WITH CHECK (tenant_id = app_current_tenant_id());
            """
        )


def _grant_roles() -> None:
    """Grant online privileges; app_admin keeps migration/offline access."""
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "inbox_records, outbox_events TO app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "inbox_records, outbox_events TO platform_api;"
    )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table};")
    op.drop_index("ix_outbox_events_status_available_at", table_name="outbox_events")
    op.drop_index("ix_outbox_events_tenant_id", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_inbox_records_tenant_id", table_name="inbox_records")
    op.drop_table("inbox_records")
