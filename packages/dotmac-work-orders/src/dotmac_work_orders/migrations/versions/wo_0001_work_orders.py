"""Create the tenant-scoped physical work execution aggregate.

Revision ID: wo_0001_work_orders
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "wo_0001_work_orders"
down_revision = None
branch_labels = ("work_orders",)
REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_workorders"
_TENANT_TABLES = (
    "work_orders",
    "work_order_assignments",
    "work_order_events",
    "work_order_worklogs",
    "work_order_notes",
    "work_order_evidence",
)


def _identity(name: str) -> tuple[sa.Column, sa.Column, sa.UniqueConstraint]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id"),
    )


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["public.tenants.id"],
        ondelete="CASCADE",
        name=f"fk_{name}_tenant",
    )


def _work_order_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "work_order_id"],
        ["mod_workorders.work_orders.tenant_id", "mod_workorders.work_orders.id"],
        ondelete="CASCADE",
        name=f"fk_{name}_work_order",
    )


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def _secure_tenant_table(table: str) -> None:
    qualified = f"{_SCHEMA}.{table}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {qualified} "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    for role in ("app_user", "platform_api"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {qualified} TO {role};")


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_workorders;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_workorders TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        "work_orders",
        *_identity("work_orders"),
        sa.Column("public_id", sa.String(80), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("priority", sa.String(20), nullable=False),
        sa.Column("work_type", sa.String(80), nullable=True),
        sa.Column("current_assignee_id", sa.Uuid(), nullable=True),
        sa.Column("current_assignee_kind", sa.String(40), nullable=True),
        sa.Column("scheduled_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_duration_minutes", sa.Integer(), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("access_notes", sa.Text(), nullable=True),
        sa.Column(
            "required_skills",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "tags",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "minimum_photo_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "customer_signoff_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "signature_unavailable_reason_allowed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "required_evidence_kinds",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "total_active_seconds", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("work_orders"),
        sa.UniqueConstraint(
            "tenant_id", "public_id", name="uq_work_orders_tenant_public_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_work_orders_tenant_status",
        "work_orders",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_work_orders_tenant_assignee",
        "work_orders",
        ["tenant_id", "current_assignee_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "work_order_assignments",
        *_identity("work_order_assignments"),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_kind", sa.String(40), nullable=False),
        sa.Column("assigned_by_id", sa.Uuid(), nullable=False),
        sa.Column("client_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unassigned_by_id", sa.Uuid(), nullable=True),
        sa.Column("unassignment_reason", sa.Text(), nullable=True),
        *_timestamps(),
        _tenant_fk("work_order_assignments"),
        _work_order_fk("work_order_assignments"),
        sa.UniqueConstraint(
            "tenant_id",
            "client_assignment_id",
            name="uq_work_order_assignments_tenant_client",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_work_order_assignments_tenant_work_order",
        "work_order_assignments",
        ["tenant_id", "work_order_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_work_order_assignments_one_active",
        "work_order_assignments",
        ["tenant_id", "work_order_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "work_order_events",
        *_identity("work_order_events"),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("previous_status", sa.String(20), nullable=False),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("client_event_id", sa.Uuid(), nullable=False),
        _tenant_fk("work_order_events"),
        _work_order_fk("work_order_events"),
        sa.UniqueConstraint(
            "tenant_id",
            "client_event_id",
            name="uq_work_order_events_tenant_client",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_work_order_events_tenant_work_order_occurred",
        "work_order_events",
        ["tenant_id", "work_order_id", "occurred_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "work_order_worklogs",
        *_identity("work_order_worklogs"),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("client_worklog_id", sa.Uuid(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        _tenant_fk("work_order_worklogs"),
        _work_order_fk("work_order_worklogs"),
        sa.UniqueConstraint(
            "tenant_id",
            "client_worklog_id",
            name="uq_work_order_worklogs_tenant_client",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_work_order_worklogs_tenant_actor_start",
        "work_order_worklogs",
        ["tenant_id", "actor_id", "started_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_work_order_worklogs_one_open_actor",
        "work_order_worklogs",
        ["tenant_id", "actor_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("ended_at IS NULL AND active"),
    )

    op.create_table(
        "work_order_notes",
        *_identity("work_order_notes"),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("client_note_id", sa.Uuid(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("internal", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _tenant_fk("work_order_notes"),
        _work_order_fk("work_order_notes"),
        sa.UniqueConstraint(
            "tenant_id", "client_note_id", name="uq_work_order_notes_tenant_client"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_work_order_notes_tenant_work_order",
        "work_order_notes",
        ["tenant_id", "work_order_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "work_order_evidence",
        *_identity("work_order_evidence"),
        sa.Column("work_order_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(60), nullable=False),
        sa.Column("artifact_reference", sa.String(255), nullable=False),
        sa.Column("recorded_by_id", sa.Uuid(), nullable=False),
        sa.Column("client_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        *_timestamps(),
        _tenant_fk("work_order_evidence"),
        _work_order_fk("work_order_evidence"),
        sa.UniqueConstraint(
            "tenant_id",
            "client_evidence_id",
            name="uq_work_order_evidence_tenant_client",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_work_order_evidence_tenant_work_order_kind",
        "work_order_evidence",
        ["tenant_id", "work_order_id", "kind"],
        schema=_SCHEMA,
    )

    for table in _TENANT_TABLES:
        _secure_tenant_table(table)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_workorders.work_order_evidence CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_workorders.work_order_notes CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_workorders.work_order_worklogs CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_workorders.work_order_events CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_workorders.work_order_assignments CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_workorders.work_orders CASCADE;")
