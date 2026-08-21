"""Create Network Control.

Revision ID: nc_0001_network_control
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "nc_0001_network_control"
down_revision = None
branch_labels = ("network_control",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_netctrl"
_TABLES = (
    "commands",
    "command_events",
    "dispatches",
    "execution_evidence",
    "reconciliation_runs",
)


def _tenant(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], name=name, ondelete="CASCADE"
    )


def _command_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "command_id"],
        ["mod_netctrl.commands.tenant_id", "mod_netctrl.commands.id"],
        name=name,
        ondelete="CASCADE",
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_netctrl;")
    op.execute("GRANT USAGE ON SCHEMA mod_netctrl TO app_user, platform_api;")
    op.create_table(
        "commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("operation_code", sa.String(120), nullable=False),
        sa.Column("target_ref", sa.String(200), nullable=False),
        sa.Column("capability_code", sa.String(160), nullable=False),
        sa.Column("parameters", sa.JSON(), nullable=False),
        sa.Column("request_fingerprint", sa.String(128), nullable=False),
        sa.Column("correlation_ref", sa.String(200), nullable=False),
        sa.Column("requested_by_ref", sa.String(200), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_at", sa.DateTime(timezone=True)),
        _tenant("fk_netctrl_commands_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netctrl_commands_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "correlation_ref", name="uq_netctrl_command_correlation"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netctrl_commands_state",
        "commands",
        ["tenant_id", "state", "requested_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "command_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netctrl_events_tenant"),
        _command_fk("fk_netctrl_events_command"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netctrl_events_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netctrl_events_command",
        "command_events",
        ["tenant_id", "command_id", "occurred_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "dispatches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_ref", sa.String(200), nullable=False),
        sa.Column("plugin_capability", sa.String(160), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netctrl_dispatches_tenant"),
        _command_fk("fk_netctrl_dispatches_command"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netctrl_dispatches_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "dispatch_ref", name="uq_netctrl_dispatch_ref"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "execution_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_ref", sa.String(200), nullable=False),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("result_fingerprint", sa.String(128), nullable=False),
        sa.Column("error_code", sa.String(120)),
        _tenant("fk_netctrl_execution_tenant"),
        _command_fk("fk_netctrl_execution_command"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dispatch_ref"],
            ["mod_netctrl.dispatches.tenant_id", "mod_netctrl.dispatches.dispatch_ref"],
            name="fk_netctrl_execution_dispatch",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netctrl_execution_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dispatch_ref",
            "result_fingerprint",
            name="uq_netctrl_execution_result",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.Uuid(), nullable=False),
        sa.Column("missing_dispatch_refs", sa.JSON(), nullable=False),
        sa.Column("unexpected_dispatch_refs", sa.JSON(), nullable=False),
        sa.Column("changed", sa.Boolean(), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netctrl_reconciliation_tenant"),
        _command_fk("fk_netctrl_reconciliation_command"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netctrl_reconciliation_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE FUNCTION mod_netctrl.refuse_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Network control evidence is append-only' USING ERRCODE = '55000'; END; $$;"
    )
    for table in (
        "command_events",
        "dispatches",
        "execution_evidence",
        "reconciliation_runs",
    ):
        op.execute(
            f"CREATE TRIGGER netctrl_{table}_append_only BEFORE UPDATE OR DELETE ON mod_netctrl.{table} FOR EACH ROW EXECUTE FUNCTION mod_netctrl.refuse_evidence_mutation();"
        )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_netctrl.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_netctrl.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY netctrl_{table}_tenant_isolation ON mod_netctrl.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table != "commands"
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"GRANT {privileges} ON mod_netctrl.{table} TO app_user;")
        op.execute(f"GRANT {privileges} ON mod_netctrl.{table} TO platform_api;")


def downgrade() -> None:
    op.drop_table("reconciliation_runs", schema=_SCHEMA)
    op.drop_table("execution_evidence", schema=_SCHEMA)
    op.drop_table("dispatches", schema=_SCHEMA)
    op.drop_index(
        "ix_netctrl_events_command", table_name="command_events", schema=_SCHEMA
    )
    op.drop_table("command_events", schema=_SCHEMA)
    op.drop_index("ix_netctrl_commands_state", table_name="commands", schema=_SCHEMA)
    op.drop_table("commands", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_netctrl.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_netctrl RESTRICT;")
