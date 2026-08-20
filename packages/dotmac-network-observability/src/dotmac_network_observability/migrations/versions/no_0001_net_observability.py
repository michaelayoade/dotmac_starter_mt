"""Create Network Observability.

Revision ID: no_0001_net_observability
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "no_0001_net_observability"
down_revision = None
branch_labels = ("network_observability",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_netobs"
_TABLES = (
    "observations",
    "measurements",
    "availability_facts",
    "health_projections",
    "alerts",
    "alert_evidence",
)


def _tenant(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], name=name, ondelete="CASCADE"
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_netobs;")
    op.execute("GRANT USAGE ON SCHEMA mod_netobs TO app_user, platform_api;")
    op.create_table(
        "observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        _tenant("fk_netobs_observations_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netobs_observations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_netobs_observation_fingerprint",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netobs_observations_subject",
        "observations",
        ["tenant_id", "subject_ref", "observed_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "measurements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("metric_code", sa.String(120), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit", sa.String(40), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("dimensions", sa.JSON(), nullable=False),
        _tenant("fk_netobs_measurements_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netobs_measurements_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_netobs_measurement_fingerprint",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netobs_measurements_subject_metric",
        "measurements",
        ["tenant_id", "subject_ref", "metric_code", "observed_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "availability_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason_code", sa.String(120)),
        _tenant("fk_netobs_availability_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netobs_availability_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_ref",
            "source_ref",
            "observed_at",
            name="uq_netobs_availability_observation",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "health_projections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reason_code", sa.String(120)),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_observation_ids", sa.JSON(), nullable=False),
        sa.Column("rebuilt_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netobs_health_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netobs_health_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "subject_ref", name="uq_netobs_health_subject"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("rule_ref", sa.String(200), nullable=False),
        sa.Column("severity", sa.String(40), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("latest_evidence_ref", sa.String(240), nullable=False),
        _tenant("fk_netobs_alerts_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netobs_alerts_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netobs_alerts_subject_state",
        "alerts",
        ["tenant_id", "subject_ref", "state"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_netobs_open_alert",
        "alerts",
        ["tenant_id", "subject_ref", "rule_ref"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("state = 'open'"),
    )
    op.create_table(
        "alert_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netobs_alert_evidence_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "alert_id"],
            ["mod_netobs.alerts.tenant_id", "mod_netobs.alerts.id"],
            name="fk_netobs_alert_evidence_alert",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netobs_alert_evidence_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "alert_id",
            "evidence_ref",
            "event_type",
            name="uq_netobs_alert_evidence_identity",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netobs_alert_evidence_alert",
        "alert_evidence",
        ["tenant_id", "alert_id", "observed_at"],
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE FUNCTION mod_netobs.refuse_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Network observation evidence is append-only' USING ERRCODE = '55000'; END; $$;"
    )
    for table in (
        "observations",
        "measurements",
        "availability_facts",
        "alert_evidence",
    ):
        op.execute(
            f"CREATE TRIGGER netobs_{table}_append_only BEFORE UPDATE OR DELETE ON mod_netobs.{table} FOR EACH ROW EXECUTE FUNCTION mod_netobs.refuse_evidence_mutation();"
        )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_netobs.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_netobs.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY netobs_{table}_tenant_isolation ON mod_netobs.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table
            in {"observations", "measurements", "availability_facts", "alert_evidence"}
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"GRANT {privileges} ON mod_netobs.{table} TO app_user;")
        op.execute(f"GRANT {privileges} ON mod_netobs.{table} TO platform_api;")


def downgrade() -> None:
    op.drop_index(
        "ix_netobs_alert_evidence_alert", table_name="alert_evidence", schema=_SCHEMA
    )
    op.drop_table("alert_evidence", schema=_SCHEMA)
    op.drop_index("uq_netobs_open_alert", table_name="alerts", schema=_SCHEMA)
    op.drop_index("ix_netobs_alerts_subject_state", table_name="alerts", schema=_SCHEMA)
    op.drop_table("alerts", schema=_SCHEMA)
    op.drop_table("health_projections", schema=_SCHEMA)
    op.drop_table("availability_facts", schema=_SCHEMA)
    op.drop_index(
        "ix_netobs_measurements_subject_metric",
        table_name="measurements",
        schema=_SCHEMA,
    )
    op.drop_table("measurements", schema=_SCHEMA)
    op.drop_index(
        "ix_netobs_observations_subject", table_name="observations", schema=_SCHEMA
    )
    op.drop_table("observations", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_netobs.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_netobs RESTRICT;")
