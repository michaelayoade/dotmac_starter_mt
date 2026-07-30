"""Platform-scoped audit + idempotency ledger (kernel — platform variants).

Creates the two PLATFORM catalog tables backing the platform-scoped audit and
idempotency primitives (the counterparts to the tenant-scoped `audit_events` and
`inbox_records`), so a platform-level assembly (e.g. the vendor control plane)
gets the same idempotency + audit guarantees for platform-level resources:
- `platform_audit_events` — platform audit trail (actor = a platform admin).
- `platform_inbox_records` — platform idempotency ledger (unique command_id).

Platform catalog pattern (0007): NO tenant_id, NO RLS; GRANTed to `platform_api`
and `app_admin`, REVOKEd from `app_user`. Extends the KERNEL lineage (head 0008).

Revision ID: 0009_platform_audit_inbox
Revises: 0008_outbox_inbox
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0009_platform_audit_inbox"
down_revision = "0008_outbox_inbox"
branch_labels = None
depends_on = None

_TABLES = "platform_audit_events, platform_inbox_records"


def upgrade() -> None:
    op.create_table(
        "platform_audit_events",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("actor_admin_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=120), nullable=False),
        sa.Column("entity_id", sa.String(length=120), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_admin_id"],
            ["platform_admins.id"],
            ondelete="SET NULL",
            name="fk_platform_audit_events_admin",
        ),
    )
    op.create_index(
        "ix_platform_audit_events_actor_admin_id",
        "platform_audit_events",
        ["actor_admin_id"],
    )

    op.create_table(
        "platform_inbox_records",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("command_id", sa.String(length=200), nullable=False),
        sa.Column("command_type", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "result", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")
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
        sa.UniqueConstraint("command_id", name="uq_platform_inbox_command_id"),
    )

    # Platform catalog grants — no RLS (there is no tenant to scope by).
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLES} TO platform_api;")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_TABLES} TO app_admin;")
    op.execute(f"REVOKE ALL ON {_TABLES} FROM app_user;")


def downgrade() -> None:
    op.drop_index(
        "ix_platform_audit_events_actor_admin_id", table_name="platform_audit_events"
    )
    op.drop_table("platform_inbox_records")
    op.drop_table("platform_audit_events")
