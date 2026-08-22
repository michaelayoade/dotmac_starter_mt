"""Make routing executable and queue lifecycles concurrency-safe.

Revision ID: io_0003_operational_safety
Revises: io_0002_queue_admission
Create Date: 2026-08-22

Assignments and queue entries are history. Only their active states are unique;
released, promoted and cancelled rows remain durable evidence and no longer
prevent a later assignment or queue cycle for the same conversation.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "io_0003_operational_safety"
down_revision = "io_0002_queue_admission"
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_inbox_ops"
_TENANT_TABLES = ("inbox_routing_decisions",)


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


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.drop_constraint(
        "uq_conversation_assignments_tenant_conversation",
        "conversation_assignments",
        schema=_SCHEMA,
        type_="unique",
    )
    op.create_index(
        "uq_conversation_assignments_active_conversation",
        "conversation_assignments",
        ["tenant_id", "conversation_reference"],
        schema=_SCHEMA,
        unique=True,
        postgresql_where=sa.text("status = 'ASSIGNED'"),
    )
    op.drop_constraint(
        "uq_inbox_queue_entries_tenant_conversation",
        "inbox_queue_entries",
        schema=_SCHEMA,
        type_="unique",
    )
    op.create_index(
        "uq_inbox_queue_entries_active_conversation",
        "inbox_queue_entries",
        ["tenant_id", "conversation_reference"],
        schema=_SCHEMA,
        unique=True,
        postgresql_where=sa.text("status = 'QUEUED'"),
    )

    op.create_table(
        "inbox_routing_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("decision_reference", sa.String(180), nullable=False),
        sa.Column("conversation_reference", sa.String(180), nullable=False),
        sa.Column("channel_code", sa.String(80), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("queue_id", sa.Uuid(), nullable=False),
        sa.Column("queue_entry_id", sa.Uuid(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_routing_decisions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [
                "mod_inbox_ops.inbox_routing_rules.tenant_id",
                "mod_inbox_ops.inbox_routing_rules.id",
            ],
            name="fk_inbox_routing_decisions_tenant_rule",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            ["mod_inbox_ops.inbox_queues.tenant_id", "mod_inbox_ops.inbox_queues.id"],
            name="fk_inbox_routing_decisions_tenant_queue",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "queue_entry_id"],
            [
                "mod_inbox_ops.inbox_queue_entries.tenant_id",
                "mod_inbox_ops.inbox_queue_entries.id",
            ],
            name="fk_inbox_routing_decisions_tenant_queue_entry",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_routing_decisions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "decision_reference",
            name="uq_inbox_routing_decisions_tenant_reference",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_routing_decisions_tenant_conversation_time",
        "inbox_routing_decisions",
        ["tenant_id", "conversation_reference", "decided_at"],
        schema=_SCHEMA,
    )
    op.execute(
        """
        CREATE FUNCTION mod_inbox_ops.refuse_routing_decision_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'inbox routing decisions are append-only'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER inbox_routing_decisions_append_only "
        "BEFORE UPDATE OR DELETE ON mod_inbox_ops.inbox_routing_decisions "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_inbox_ops.refuse_routing_decision_mutation();"
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
    op.drop_table("inbox_routing_decisions", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_inbox_ops.refuse_routing_decision_mutation();")
    op.drop_index(
        "uq_inbox_queue_entries_active_conversation",
        table_name="inbox_queue_entries",
        schema=_SCHEMA,
    )
    op.create_unique_constraint(
        "uq_inbox_queue_entries_tenant_conversation",
        "inbox_queue_entries",
        ["tenant_id", "conversation_reference"],
        schema=_SCHEMA,
    )
    op.drop_index(
        "uq_conversation_assignments_active_conversation",
        table_name="conversation_assignments",
        schema=_SCHEMA,
    )
    op.create_unique_constraint(
        "uq_conversation_assignments_tenant_conversation",
        "conversation_assignments",
        ["tenant_id", "conversation_reference"],
        schema=_SCHEMA,
    )
