"""Extract Sub's proven IPAM shape into ``mod_ipam`` (0.2.0a1).

Revision ID: ip_0002_extracted
Revises: ip_0001_ipam
Create Date: 2026-10-28

Additive successor to the 0.1 template (``ip_0001`` bytes are untouched). It
REFUSES to run when any 0.1 template table holds a row (0.1.0a1 is
must-not-install; nothing legitimate can be there), then drops the five
template tables and creates the Sub-shaped tables with ``tenant_id``,
composite ``(tenant_id, id)`` keys and forced RLS. ``ipam_events`` and its
append-only trigger are kept as they are.

Sub names are kept (``ip_pools``, ``ip_assignments`` ...). Product identities
are opaque UUIDs with no foreign key; ``ont_unit_id`` became ``device_ref``.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ip_0002_extracted"
down_revision = "ip_0001_ipam"
branch_labels = None
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_ipam"
#: 0.1 template tables, dropped children-first. ``ipam_events`` is kept.
_TEMPLATE_DROP_ORDER = (
    "utilization_snapshots",
    "assignments",
    "addresses",
    "pools",
    "address_spaces",
)
_TEMPLATE_CREATE_ORDER = tuple(reversed(_TEMPLATE_DROP_ORDER))
#: Extracted tables, parents first.
_NEW_TABLES = (
    "ip_pools",
    "ip_blocks",
    "ipv4_addresses",
    "ipv6_addresses",
    "ip_assignments",
    "subscriber_additional_routes",
    "ipv6_delegated_prefixes",
    "ip_pool_utilization_snapshots",
)
_PRIMARY_WHERE = (
    "is_active AND is_primary AND ipv4_address_id IS NOT NULL "
    "AND subscription_id IS NOT NULL"
)


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], name=name, ondelete="CASCADE"
    )


def _ts(name: str, *, nullable: bool = False, default: bool = True) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default else None,
    )


def _pool_fk(table: str, ondelete: str = "RESTRICT") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "pool_id"],
        ["mod_ipam.ip_pools.tenant_id", "mod_ipam.ip_pools.id"],
        name=f"fk_{table}_pool",
        ondelete=ondelete,
    )


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def _enforce_rls(table: str) -> None:
    op.execute(f"ALTER TABLE mod_ipam.{table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE mod_ipam.{table} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON mod_ipam.{table} "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )


def _grant(table: str, privileges: str) -> None:
    op.execute(f"GRANT {privileges} ON mod_ipam.{table} TO app_user;")
    op.execute(f"GRANT {privileges} ON mod_ipam.{table} TO platform_api;")


def _lock_and_see_all_rows(tables: tuple[str, ...]) -> None:
    """Bounded exclusive locks, then RLS off so FORCE RLS cannot hide rows.

    Under forced RLS an unprivileged migration role would see zero rows and the
    refusal would silently pass (fail open); ``row_security = off`` makes a
    role that cannot bypass RLS ERROR instead (fail closed). Same shape as
    bi_0004 / bi_0008."""
    op.execute("SET LOCAL lock_timeout = '5s'; SET LOCAL statement_timeout = '60s';")
    for table in tables:
        op.execute(f"LOCK TABLE mod_ipam.{table} IN ACCESS EXCLUSIVE MODE;")
    op.execute("SET LOCAL row_security = off;")


def _refuse_if_template_rows(action: str) -> None:
    bind = op.get_bind()
    _lock_and_see_all_rows(_TEMPLATE_DROP_ORDER)
    for table in _TEMPLATE_DROP_ORDER:
        if bind.scalar(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM mod_ipam.{table})")  # noqa: S608  # nosec B608 - constant table tuple
        ):
            raise RuntimeError(
                f"cannot {action} ip_0002: 0.1 template table mod_ipam.{table} "
                "holds data (dotmac-ipam 0.1.0a1 must not be installed or used)"
            )


def _refuse_if_extracted_rows() -> None:
    bind = op.get_bind()
    _lock_and_see_all_rows(tuple(reversed(_NEW_TABLES)))
    for table in _NEW_TABLES:
        if bind.scalar(
            sa.text(f"SELECT EXISTS (SELECT 1 FROM mod_ipam.{table})")  # noqa: S608  # nosec B608 - constant table tuple
        ):
            raise RuntimeError(
                f"cannot downgrade ip_0002: mod_ipam.{table} holds data; the "
                "0.1 template shape cannot represent it"
            )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    _refuse_if_template_rows("upgrade")
    for table in _TEMPLATE_DROP_ORDER:
        op.drop_table(table, schema=_SCHEMA)

    op.create_table(
        "ip_pools",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("ip_version", sa.String(8), nullable=False, server_default="ipv4"),
        sa.Column("cidr", sa.String(64), nullable=False),
        sa.Column("gateway", sa.String(64), nullable=True),
        sa.Column("dns_primary", sa.String(64), nullable=True),
        sa.Column("dns_secondary", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("delegation_prefix_length", sa.Integer(), nullable=True),
        sa.Column("site_ref", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column(
            "allow_network_broadcast",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        _ts("created_at"),
        _ts("updated_at"),
        _tenant_fk("fk_ip_pools_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ip_pools_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_ip_pools_tenant_name"),
        sa.CheckConstraint(
            "ip_version IN ('ipv4', 'ipv6')", name="ck_ip_pools_version"
        ),
        sa.CheckConstraint(
            "purpose IN ('customer', 'infrastructure', 'management')",
            name="ck_ip_pools_purpose",
        ),
        sa.CheckConstraint(
            "purpose <> 'customer' OR site_ref IS NOT NULL",
            name="ck_ip_pools_customer_site",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ip_pools_site_ref", "ip_pools", ["tenant_id", "site_ref"], schema=_SCHEMA
    )

    op.create_table(
        "ip_blocks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("cidr", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _tenant_fk("fk_ip_blocks_tenant"),
        _pool_fk("ip_blocks"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ip_blocks_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "pool_id", "cidr", name="uq_ip_blocks_pool_cidr"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "ipv4_addresses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.String(15), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=True),
        sa.Column("is_reserved", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("device_ref", sa.Uuid(), nullable=True),
        sa.Column("allocation_type", sa.String(20), nullable=True),
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _tenant_fk("fk_ipv4_addresses_tenant"),
        _pool_fk("ipv4_addresses"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ipv4_addresses_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "address", name="uq_ipv4_addresses_address"),
        sa.CheckConstraint(
            "allocation_type IS NULL OR allocation_type IN "
            "('management', 'wan', 'static', 'device')",
            name="ck_ipv4_addresses_allocation_type",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ipv4_addresses_pool",
        "ipv4_addresses",
        ["tenant_id", "pool_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ipv4_addresses_device_ref",
        "ipv4_addresses",
        ["tenant_id", "device_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "ipv6_addresses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.String(64), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=True),
        sa.Column("is_reserved", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("reserved_until", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _tenant_fk("fk_ipv6_addresses_tenant"),
        _pool_fk("ipv6_addresses"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ipv6_addresses_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "address", name="uq_ipv6_addresses_address"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ipv6_addresses_pool",
        "ipv6_addresses",
        ["tenant_id", "pool_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "ip_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subscriber_id", sa.Uuid(), nullable=True),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("ip_version", sa.String(8), nullable=False, server_default="ipv4"),
        sa.Column("ipv4_address_id", sa.Uuid(), nullable=True),
        sa.Column("ipv6_address_id", sa.Uuid(), nullable=True),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("prefix_length", sa.Integer(), nullable=True),
        sa.Column("gateway", sa.String(64), nullable=True),
        sa.Column("dns_primary", sa.String(64), nullable=True),
        sa.Column("dns_secondary", sa.String(64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.String(240), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _tenant_fk("fk_ip_assignments_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ipv4_address_id"],
            ["mod_ipam.ipv4_addresses.tenant_id", "mod_ipam.ipv4_addresses.id"],
            name="fk_ip_assignments_ipv4",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ipv6_address_id"],
            ["mod_ipam.ipv6_addresses.tenant_id", "mod_ipam.ipv6_addresses.id"],
            name="fk_ip_assignments_ipv6",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_ip_assignments_tenant_id_id"),
        sa.CheckConstraint(
            "ip_version IN ('ipv4', 'ipv6')", name="ck_ip_assignments_version"
        ),
        sa.CheckConstraint(
            "(ip_version = 'ipv4' AND ipv4_address_id IS NOT NULL "
            "AND ipv6_address_id IS NULL) OR "
            "(ip_version = 'ipv6' AND ipv6_address_id IS NOT NULL "
            "AND ipv4_address_id IS NULL)",
            name="ck_ip_assignments_version_address",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_ip_assignments_ipv4_active",
        "ip_assignments",
        ["tenant_id", "ipv4_address_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_ip_assignments_ipv6_active",
        "ip_assignments",
        ["tenant_id", "ipv6_address_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_ip_assignments_primary_ipv4_active",
        "ip_assignments",
        ["tenant_id", "subscription_id"],
        unique=True,
        postgresql_where=sa.text(_PRIMARY_WHERE),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ip_assignments_subscription",
        "ip_assignments",
        ["tenant_id", "subscription_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ip_assignments_subscriber",
        "ip_assignments",
        ["tenant_id", "subscriber_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "subscriber_additional_routes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subscriber_id", sa.Uuid(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("cidr", sa.String(64), nullable=False),
        sa.Column("prefix_length", sa.Integer(), nullable=False),
        sa.Column("metric", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(64), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _tenant_fk("fk_subscriber_additional_routes_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_subscriber_additional_routes_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "subscriber_id",
            "cidr",
            name="uq_subscriber_additional_routes_subscriber_cidr",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_subscriber_additional_routes_subscriber",
        "subscriber_additional_routes",
        ["tenant_id", "subscriber_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "ipv6_delegated_prefixes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("prefix", sa.String(64), nullable=False),
        sa.Column("prefix_length", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="available"),
        sa.Column("subscriber_id", sa.Uuid(), nullable=True),
        sa.Column("subscription_id", sa.Uuid(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        _tenant_fk("fk_ipv6_delegated_prefixes_tenant"),
        _pool_fk("ipv6_pd"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_ipv6_delegated_prefixes_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "pool_id",
            "prefix",
            "prefix_length",
            name="uq_ipv6_pd_pool_prefix",
        ),
        sa.CheckConstraint(
            "state IN ('available', 'reserved', 'assigned')", name="ck_ipv6_pd_state"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ipv6_pd_state",
        "ipv6_delegated_prefixes",
        ["tenant_id", "pool_id", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "ip_pool_utilization_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("pool_id", sa.Uuid(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("reserved", sa.Integer(), nullable=False),
        sa.Column("available", sa.Integer(), nullable=False),
        sa.Column("percent", sa.Integer(), nullable=False),
        _tenant_fk("fk_ip_pool_utilization_snapshots_tenant"),
        _pool_fk("ip_pool_util_snap"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_ip_pool_utilization_snapshots_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_ip_pool_util_snap_pool_time",
        "ip_pool_utilization_snapshots",
        ["tenant_id", "pool_id", "captured_at"],
        schema=_SCHEMA,
    )

    # Utilization snapshots stay append-only evidence: no UPDATE grant, a
    # refuse-UPDATE trigger, and the pool FK is RESTRICT. DELETE stays granted
    # only for retention pruning.
    op.execute(
        "CREATE FUNCTION mod_ipam.refuse_snapshot_update() RETURNS trigger "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION "
        "'IPAM utilization snapshots are append-only' USING ERRCODE = '55000'; "
        "END; $$;"
    )
    op.execute(
        "CREATE TRIGGER ip_pool_utilization_snapshots_no_update BEFORE UPDATE "
        "ON mod_ipam.ip_pool_utilization_snapshots FOR EACH ROW "
        "EXECUTE FUNCTION mod_ipam.refuse_snapshot_update();"
    )
    for table in _NEW_TABLES:
        _enforce_rls(table)
        _grant(
            table,
            "SELECT, INSERT, DELETE"
            if table == "ip_pool_utilization_snapshots"
            else "SELECT, INSERT, UPDATE, DELETE",
        )


def downgrade() -> None:
    _refuse_if_extracted_rows()
    for table in reversed(_NEW_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_ipam.refuse_snapshot_update();")

    # Recreate the 0.1 template shape (DDL copied from ip_0001, unmodified).
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

    op.execute(
        "CREATE TRIGGER ipam_utilization_snapshots_append_only BEFORE UPDATE OR DELETE "
        "ON mod_ipam.utilization_snapshots FOR EACH ROW "
        "EXECUTE FUNCTION mod_ipam.refuse_evidence_mutation();"
    )
    for table in _TEMPLATE_CREATE_ORDER:
        op.execute(f"ALTER TABLE mod_ipam.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_ipam.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY ipam_{table}_tenant_isolation ON mod_ipam.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table == "utilization_snapshots"
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        _grant(table, privileges)
