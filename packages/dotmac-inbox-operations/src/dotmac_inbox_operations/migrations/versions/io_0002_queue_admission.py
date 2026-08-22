"""Add durable FIFO admission and round-robin rotation state.

Revision ID: io_0002_queue_admission
Revises: io_0001_inbox_operations
Create Date: 2026-08-22

Sub keeps both of these durable, and both for a reason a comment is worth:
a queue position derived at read time changes under the customer whenever a
reader sorts differently, and an in-memory rotation cursor restarts at the same
agent after every deploy.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "io_0002_queue_admission"
down_revision = "io_0001_inbox_operations"
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_inbox_ops"

_ENTRY_STATUSES = ("QUEUED", "PROMOTED", "CANCELLED")
_TENANT_TABLES = ("inbox_queue_entries", "inbox_round_robin_cursors")


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.create_table(
        "inbox_queue_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("queue_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_reference", sa.String(160), nullable=False),
        sa.Column("queue_position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_queue_entries_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            ["mod_inbox_ops.inbox_queues.tenant_id", "mod_inbox_ops.inbox_queues.id"],
            ondelete="CASCADE",
            name="fk_inbox_queue_entries_tenant_queue",
        ),
        sa.CheckConstraint(
            _in_list("status", _ENTRY_STATUSES), name="ck_inbox_queue_entries_status"
        ),
        sa.CheckConstraint(
            "queue_position > 0", name="ck_inbox_queue_entries_position_positive"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_queue_entries_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_reference",
            name="uq_inbox_queue_entries_tenant_conversation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "queue_id",
            "queue_position",
            name="uq_inbox_queue_entries_tenant_queue_position",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_queue_entries_tenant_queue_status_position",
        "inbox_queue_entries",
        ["tenant_id", "queue_id", "status", "queue_position"],
        schema=_SCHEMA,
    )

    op.create_table(
        "inbox_round_robin_cursors",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("queue_id", sa.Uuid(), nullable=False),
        sa.Column("last_assigned_agent_reference", sa.String(160), nullable=True),
        sa.Column("rotation_count", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_round_robin_cursors_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            ["mod_inbox_ops.inbox_queues.tenant_id", "mod_inbox_ops.inbox_queues.id"],
            ondelete="CASCADE",
            name="fk_inbox_round_robin_cursors_tenant_queue",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_round_robin_cursors_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "queue_id", name="uq_inbox_round_robin_cursors_tenant_queue"
        ),
        schema=_SCHEMA,
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_inbox_ops.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_inbox_ops.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_inbox_ops.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON "
            f"mod_inbox_ops.{table} TO app_user;"
        )


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.drop_table(table, schema=_SCHEMA)
