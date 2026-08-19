"""Create managed Network Inventory.

Revision ID: ni_0001_network_inventory
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ni_0001_network_inventory"
down_revision = None
branch_labels = ("network_inventory",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_netinv"
_TABLES = (
    "sites",
    "nodes",
    "interfaces",
    "ports",
    "vlans",
    "vlan_attachments",
    "configuration_snapshots",
    "network_inventory_events",
)


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], name=name, ondelete="CASCADE"
    )


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_netinv;")
    op.execute("GRANT USAGE ON SCHEMA mod_netinv TO app_user, platform_api;")
    op.create_table(
        "sites",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("site_kind", sa.String(80), nullable=False),
        sa.Column("location_ref", sa.String(200)),
        _created_at(),
        _tenant_fk("fk_netinv_sites_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netinv_sites_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_netinv_sites_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "nodes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("site_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("management_identity", sa.String(200), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("role_codes", sa.JSON(), nullable=False),
        sa.Column("capability_codes", sa.JSON(), nullable=False),
        sa.Column("asset_ref", sa.String(200)),
        sa.Column("source_ref", sa.String(240)),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        _tenant_fk("fk_netinv_nodes_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            ["mod_netinv.sites.tenant_id", "mod_netinv.sites.id"],
            name="fk_netinv_nodes_site",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netinv_nodes_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_netinv_nodes_tenant_code"),
        sa.UniqueConstraint(
            "tenant_id",
            "management_identity",
            name="uq_netinv_nodes_management_identity",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netinv_nodes_state", "nodes", ["tenant_id", "state"], schema=_SCHEMA
    )
    op.create_table(
        "interfaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("interface_kind", sa.String(80), nullable=False),
        sa.Column("mac_address", sa.String(32)),
        sa.Column("admin_state", sa.String(24), nullable=False),
        sa.Column("source_ref", sa.String(240)),
        _created_at(),
        _tenant_fk("fk_netinv_interfaces_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            ["mod_netinv.nodes.tenant_id", "mod_netinv.nodes.id"],
            name="fk_netinv_interfaces_node",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netinv_interfaces_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "node_id", "name", name="uq_netinv_interfaces_node_name"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "ports",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("interface_id", sa.Uuid()),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("port_kind", sa.String(80), nullable=False),
        sa.Column("source_ref", sa.String(240)),
        _created_at(),
        _tenant_fk("fk_netinv_ports_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            ["mod_netinv.nodes.tenant_id", "mod_netinv.nodes.id"],
            name="fk_netinv_ports_node",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interface_id"],
            ["mod_netinv.interfaces.tenant_id", "mod_netinv.interfaces.id"],
            name="fk_netinv_ports_interface",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netinv_ports_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "node_id", "name", name="uq_netinv_ports_node_name"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "vlans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("vlan_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("purpose", sa.String(120), nullable=False),
        sa.Column("site_ref", sa.String(200)),
        _created_at(),
        _tenant_fk("fk_netinv_vlans_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netinv_vlans_tenant_id_id"),
        sa.CheckConstraint("vlan_id BETWEEN 1 AND 4094", name="ck_netinv_vlan_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_netinv_vlans_global",
        "vlans",
        ["tenant_id", "vlan_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("site_ref IS NULL"),
    )
    op.create_index(
        "uq_netinv_vlans_site",
        "vlans",
        ["tenant_id", "vlan_id", "site_ref"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("site_ref IS NOT NULL"),
    )
    op.create_table(
        "vlan_attachments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("vlan_id", sa.Uuid(), nullable=False),
        sa.Column("interface_id", sa.Uuid()),
        sa.Column("port_id", sa.Uuid()),
        sa.Column("tagged", sa.Boolean(), nullable=False),
        sa.Column("source_ref", sa.String(240)),
        _created_at(),
        _tenant_fk("fk_netinv_vlan_attachments_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "vlan_id"],
            ["mod_netinv.vlans.tenant_id", "mod_netinv.vlans.id"],
            name="fk_netinv_vlan_attachments_vlan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "interface_id"],
            ["mod_netinv.interfaces.tenant_id", "mod_netinv.interfaces.id"],
            name="fk_netinv_vlan_attachments_interface",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "port_id"],
            ["mod_netinv.ports.tenant_id", "mod_netinv.ports.id"],
            name="fk_netinv_vlan_attachments_port",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netinv_vlan_attachments_tenant_id_id"
        ),
        sa.CheckConstraint(
            "(interface_id IS NOT NULL) <> (port_id IS NOT NULL)",
            name="ck_netinv_vlan_attachment_one_target",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_netinv_vlan_attachment_interface",
        "vlan_attachments",
        ["tenant_id", "vlan_id", "interface_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("interface_id IS NOT NULL"),
    )
    op.create_index(
        "uq_netinv_vlan_attachment_port",
        "vlan_attachments",
        ["tenant_id", "vlan_id", "port_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("port_id IS NOT NULL"),
    )
    op.create_table(
        "configuration_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(80), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("fk_netinv_config_snapshots_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            ["mod_netinv.nodes.tenant_id", "mod_netinv.nodes.id"],
            name="fk_netinv_config_snapshots_node",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_netinv_config_snapshots_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "node_id",
            "fingerprint",
            name="uq_netinv_config_snapshot_fingerprint",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "network_inventory_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_ref", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("fk_netinv_events_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_netinv_events_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_netinv_events_aggregate",
        "network_inventory_events",
        ["tenant_id", "aggregate_ref", "occurred_at"],
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE FUNCTION mod_netinv.refuse_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Network Inventory evidence is append-only' USING ERRCODE = '55000'; END; $$;"
    )
    for table in ("configuration_snapshots", "network_inventory_events"):
        op.execute(
            f"CREATE TRIGGER netinv_{table}_append_only BEFORE UPDATE OR DELETE ON mod_netinv.{table} FOR EACH ROW EXECUTE FUNCTION mod_netinv.refuse_evidence_mutation();"
        )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_netinv.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_netinv.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY netinv_{table}_tenant_isolation ON mod_netinv.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table in {"configuration_snapshots", "network_inventory_events"}
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"GRANT {privileges} ON mod_netinv.{table} TO app_user;")
        op.execute(f"GRANT {privileges} ON mod_netinv.{table} TO platform_api;")


def downgrade() -> None:
    op.drop_index(
        "ix_netinv_events_aggregate",
        table_name="network_inventory_events",
        schema=_SCHEMA,
    )
    op.drop_table("network_inventory_events", schema=_SCHEMA)
    op.drop_table("configuration_snapshots", schema=_SCHEMA)
    op.drop_index(
        "uq_netinv_vlan_attachment_port",
        table_name="vlan_attachments",
        schema=_SCHEMA,
    )
    op.drop_index(
        "uq_netinv_vlan_attachment_interface",
        table_name="vlan_attachments",
        schema=_SCHEMA,
    )
    op.drop_table("vlan_attachments", schema=_SCHEMA)
    op.drop_index("uq_netinv_vlans_site", table_name="vlans", schema=_SCHEMA)
    op.drop_index("uq_netinv_vlans_global", table_name="vlans", schema=_SCHEMA)
    op.drop_table("vlans", schema=_SCHEMA)
    op.drop_table("ports", schema=_SCHEMA)
    op.drop_table("interfaces", schema=_SCHEMA)
    op.drop_index("ix_netinv_nodes_state", table_name="nodes", schema=_SCHEMA)
    op.drop_table("nodes", schema=_SCHEMA)
    op.drop_table("sites", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_netinv.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_netinv RESTRICT;")
