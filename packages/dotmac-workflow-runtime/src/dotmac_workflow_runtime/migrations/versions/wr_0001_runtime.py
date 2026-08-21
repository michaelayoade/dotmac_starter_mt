"""Create the tenant Workflow Runtime owner.

Revision ID: wr_0001_runtime
Revises: (lineage root)
Create Date: 2026-08-21

Every table carries tenant_id NOT NULL and UNIQUE (tenant_id, id).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from alembic import op
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

revision = "wr_0001_runtime"
down_revision = None
branch_labels = ("workflow_runtime",)

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_workflow"
_TABLES = ("workflow_executions", "workflow_checkpoints", "workflow_repairs")


def _identity() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
    )


def _tenant_constraints(name: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id"),
    )


def _secure_tenant_tables(tables: Iterable[str]) -> None:
    for table in tables:
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {_SCHEMA}.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.{table} TO app_user;"
        )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_workflow;")
    op.execute("REVOKE ALL ON SCHEMA mod_workflow FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_workflow TO app_user, app_admin;")

    op.create_table(
        "workflow_executions",
        *_identity(),
        sa.Column("definition_version_ref", sa.String(255), nullable=False),
        sa.Column("definition_digest", sa.String(64), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant_constraints("workflow_executions"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_workflow_executions_source_event",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_executions_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_workflow_executions_tenant_subject",
        "workflow_executions",
        ["tenant_id", "subject_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "workflow_checkpoints",
        *_identity(),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_owner_ref", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("output_ref", sa.String(500), nullable=True),
        sa.Column("output_digest", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant_constraints("workflow_checkpoints"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "execution_id"],
            ["mod_workflow.workflow_executions.tenant_id", "mod_workflow.workflow_executions.id"],
            ondelete="CASCADE",
            name="fk_workflow_checkpoints_execution",
        ),
        sa.UniqueConstraint(
            "tenant_id", "execution_id", "code", name="uq_workflow_checkpoints_execution_code"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "execution_id",
            "position",
            name="uq_workflow_checkpoints_execution_position",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'leased', 'retryable', 'succeeded', 'failed')",
            name="ck_workflow_checkpoints_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0 AND attempt_count <= max_attempts",
            name="ck_workflow_checkpoints_attempts",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "workflow_repairs",
        *_identity(),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("repaired_by_ref", sa.String(255), nullable=False),
        sa.Column("repaired_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("workflow_repairs"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "execution_id"],
            ["mod_workflow.workflow_executions.tenant_id", "mod_workflow.workflow_executions.id"],
            ondelete="RESTRICT",
            name="fk_workflow_repairs_execution",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "checkpoint_id"],
            ["mod_workflow.workflow_checkpoints.tenant_id", "mod_workflow.workflow_checkpoints.id"],
            ondelete="RESTRICT",
            name="fk_workflow_repairs_checkpoint",
        ),
        schema=_SCHEMA,
    )

    _secure_tenant_tables(_TABLES)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_workflow;")
