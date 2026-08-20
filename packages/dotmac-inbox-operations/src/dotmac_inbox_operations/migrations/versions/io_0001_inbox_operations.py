"""Create staffed inbox operation tables.

Revision ID: io_0001_inbox_operations
Revises: (lineage root)
Create Date: 2026-08-20
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "io_0001_inbox_operations"
down_revision = None
branch_labels = ("inbox_operations",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_inbox_ops"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_inbox_ops;")
    op.execute("REVOKE ALL ON SCHEMA mod_inbox_ops FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_inbox_ops TO app_user, app_admin;")
    op.create_table(
        "inbox_queues",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_queues_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_inbox_queues_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_inbox_queues_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "inbox_routing_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("queue_id", sa.Uuid(), nullable=False),
        sa.Column("channel_code", sa.String(80), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_routing_rules_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            ["mod_inbox_ops.inbox_queues.tenant_id", "mod_inbox_ops.inbox_queues.id"],
            ondelete="CASCADE",
            name="fk_inbox_routing_rules_tenant_queue",
        ),
        sa.CheckConstraint("priority >= 0", name="ck_inbox_routing_rules_priority"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_routing_rules_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "queue_id",
            "channel_code",
            name="uq_inbox_routing_rules_tenant_queue_channel",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_routing_rules_tenant_channel",
        "inbox_routing_rules",
        ["tenant_id", "channel_code"],
        schema=_SCHEMA,
    )
    op.create_table(
        "inbox_agent_presence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_reference", sa.String(160), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("assignment_capacity", sa.Integer(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_agent_presence_tenant",
        ),
        sa.CheckConstraint(
            "state IN ('AVAILABLE', 'AWAY', 'OFFLINE')",
            name="ck_inbox_agent_presence_state",
        ),
        sa.CheckConstraint(
            "assignment_capacity >= 0", name="ck_inbox_agent_presence_capacity"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_agent_presence_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "agent_reference",
            name="uq_inbox_agent_presence_tenant_agent",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "conversation_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_reference", sa.String(180), nullable=False),
        sa.Column("queue_id", sa.Uuid(), nullable=False),
        sa.Column("agent_reference", sa.String(160), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_conversation_assignments_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "queue_id"],
            ["mod_inbox_ops.inbox_queues.tenant_id", "mod_inbox_ops.inbox_queues.id"],
            name="fk_conversation_assignments_tenant_queue",
        ),
        sa.CheckConstraint(
            "status IN ('ASSIGNED', 'RELEASED')",
            name="ck_conversation_assignments_status",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_conversation_assignments_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "conversation_reference",
            name="uq_conversation_assignments_tenant_conversation",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_conversation_assignments_tenant_agent_status",
        "conversation_assignments",
        ["tenant_id", "agent_reference", "status"],
        schema=_SCHEMA,
    )
    op.create_table(
        "inbox_workflow_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_workflow_events_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assignment_id"],
            [
                "mod_inbox_ops.conversation_assignments.tenant_id",
                "mod_inbox_ops.conversation_assignments.id",
            ],
            ondelete="CASCADE",
            name="fk_inbox_workflow_events_tenant_assignment",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_workflow_events_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_workflow_events_tenant_assignment_time",
        "inbox_workflow_events",
        ["tenant_id", "assignment_id", "occurred_at"],
        schema=_SCHEMA,
    )
    op.execute("ALTER TABLE mod_inbox_ops.inbox_queues ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_inbox_ops.inbox_queues FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY inbox_queues_tenant_isolation ON mod_inbox_ops.inbox_queues USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inbox_ops.inbox_queues TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_inbox_ops.inbox_routing_rules ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_inbox_ops.inbox_routing_rules FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY inbox_routing_rules_tenant_isolation ON mod_inbox_ops.inbox_routing_rules USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inbox_ops.inbox_routing_rules TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_inbox_ops.inbox_agent_presence ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_inbox_ops.inbox_agent_presence FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY inbox_agent_presence_tenant_isolation ON mod_inbox_ops.inbox_agent_presence USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inbox_ops.inbox_agent_presence TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_inbox_ops.conversation_assignments ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_inbox_ops.conversation_assignments FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY conversation_assignments_tenant_isolation ON mod_inbox_ops.conversation_assignments USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inbox_ops.conversation_assignments TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_inbox_ops.inbox_workflow_events ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_inbox_ops.inbox_workflow_events FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY inbox_workflow_events_tenant_isolation ON mod_inbox_ops.inbox_workflow_events USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_inbox_ops.inbox_workflow_events TO app_user;"
    )


def downgrade() -> None:
    op.drop_table("inbox_workflow_events", schema=_SCHEMA)
    op.drop_table("conversation_assignments", schema=_SCHEMA)
    op.drop_table("inbox_agent_presence", schema=_SCHEMA)
    op.drop_table("inbox_routing_rules", schema=_SCHEMA)
    op.drop_table("inbox_queues", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_inbox_ops;")
