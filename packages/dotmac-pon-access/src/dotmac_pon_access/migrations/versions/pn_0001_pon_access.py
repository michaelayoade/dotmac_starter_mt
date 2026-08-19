"""Create PON Access.

Revision ID: pn_0001_pon_access
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "pn_0001_pon_access"
down_revision = None
branch_labels = ("pon_access",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_pon"
_TABLES = (
    "olts",
    "pon_ports",
    "onts",
    "desired_services",
    "pon_observations",
    "reconciliation_runs",
    "backup_evidence",
    "pon_events",
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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_pon;")
    op.execute("GRANT USAGE ON SCHEMA mod_pon TO app_user, platform_api;")

    op.create_table(
        "olts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("management_ref", sa.String(200), nullable=False),
        sa.Column("vendor_family", sa.String(120), nullable=False),
        sa.Column("capability_codes", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("node_ref", sa.String(200)),
        sa.Column("asset_ref", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_pon_olts_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pon_olts_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_pon_olts_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "pon_ports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("olt_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("fiber_endpoint_ref", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_pon_ports_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "olt_id"],
            ["mod_pon.olts.tenant_id", "mod_pon.olts.id"],
            name="fk_pon_ports_olt",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pon_ports_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "olt_id", "slot", "port", name="uq_pon_port_position"
        ),
        sa.CheckConstraint("slot >= 0 AND port >= 0", name="ck_pon_port_position"),
        sa.CheckConstraint("capacity > 0", name="ck_pon_port_capacity"),
        schema=_SCHEMA,
    )
    op.create_table(
        "onts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("serial_number", sa.String(160), nullable=False),
        sa.Column("vendor_family", sa.String(120), nullable=False),
        sa.Column("pon_port_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("service_subject_ref", sa.String(200)),
        sa.Column("assignment_ref", sa.String(240)),
        sa.Column("registration_ref", sa.String(240), nullable=False),
        sa.Column("asset_ref", sa.String(200)),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("commissioned_at", sa.DateTime(timezone=True)),
        sa.Column("commissioned_profile_code", sa.String(120)),
        sa.Column("desired_config_ref", sa.String(240)),
        sa.Column("operation_ref", sa.String(240)),
        _tenant("fk_pon_onts_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pon_port_id"],
            ["mod_pon.pon_ports.tenant_id", "mod_pon.pon_ports.id"],
            name="fk_pon_onts_port",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pon_onts_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "serial_number", name="uq_pon_ont_serial_number"
        ),
        sa.UniqueConstraint(
            "tenant_id", "registration_ref", name="uq_pon_ont_registration_ref"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_pon_onts_port_state",
        "onts",
        ["tenant_id", "pon_port_id", "state"],
        schema=_SCHEMA,
    )
    op.create_table(
        "desired_services",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("ont_id", sa.Uuid(), nullable=False),
        sa.Column("service_ref", sa.String(200), nullable=False),
        sa.Column("profile_code", sa.String(120), nullable=False),
        sa.Column("vlan_ref", sa.String(200)),
        sa.Column("ip_assignment_ref", sa.String(200)),
        sa.Column("desired_fingerprint", sa.String(128), nullable=False),
        sa.Column("observed_fingerprint", sa.String(128)),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("decision_ref", sa.String(240), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_pon_desired_services_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ont_id"],
            ["mod_pon.onts.tenant_id", "mod_pon.onts.id"],
            name="fk_pon_desired_services_ont",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_pon_desired_services_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "ont_id",
            "service_ref",
            name="uq_pon_desired_service",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "pon_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("observation_kind", sa.String(80), nullable=False),
        sa.Column("value", sa.String(240), nullable=False),
        sa.Column("unit", sa.String(40)),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        _tenant("fk_pon_observations_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pon_observations_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "fingerprint",
            name="uq_pon_observation_fingerprint",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_pon_observations_subject",
        "pon_observations",
        ["tenant_id", "subject_ref", "observed_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("desired_service_id", sa.Uuid(), nullable=False),
        sa.Column("observed_fingerprint", sa.String(128), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("drifted", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(120)),
        sa.Column("reconciled_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_pon_reconciliation_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "desired_service_id"],
            [
                "mod_pon.desired_services.tenant_id",
                "mod_pon.desired_services.id",
            ],
            name="fk_pon_reconciliation_desired_service",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_pon_reconciliation_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_pon_reconciliation_desired",
        "reconciliation_runs",
        ["tenant_id", "desired_service_id", "reconciled_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "backup_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("olt_id", sa.Uuid(), nullable=False),
        sa.Column("backup_ref", sa.String(240), nullable=False),
        sa.Column("configuration_fingerprint", sa.String(128), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        _tenant("fk_pon_backup_evidence_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "olt_id"],
            ["mod_pon.olts.tenant_id", "mod_pon.olts.id"],
            name="fk_pon_backup_evidence_olt",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_pon_backup_evidence_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "backup_ref", name="uq_pon_backup_evidence_ref"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "pon_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_ref", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_pon_events_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_pon_events_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_pon_events_aggregate",
        "pon_events",
        ["tenant_id", "aggregate_ref", "occurred_at"],
        schema=_SCHEMA,
    )

    op.execute(
        "CREATE FUNCTION mod_pon.refuse_evidence_mutation() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        "'PON evidence is append-only' USING ERRCODE = '55000'; END; $$;"
    )
    for table in (
        "pon_observations",
        "reconciliation_runs",
        "backup_evidence",
        "pon_events",
    ):
        op.execute(
            f"CREATE TRIGGER pon_{table}_append_only BEFORE UPDATE OR DELETE ON "
            f"mod_pon.{table} FOR EACH ROW EXECUTE FUNCTION "
            "mod_pon.refuse_evidence_mutation();"
        )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_pon.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_pon.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY pon_{table}_tenant_isolation ON mod_pon.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) WITH CHECK "
            "(tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table
            in {
                "pon_observations",
                "reconciliation_runs",
                "backup_evidence",
                "pon_events",
            }
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"GRANT {privileges} ON mod_pon.{table} TO app_user;")
        op.execute(f"GRANT {privileges} ON mod_pon.{table} TO platform_api;")


def downgrade() -> None:
    op.drop_index("ix_pon_events_aggregate", table_name="pon_events", schema=_SCHEMA)
    op.drop_table("pon_events", schema=_SCHEMA)
    op.drop_table("backup_evidence", schema=_SCHEMA)
    op.drop_index(
        "ix_pon_reconciliation_desired",
        table_name="reconciliation_runs",
        schema=_SCHEMA,
    )
    op.drop_table("reconciliation_runs", schema=_SCHEMA)
    op.drop_index(
        "ix_pon_observations_subject",
        table_name="pon_observations",
        schema=_SCHEMA,
    )
    op.drop_table("pon_observations", schema=_SCHEMA)
    op.drop_table("desired_services", schema=_SCHEMA)
    op.drop_index("ix_pon_onts_port_state", table_name="onts", schema=_SCHEMA)
    op.drop_table("onts", schema=_SCHEMA)
    op.drop_table("pon_ports", schema=_SCHEMA)
    op.drop_table("olts", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_pon.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_pon RESTRICT;")
