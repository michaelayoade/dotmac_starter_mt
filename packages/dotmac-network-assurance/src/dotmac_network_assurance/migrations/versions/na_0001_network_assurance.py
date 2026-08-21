"""Create Network Assurance.

Revision ID: na_0001_network_assurance
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "na_0001_network_assurance"
down_revision = None
branch_labels = ("network_assurance",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_netassure"
_TABLES = (
    "incidents",
    "incident_events",
    "impacts",
    "maintenance_windows",
    "notification_evidence",
    "sla_evidence",
)


def _tenant(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], name=name, ondelete="CASCADE"
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_netassure;")
    op.execute("GRANT USAGE ON SCHEMA mod_netassure TO app_user, platform_api;")
    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("summary", sa.String(240), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("detection_ref", sa.String(240), nullable=False),
        sa.Column("source_observation_refs", sa.JSON(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolution_code", sa.String(120)),
        sa.Column("resolution_summary", sa.Text()),
        _tenant("fk_netassure_incidents_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netassure_incidents_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_netassure_incidents_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netassure_incidents_state",
        "incidents",
        ["tenant_id", "state", "severity"],
        schema=_SCHEMA,
    )
    op.create_table(
        "incident_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netassure_events_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "incident_id"],
            ["mod_netassure.incidents.tenant_id", "mod_netassure.incidents.id"],
            name="fk_netassure_events_incident",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netassure_events_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netassure_events_incident",
        "incident_events",
        ["tenant_id", "incident_id", "occurred_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "impacts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("subject_kind", sa.String(80), nullable=False),
        sa.Column("severity", sa.String(24), nullable=False),
        sa.Column("topology_path_ref", sa.String(200)),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netassure_impacts_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "incident_id"],
            ["mod_netassure.incidents.tenant_id", "mod_netassure.incidents.id"],
            name="fk_netassure_impacts_incident",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netassure_impacts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "incident_id",
            "subject_ref",
            name="uq_netassure_impact_subject",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "maintenance_windows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("summary", sa.String(240), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scope_refs", sa.JSON(), nullable=False),
        sa.Column("change_ref", sa.String(200)),
        _tenant("fk_netassure_maintenance_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netassure_maintenance_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_netassure_maintenance_tenant_code"
        ),
        sa.CheckConstraint(
            "ends_at > starts_at", name="ck_netassure_maintenance_interval"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "notification_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("delivery_ref", sa.String(240), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_netassure_notifications_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "incident_id"],
            ["mod_netassure.incidents.tenant_id", "mod_netassure.incidents.id"],
            name="fk_netassure_notifications_incident",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netassure_notifications_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "delivery_ref", name="uq_netassure_notification_delivery"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "sla_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_seconds", sa.Numeric(24, 6), nullable=False),
        sa.Column("unavailable_seconds", sa.Numeric(24, 6), nullable=False),
        sa.Column("availability_ratio", sa.Numeric(12, 9), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        _tenant("fk_netassure_sla_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netassure_sla_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_ref",
            "period_start",
            "period_end",
            "source_ref",
            name="uq_netassure_sla_period",
        ),
        sa.CheckConstraint(
            "period_end > period_start AND available_seconds >= 0 AND unavailable_seconds >= 0 AND availability_ratio BETWEEN 0 AND 1",
            name="ck_netassure_sla_values",
        ),
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE FUNCTION mod_netassure.refuse_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Assurance evidence is append-only' USING ERRCODE = '55000'; END; $$;"
    )
    for table in ("incident_events", "notification_evidence", "sla_evidence"):
        op.execute(
            f"CREATE TRIGGER netassure_{table}_append_only BEFORE UPDATE OR DELETE ON mod_netassure.{table} FOR EACH ROW EXECUTE FUNCTION mod_netassure.refuse_evidence_mutation();"
        )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_netassure.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_netassure.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY netassure_{table}_tenant_isolation ON mod_netassure.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table in {"incident_events", "notification_evidence", "sla_evidence"}
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"GRANT {privileges} ON mod_netassure.{table} TO app_user;")
        op.execute(f"GRANT {privileges} ON mod_netassure.{table} TO platform_api;")


def downgrade() -> None:
    op.drop_table("sla_evidence", schema=_SCHEMA)
    op.drop_table("notification_evidence", schema=_SCHEMA)
    op.drop_table("maintenance_windows", schema=_SCHEMA)
    op.drop_table("impacts", schema=_SCHEMA)
    op.drop_index(
        "ix_netassure_events_incident", table_name="incident_events", schema=_SCHEMA
    )
    op.drop_table("incident_events", schema=_SCHEMA)
    op.drop_index(
        "ix_netassure_incidents_state", table_name="incidents", schema=_SCHEMA
    )
    op.drop_table("incidents", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_netassure.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_netassure RESTRICT;")
