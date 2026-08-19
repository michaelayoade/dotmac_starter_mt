"""Create Network Topology.

Revision ID: nt_0001_network_topology
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "nt_0001_network_topology"
down_revision = None
branch_labels = ("network_topology",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_nettop"
_TABLES = (
    "links",
    "path_projections",
    "reachability_projections",
    "coverage_gaps",
    "topology_events",
)


def _tenant(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], name=name, ondelete="CASCADE"
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_nettop;")
    op.execute("GRANT USAGE ON SCHEMA mod_nettop TO app_user, platform_api;")
    op.create_table(
        "links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("left_ref", sa.String(200), nullable=False),
        sa.Column("right_ref", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("direction", sa.String(24), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("fingerprint", sa.String(128)),
        sa.Column("observed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True)),
        _tenant("fk_nettop_links_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_nettop_links_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "left_ref",
            "right_ref",
            "kind",
            "source_ref",
            name="uq_nettop_link_identity",
        ),
        sa.CheckConstraint("cost >= 0", name="ck_nettop_link_cost"),
        sa.CheckConstraint(
            "direction IN ('directed', 'bidirectional')",
            name="ck_nettop_link_direction",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_nettop_links_endpoints",
        "links",
        ["tenant_id", "left_ref", "right_ref", "state"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_nettop_observed_fingerprint",
        "links",
        ["tenant_id", "source_ref", "fingerprint"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("fingerprint IS NOT NULL"),
    )
    op.create_table(
        "path_projections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_ref", sa.String(200), nullable=False),
        sa.Column("destination_ref", sa.String(200), nullable=False),
        sa.Column("hop_refs", sa.JSON(), nullable=False),
        sa.Column("link_ids", sa.JSON(), nullable=False),
        sa.Column("total_cost", sa.Integer(), nullable=False),
        sa.Column("reachable", sa.Boolean(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rebuilt_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_nettop_paths_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_nettop_paths_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "source_ref", "destination_ref", name="uq_nettop_path_identity"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "reachability_projections",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_ref", sa.String(200), nullable=False),
        sa.Column("from_ref", sa.String(200), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("path_id", sa.Uuid()),
        sa.Column("reason_code", sa.String(120)),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_nettop_reachability_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "path_id"],
            ["mod_nettop.path_projections.tenant_id", "mod_nettop.path_projections.id"],
            name="fk_nettop_reachability_path",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_nettop_reachability_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "subject_ref",
            "from_ref",
            name="uq_nettop_reachability_identity",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "coverage_gaps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("scope_ref", sa.String(200), nullable=False),
        sa.Column("missing_ref", sa.String(200), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_nettop_gaps_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_nettop_gaps_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "scope_ref", "missing_ref", name="uq_nettop_gap_identity"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "topology_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("aggregate_ref", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant("fk_nettop_events_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_nettop_events_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_nettop_events_aggregate",
        "topology_events",
        ["tenant_id", "aggregate_ref", "occurred_at"],
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE FUNCTION mod_nettop.refuse_evidence_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Topology evidence is append-only' USING ERRCODE = '55000'; END; $$;"
    )
    op.execute(
        "CREATE TRIGGER nettop_topology_events_append_only BEFORE UPDATE OR DELETE ON mod_nettop.topology_events FOR EACH ROW EXECUTE FUNCTION mod_nettop.refuse_evidence_mutation();"
    )
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_nettop.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_nettop.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY nettop_{table}_tenant_isolation ON mod_nettop.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        privileges = (
            "SELECT, INSERT"
            if table == "topology_events"
            else "SELECT, INSERT, UPDATE, DELETE"
        )
        op.execute(f"GRANT {privileges} ON mod_nettop.{table} TO app_user;")
        op.execute(f"GRANT {privileges} ON mod_nettop.{table} TO platform_api;")


def downgrade() -> None:
    op.drop_index(
        "ix_nettop_events_aggregate", table_name="topology_events", schema=_SCHEMA
    )
    op.drop_table("topology_events", schema=_SCHEMA)
    op.drop_table("coverage_gaps", schema=_SCHEMA)
    op.drop_table("reachability_projections", schema=_SCHEMA)
    op.drop_table("path_projections", schema=_SCHEMA)
    op.drop_index("uq_nettop_observed_fingerprint", table_name="links", schema=_SCHEMA)
    op.drop_index("ix_nettop_links_endpoints", table_name="links", schema=_SCHEMA)
    op.drop_table("links", schema=_SCHEMA)
    op.execute("DROP FUNCTION mod_nettop.refuse_evidence_mutation();")
    op.execute("DROP SCHEMA IF EXISTS mod_nettop RESTRICT;")
