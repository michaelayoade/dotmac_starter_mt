"""Create the tenant-only IPAM owner.

Revision ID: ip_0001_ipam
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ip_0001_ipam"
down_revision = None
branch_labels = ("ipam",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_ipam"
_TABLES = (
    "address_spaces",
    "pools",
    "addresses",
    "assignments",
    "utilization_snapshots",
    "ipam_events",
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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_ipam;")
    op.execute("GRANT USAGE ON SCHEMA mod_ipam TO app_user, platform_api;")

    op.create_table(
        "address_spaces",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("family", sa.String(8), nullable=False),
        sa.Column("prefix", sa.String(64), nullable=False),
        sa.Column("routing_domain_ref", sa.String(160), nullable=True),
        _created_at(),
        _tenant_fk("fk_ipam_spaces_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ipam_spaces_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_ipam_spaces_tenant_code"),
        sa.CheckConstraint("family IN ('ipv4', 'ipv6')", name="ck_ipam_spaces_family"),
        schema=_SCHEMA,
    )
    op.create_table(
        "pools",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("address_space_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("prefix", sa.String(64), nullable=False),
        sa.Column("allocation_prefix_length", sa.Integer(), nullable=False),
        sa.Column("purpose", sa.String(120), nullable=False),
        _created_at(),
        _tenant_fk("fk_ipam_pools_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "address_space_id"],
            ["mod_ipam.address_spaces.tenant_id", "mod_ipam.address_spaces.id"],
            name="fk_ipam_pools_space",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ipam_pools_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_ipam_pools_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_table(
        "addresses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("reservation_purpose", sa.String(120), nullable=True),
        sa.Column("reservation_ref", sa.String(200), nullable=True),
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _tenant_fk("fk_ipam_addresses_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            ["mod_ipam.pools.tenant_id", "mod_ipam.pools.id"],
            name="fk_ipam_addresses_pool",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ipam_addresses_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "pool_id", "address", name="uq_ipam_addresses_pool_address"
        ),
        sa.CheckConstraint(
            "state IN ('available', 'reserved', 'assigned', 'quarantined')",
            name="ck_ipam_addresses_state",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ipam_addresses_available",
        "addresses",
        ["tenant_id", "pool_id", "state"],
        schema=_SCHEMA,
    )
    op.create_table(
        "assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("address_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("assignment_kind", sa.String(80), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(240), nullable=True),
        _tenant_fk("fk_ipam_assignments_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "address_id"],
            ["mod_ipam.addresses.tenant_id", "mod_ipam.addresses.id"],
            name="fk_ipam_assignments_address",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ipam_assignments_tenant_id_id"),
        sa.CheckConstraint(
            "state IN ('active', 'released')", name="ck_ipam_assignments_state"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_ipam_assignments_active_address",
        "assignments",
        ["tenant_id", "address_id"],
        unique=True,
        postgresql_where=sa.text("state = 'active'"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ipam_assignments_subject",
        "assignments",
        ["tenant_id", "subject_ref", "state"],
        schema=_SCHEMA,
    )
    op.create_table(
        "utilization_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("assigned", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        _tenant_fk("fk_ipam_utilization_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            ["mod_ipam.pools.tenant_id", "mod_ipam.pools.id"],
            name="fk_ipam_utilization_pool",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ipam_utilization_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "pool_id",
            "source_ref",
            "observed_at",
            name="uq_ipam_utilization_observation",
        ),
        sa.CheckConstraint(
            "total >= 0 AND available >= 0 AND reserved >= 0 AND assigned >= 0",
            name="ck_ipam_utilization_nonnegative",
        ),
        sa.CheckConstraint(
            "available + reserved + assigned = total",
            name="ck_ipam_utilization_balanced",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "ipam_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_ref", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("fk_ipam_events_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ipam_events_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ipam_events_aggregate",
        "ipam_events",
        ["tenant_id", "aggregate_ref", "occurred_at"],
        schema=_SCHEMA,
    )

    op.execute(
        "CREATE FUNCTION mod_ipam.refuse_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'IPAM evidence is append-only' USING ERRCODE = '55000'; END; $$;"
    )
    for table in ("utilization_snapshots", "ipam_events"):
        op.execute(
            f"CREATE TRIGGER ipam_{table}_append_only BEFORE UPDATE OR DELETE ON mod_ipam.{table} FOR EACH ROW EXECUTE FUNCTION mod_ipam.refuse_evidence_mutation();"
        )

    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_ipam.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_ipam.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY ipam_{table}_tenant_isolation ON mod_ipam.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table in {"utilization_snapshots", "ipam_events"}
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"GRANT {privileges} ON mod_ipam.{table} TO app_user;")
        op.execute(f"GRANT {privileges} ON mod_ipam.{table} TO platform_api;")


def downgrade() -> None:
    op.drop_index("ix_ipam_events_aggregate", table_name="ipam_events", schema=_SCHEMA)
    op.drop_table("ipam_events", schema=_SCHEMA)
    op.drop_table("utilization_snapshots", schema=_SCHEMA)
    op.drop_index(
        "ix_ipam_assignments_subject", table_name="assignments", schema=_SCHEMA
    )
    op.drop_index(
        "uq_ipam_assignments_active_address", table_name="assignments", schema=_SCHEMA
    )
    op.drop_table("assignments", schema=_SCHEMA)
    op.drop_index("ix_ipam_addresses_available", table_name="addresses", schema=_SCHEMA)
    op.drop_table("addresses", schema=_SCHEMA)
    op.drop_table("pools", schema=_SCHEMA)
    op.drop_table("address_spaces", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_ipam.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_ipam RESTRICT;")
