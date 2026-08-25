"""Agent availability evidence and the separated ownership-movement commands.

Revision ID: io_0004_availability_transfers
Revises: io_0003_operational_safety
Create Date: 2026-08-23

Four tenant tables, all on the same forced-RLS plane as the eight before them.

`inbox_presence_events` records presence TRANSITIONS only — never heartbeats —
with a check constraint that a MANAGER-sourced row carries both an actor and a
reason, so an override cannot be written as though it were the agent's own
choice.

`inbox_transfer_requests` holds cold and warm moves in one table: a cold
transfer lands ACCEPTED, a warm one arrives REQUESTED and settles later. A
partial unique index allows at most one REQUESTED row per conversation, so two
agents can never both be deciding whether to take the same conversation.

`inbox_escalation_requests` records that an agent ASKED for an escalation, and
nothing more. `dotmac-operational-escalations` owns whether one should exist,
under which policy version and who answered it, for tickets and outages and
inboxes alike — so there is no status column here and nothing to settle. It is
append-only for the same reason routing decisions are, carries a tenant-unique
`dedup_key` the escalation owner dedupes on too, and has no target-agent column
at all: an escalation that quietly reassigns the work is not expressible.

`inbox_offline_dispositions` makes the grace-period decision durable rather than
scheduler-resident, for the reason `inbox_round_robin_cursors` already exists.

The three enum-widening changes — ON_BREAK on presence state, TRANSFERRED and
REQUEUED on assignment status — replace the value-list CHECK constraints the
model layer generates. Existing rows keep their values; the partial unique index
that permits one ASSIGNED assignment per conversation is untouched, because both
new members are terminal.
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "io_0004_availability_transfers"
down_revision = "io_0003_operational_safety"
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_inbox_ops"
_TENANT_TABLES = (
    "inbox_presence_events",
    "inbox_transfer_requests",
    "inbox_escalation_requests",
    "inbox_offline_dispositions",
)
_PRESENCE_STATES = ("AVAILABLE", "AWAY", "ON_BREAK", "OFFLINE")
_ASSIGNMENT_STATUSES = ("ASSIGNED", "RELEASED", "TRANSFERRED", "REQUEUED")


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


def _value_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _widen_value_check(
    table: str, constraint: str, column: str, values: tuple[str, ...]
) -> None:
    """Re-issue a value-list CHECK with the new members included.

    The columns are `native_enum=False`, so the vocabulary lives in a named
    CHECK rather than a PostgreSQL enum type; widening it is a drop and
    recreate, not an `ALTER TYPE ... ADD VALUE`. The constraint names are the
    ones `io_0001` actually issued — the model layer derives a different name
    for its in-memory metadata, and the database only ever saw these.
    """
    op.execute(f"ALTER TABLE {_SCHEMA}.{table} DROP CONSTRAINT IF EXISTS {constraint};")
    op.execute(
        f"ALTER TABLE {_SCHEMA}.{table} ADD CONSTRAINT {constraint} "
        f"CHECK ({column} IN ({_value_list(values)}));"
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)

    # The trail validated an actor and then discarded it: an ownership move
    # could not answer who made it.
    op.add_column(
        "inbox_workflow_events",
        sa.Column("actor_reference", sa.String(160), nullable=True),
        schema=_SCHEMA,
    )

    _widen_value_check(
        "inbox_agent_presence",
        "ck_inbox_agent_presence_state",
        "state",
        _PRESENCE_STATES,
    )
    _widen_value_check(
        "conversation_assignments",
        "ck_conversation_assignments_status",
        "status",
        _ASSIGNMENT_STATUSES,
    )

    op.create_table(
        "inbox_presence_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_reference", sa.String(160), nullable=False),
        sa.Column("previous_state", sa.String(20), nullable=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("previous_capacity", sa.Integer(), nullable=True),
        sa.Column("assignment_capacity", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("actor_reference", sa.String(160), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_presence_events_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_presence_events_tenant_id_id"
        ),
        sa.CheckConstraint(
            f"state IN ({_value_list(_PRESENCE_STATES)})",
            name="ck_inbox_presence_events_state",
        ),
        sa.CheckConstraint(
            f"previous_state IS NULL OR "
            f"previous_state IN ({_value_list(_PRESENCE_STATES)})",
            name="ck_inbox_presence_events_previous_state",
        ),
        sa.CheckConstraint(
            "source IN ('AGENT', 'HEARTBEAT', 'MANAGER', 'SESSION')",
            name="ck_inbox_presence_events_source",
        ),
        sa.CheckConstraint(
            "(source <> 'MANAGER') OR "
            "(actor_reference IS NOT NULL AND reason IS NOT NULL)",
            name="ck_inbox_presence_events_manager_evidence",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_presence_events_tenant_agent_time",
        "inbox_presence_events",
        ["tenant_id", "agent_reference", "occurred_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "inbox_transfer_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_reference", sa.String(180), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("source_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("resulting_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("from_agent_reference", sa.String(160), nullable=False),
        sa.Column("to_agent_reference", sa.String(160), nullable=False),
        sa.Column("from_queue_id", sa.Uuid(), nullable=False),
        sa.Column("to_queue_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by_reference", sa.String(160), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settled_by_reference", sa.String(160), nullable=True),
        sa.Column("settlement_reason", sa.Text(), nullable=True),
        sa.Column(
            "supervisor_override",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("notify_reference", sa.String(160), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_transfer_requests_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_assignment_id"],
            [
                "mod_inbox_ops.conversation_assignments.tenant_id",
                "mod_inbox_ops.conversation_assignments.id",
            ],
            name="fk_inbox_transfer_requests_tenant_source_assignment",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "resulting_assignment_id"],
            [
                "mod_inbox_ops.conversation_assignments.tenant_id",
                "mod_inbox_ops.conversation_assignments.id",
            ],
            name="fk_inbox_transfer_requests_tenant_resulting_assignment",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "from_queue_id"],
            ["mod_inbox_ops.inbox_queues.tenant_id", "mod_inbox_ops.inbox_queues.id"],
            name="fk_inbox_transfer_requests_tenant_from_queue",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "to_queue_id"],
            ["mod_inbox_ops.inbox_queues.tenant_id", "mod_inbox_ops.inbox_queues.id"],
            name="fk_inbox_transfer_requests_tenant_to_queue",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_transfer_requests_tenant_id_id"
        ),
        sa.CheckConstraint(
            "kind IN ('COLD', 'WARM')", name="ck_inbox_transfer_requests_kind"
        ),
        sa.CheckConstraint(
            "status IN ('REQUESTED', 'ACCEPTED', 'DECLINED', 'EXPIRED', "
            "'CANCELLED')",
            name="ck_inbox_transfer_requests_status",
        ),
        sa.CheckConstraint(
            "(supervisor_override = false) OR (override_reason IS NOT NULL)",
            name="ck_inbox_transfer_requests_override_reason",
        ),
        sa.CheckConstraint(
            "(kind <> 'WARM') OR (expires_at IS NOT NULL)",
            name="ck_inbox_transfer_requests_warm_has_sla",
        ),
        sa.CheckConstraint(
            "(status = 'REQUESTED') = (settled_at IS NULL)",
            name="ck_inbox_transfer_requests_settled_coherence",
        ),
        sa.CheckConstraint(
            "(resulting_assignment_id IS NULL) OR (status = 'ACCEPTED')",
            name="ck_inbox_transfer_requests_result_only_when_accepted",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_inbox_transfer_requests_open_conversation",
        "inbox_transfer_requests",
        ["tenant_id", "conversation_reference"],
        schema=_SCHEMA,
        unique=True,
        postgresql_where=sa.text("status = 'REQUESTED'"),
    )
    op.create_index(
        "ix_inbox_transfer_requests_tenant_status_expiry",
        "inbox_transfer_requests",
        ["tenant_id", "status", "expires_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_transfer_requests_tenant_target_status",
        "inbox_transfer_requests",
        ["tenant_id", "to_agent_reference", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "inbox_escalation_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_reference", sa.String(180), nullable=False),
        sa.Column("dedup_key", sa.String(180), nullable=False),
        sa.Column("severity", sa.String(40), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by_reference", sa.String(160), nullable=False),
        sa.Column("notify_reference", sa.String(160), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_escalation_requests_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_escalation_requests_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dedup_key",
            name="uq_inbox_escalation_requests_tenant_dedup_key",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_escalation_requests_tenant_conversation_time",
        "inbox_escalation_requests",
        ["tenant_id", "conversation_reference", "requested_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "inbox_offline_dispositions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("agent_reference", sa.String(160), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_reference", sa.String(180), nullable=False),
        sa.Column("disposition", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("notify_reference", sa.String(160), nullable=True),
        sa.Column("escalation_severity", sa.Integer(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_inbox_offline_dispositions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assignment_id"],
            [
                "mod_inbox_ops.conversation_assignments.tenant_id",
                "mod_inbox_ops.conversation_assignments.id",
            ],
            ondelete="CASCADE",
            name="fk_inbox_offline_dispositions_tenant_assignment",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_inbox_offline_dispositions_tenant_id_id"
        ),
        sa.CheckConstraint(
            "disposition IN ('RETAIN', 'ESCALATE', 'REQUEUE')",
            name="ck_inbox_offline_dispositions_disposition",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SETTLED', 'CANCELLED')",
            name="ck_inbox_offline_dispositions_status",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING') = (settled_at IS NULL)",
            name="ck_inbox_offline_dispositions_settled_coherence",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_inbox_offline_dispositions_pending_assignment",
        "inbox_offline_dispositions",
        ["tenant_id", "assignment_id"],
        schema=_SCHEMA,
        unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.create_index(
        "ix_inbox_offline_dispositions_tenant_status_due",
        "inbox_offline_dispositions",
        ["tenant_id", "status", "due_at"],
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_inbox_ops.refuse_append_only_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% rows are append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER inbox_presence_events_append_only "
        "BEFORE UPDATE OR DELETE ON mod_inbox_ops.inbox_presence_events "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_inbox_ops.refuse_append_only_mutation();"
    )
    op.execute(
        "CREATE TRIGGER inbox_escalation_requests_append_only "
        "BEFORE UPDATE OR DELETE ON mod_inbox_ops.inbox_escalation_requests "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_inbox_ops.refuse_append_only_mutation();"
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
    op.execute("DROP FUNCTION mod_inbox_ops.refuse_append_only_mutation();")
    # Refuse rather than silently widen-forever. Dropping the CHECK would leave
    # the column accepting values the pre-a5 code cannot read, which is a
    # downgrade in name only. Narrowing it back is only safe once no row uses a
    # new member, so the operator is told exactly what to reconcile.
    op.execute(
        """
        DO $$
        DECLARE offending bigint;
        BEGIN
            SELECT count(*) INTO offending
            FROM mod_inbox_ops.conversation_assignments
            WHERE status IN ('TRANSFERRED', 'REQUEUED');
            IF offending > 0 THEN
                RAISE EXCEPTION
                    'cannot downgrade: % assignment(s) are TRANSFERRED or '
                    'REQUEUED. Reconcile them to RELEASED first.', offending
                    USING ERRCODE = '55000';
            END IF;
            SELECT count(*) INTO offending
            FROM mod_inbox_ops.inbox_agent_presence WHERE state = 'ON_BREAK';
            IF offending > 0 THEN
                RAISE EXCEPTION
                    'cannot downgrade: % agent(s) are ON_BREAK. Move them to '
                    'AWAY first.', offending
                    USING ERRCODE = '55000';
            END IF;
        END $$;
        """
    )
    _widen_value_check(
        "conversation_assignments",
        "ck_conversation_assignments_status",
        "status",
        ("ASSIGNED", "RELEASED"),
    )
    _widen_value_check(
        "inbox_agent_presence",
        "ck_inbox_agent_presence_state",
        "state",
        ("AVAILABLE", "AWAY", "OFFLINE"),
    )
    op.drop_column("inbox_workflow_events", "actor_reference", schema=_SCHEMA)
