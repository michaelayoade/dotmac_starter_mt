"""Create Network Access.

Revision ID: nac_0001_network_access
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "nac_0001_network_access"
down_revision = None
branch_labels = ("network_access",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_netaccess"
_TABLES = (
    "nas_attachments",
    "access_projections",
    "authentication_observations",
    "accounting_observations",
    "sessions",
    "reconciliation_runs",
    "access_events",
)


def _tenant(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["public.tenants.id"],
        name=name,
        ondelete="CASCADE",
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_netaccess;")
    op.execute("GRANT USAGE ON SCHEMA mod_netaccess TO app_user, platform_api;")

    op.create_table(
        "nas_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("nas_ref", sa.String(200), nullable=False),
        sa.Column("access_server_ref", sa.String(200), nullable=False),
        sa.Column("capability_code", sa.String(160), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        _tenant("fk_netaccess_nas_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netaccess_nas_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "nas_ref",
            "access_server_ref",
            name="uq_netaccess_nas_attachment",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "access_projections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("desired_state", sa.String(24), nullable=False),
        sa.Column("policy_code", sa.String(120), nullable=False),
        sa.Column("policy_version", sa.String(80), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("decision_ref", sa.String(240), nullable=False),
        sa.Column("desired_fingerprint", sa.String(128), nullable=False),
        sa.Column("observed_fingerprint", sa.String(128)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("projected_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netaccess_projections_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netaccess_projections_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_ref",
            name="uq_netaccess_projection_subject",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "authentication_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("nas_ref", sa.String(200), nullable=False),
        sa.Column("session_ref", sa.String(200)),
        sa.Column("outcome", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(120)),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        _tenant("fk_netaccess_auth_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netaccess_auth_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_netaccess_auth_fingerprint",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netaccess_auth_subject",
        "authentication_observations",
        ["tenant_id", "subject_ref", "observed_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "accounting_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("nas_ref", sa.String(200), nullable=False),
        sa.Column("session_ref", sa.String(200), nullable=False),
        sa.Column("event_kind", sa.String(40), nullable=False),
        sa.Column("input_octets", sa.Integer(), nullable=False),
        sa.Column("output_octets", sa.Integer(), nullable=False),
        sa.Column("session_seconds", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        _tenant("fk_netaccess_accounting_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netaccess_accounting_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_netaccess_accounting_fingerprint",
        ),
        sa.CheckConstraint(
            "input_octets >= 0 AND output_octets >= 0 AND session_seconds >= 0",
            name="ck_netaccess_accounting_counters",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netaccess_accounting_session",
        "accounting_observations",
        ["tenant_id", "session_ref", "observed_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("nas_ref", sa.String(200), nullable=False),
        sa.Column("session_ref", sa.String(200), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("closed_reason_code", sa.String(120)),
        sa.Column("close_source_ref", sa.String(240)),
        sa.Column("input_octets", sa.Integer(), nullable=False),
        sa.Column("output_octets", sa.Integer(), nullable=False),
        _tenant("fk_netaccess_sessions_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netaccess_sessions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "session_ref", name="uq_netaccess_session_ref"
        ),
        sa.CheckConstraint(
            "input_octets >= 0 AND output_octets >= 0",
            name="ck_netaccess_session_counters",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netaccess_sessions_subject_state",
        "sessions",
        ["tenant_id", "subject_ref", "state"],
        schema=_SCHEMA,
    )
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("expected_fingerprint", sa.String(128), nullable=False),
        sa.Column("observed_fingerprint", sa.String(128), nullable=False),
        sa.Column("drifted", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(120)),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netaccess_reconciliation_tenant"),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_netaccess_reconciliation_tenant_id_id",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netaccess_reconciliation_subject",
        "reconciliation_runs",
        ["tenant_id", "subject_ref", "reconciled_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "access_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_ref", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netaccess_events_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netaccess_events_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netaccess_events_aggregate",
        "access_events",
        ["tenant_id", "aggregate_ref", "occurred_at"],
        schema=_SCHEMA,
    )

    op.execute(
        "CREATE FUNCTION mod_netaccess.refuse_evidence_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        "'Network access evidence is append-only' USING ERRCODE = '55000'; "
        "END; $$;"
    )
    for table in (
        "authentication_observations",
        "accounting_observations",
        "reconciliation_runs",
        "access_events",
    ):
        op.execute(
            f"CREATE TRIGGER netaccess_{table}_append_only BEFORE UPDATE OR DELETE "
            f"ON mod_netaccess.{table} FOR EACH ROW EXECUTE FUNCTION "
            "mod_netaccess.refuse_evidence_mutation();"
        )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_netaccess.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_netaccess.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY netaccess_{table}_tenant_isolation ON "
            f"mod_netaccess.{table} USING "
            "(tenant_id = public.app_current_tenant_id()) WITH CHECK "
            "(tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table
            in {
                "authentication_observations",
                "accounting_observations",
                "reconciliation_runs",
                "access_events",
            }
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"GRANT {privileges} ON mod_netaccess.{table} TO app_user;")
        op.execute(f"GRANT {privileges} ON mod_netaccess.{table} TO platform_api;")


def downgrade() -> None:
    op.drop_index(
        "ix_netaccess_events_aggregate",
        table_name="access_events",
        schema=_SCHEMA,
    )
    op.drop_table("access_events", schema=_SCHEMA)
    op.drop_index(
        "ix_netaccess_reconciliation_subject",
        table_name="reconciliation_runs",
        schema=_SCHEMA,
    )
    op.drop_table("reconciliation_runs", schema=_SCHEMA)
    op.drop_index(
        "ix_netaccess_sessions_subject_state",
        table_name="sessions",
        schema=_SCHEMA,
    )
    op.drop_table("sessions", schema=_SCHEMA)
    op.drop_index(
        "ix_netaccess_accounting_session",
        table_name="accounting_observations",
        schema=_SCHEMA,
    )
    op.drop_table("accounting_observations", schema=_SCHEMA)
    op.drop_index(
        "ix_netaccess_auth_subject",
        table_name="authentication_observations",
        schema=_SCHEMA,
    )
    op.drop_table("authentication_observations", schema=_SCHEMA)
    op.drop_table("access_projections", schema=_SCHEMA)
    op.drop_table("nas_attachments", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_netaccess.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_netaccess RESTRICT;")
