"""Create the tenant-scoped positioning owner.

Revision ID: po_0001_positioning
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "po_0001_positioning"
down_revision = None
branch_labels = ("positioning",)

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_pos"


def _timestamps() -> tuple[sa.Column[Any], sa.Column[Any]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_pos;")
    op.execute("GRANT USAGE ON SCHEMA mod_pos TO app_user, platform_api, app_admin;")

    op.create_table(
        "tracked_units",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_tracked_units_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_tracked_units_tenant_id_id"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_tracked_units_tenant_active",
        "tracked_units",
        ["tenant_id", "is_active"],
        schema=_SCHEMA,
    )

    op.create_table(
        "source_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_unit_ref", sa.String(128), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_source_identities_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_source_identities_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source",
            "source_unit_ref",
            name="uq_source_identities_source_ref",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "source_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("tracked_unit_id", sa.Uuid(), nullable=False),
        sa.Column("source_identity_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("unassigned_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_source_assignments_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_identity_id"],
            ["mod_pos.source_identities.tenant_id", "mod_pos.source_identities.id"],
            ondelete="CASCADE",
            name="fk_source_assignments_source_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            ["mod_pos.tracked_units.tenant_id", "mod_pos.tracked_units.id"],
            ondelete="CASCADE",
            name="fk_source_assignments_tracked_unit",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_source_assignments_tenant_id_id"
        ),
        sa.CheckConstraint(
            "unassigned_at IS NULL OR unassigned_at > assigned_at",
            name="ck_source_assignments_time_order",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_source_assignments_tenant_source_ref",
        "source_assignments",
        ["tenant_id", "source_identity_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_source_assignments_tenant_unit",
        "source_assignments",
        ["tenant_id", "tracked_unit_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "collection_grants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("tracked_unit_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_collection_grants_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            ["mod_pos.tracked_units.tenant_id", "mod_pos.tracked_units.id"],
            ondelete="CASCADE",
            name="fk_collection_grants_tracked_unit",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_collection_grants_tenant_id_id"
        ),
        sa.CheckConstraint(
            "expires_at > granted_at", name="ck_collection_grants_expiry"
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="ck_collection_grants_revoke_time",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_collection_grants_tenant_unit_purpose",
        "collection_grants",
        ["tenant_id", "tracked_unit_id", "purpose"],
        schema=_SCHEMA,
    )

    op.create_table(
        "position_observations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("tracked_unit_id", sa.Uuid(), nullable=False),
        sa.Column("source_identity_id", sa.Uuid(), nullable=False),
        sa.Column("client_observation_id", sa.Uuid(), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_unit_ref", sa.String(128), nullable=False),
        sa.Column("context_ref", sa.String(128), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("accuracy_m", sa.Float(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_position_observations_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_identity_id"],
            ["mod_pos.source_identities.tenant_id", "mod_pos.source_identities.id"],
            ondelete="RESTRICT",
            name="fk_position_observations_source_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            ["mod_pos.tracked_units.tenant_id", "mod_pos.tracked_units.id"],
            ondelete="CASCADE",
            name="fk_position_observations_tracked_unit",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_position_observations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_identity_id",
            "client_observation_id",
            name="uq_position_observations_identity",
        ),
        sa.CheckConstraint(
            "latitude >= -90 AND latitude <= 90",
            name="ck_position_observations_latitude",
        ),
        sa.CheckConstraint(
            "longitude >= -180 AND longitude <= 180",
            name="ck_position_observations_longitude",
        ),
        sa.CheckConstraint("accuracy_m >= 0", name="ck_position_observations_accuracy"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_position_observations_tenant_unit_captured",
        "position_observations",
        ["tenant_id", "tracked_unit_id", "captured_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_position_observations_tenant_received",
        "position_observations",
        ["tenant_id", "received_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "current_positions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("tracked_unit_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=True),
        sa.Column("source_identity_id", sa.Uuid(), nullable=True),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("source_unit_ref", sa.String(128), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("accuracy_m", sa.Float(), nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_current_positions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_identity_id"],
            ["mod_pos.source_identities.tenant_id", "mod_pos.source_identities.id"],
            ondelete="RESTRICT",
            name="fk_current_positions_source_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            ["mod_pos.tracked_units.tenant_id", "mod_pos.tracked_units.id"],
            ondelete="CASCADE",
            name="fk_current_positions_tracked_unit",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                "mod_pos.position_observations.tenant_id",
                "mod_pos.position_observations.id",
            ],
            ondelete="RESTRICT",
            name="fk_current_positions_observation",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_current_positions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "tracked_unit_id",
            name="uq_current_positions_tenant_unit",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_current_positions_tenant_captured",
        "current_positions",
        ["tenant_id", "captured_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "geofences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("shape_kind", sa.String(16), nullable=False),
        sa.Column("center_latitude", sa.Float(), nullable=True),
        sa.Column("center_longitude", sa.Float(), nullable=True),
        sa.Column("radius_m", sa.Float(), nullable=True),
        sa.Column("polygon_points", sa.JSON(none_as_null=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_geofences_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_geofences_tenant_id_id"),
        sa.CheckConstraint(
            "shape_kind IN ('circle', 'polygon')",
            name="ck_geofences_shape_kind",
        ),
        sa.CheckConstraint(
            "(shape_kind = 'circle' AND center_latitude IS NOT NULL "
            "AND center_longitude IS NOT NULL AND radius_m > 0 "
            "AND polygon_points IS NULL) OR "
            "(shape_kind = 'polygon' AND center_latitude IS NULL "
            "AND center_longitude IS NULL AND radius_m IS NULL "
            "AND polygon_points IS NOT NULL)",
            name="ck_geofences_shape_payload",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_geofences_tenant_active",
        "geofences",
        ["tenant_id", "is_active"],
        schema=_SCHEMA,
    )

    op.create_table(
        "geofence_states",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("tracked_unit_id", sa.Uuid(), nullable=False),
        sa.Column("geofence_id", sa.Uuid(), nullable=False),
        sa.Column("is_inside", sa.Boolean(), nullable=False),
        sa.Column("last_observation_id", sa.Uuid(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_geofence_states_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            ["mod_pos.tracked_units.tenant_id", "mod_pos.tracked_units.id"],
            ondelete="CASCADE",
            name="fk_geofence_states_tracked_unit",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "geofence_id"],
            ["mod_pos.geofences.tenant_id", "mod_pos.geofences.id"],
            ondelete="CASCADE",
            name="fk_geofence_states_geofence",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_geofence_states_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "tracked_unit_id",
            "geofence_id",
            name="uq_geofence_states_tenant_unit_fence",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_geofence_states_tenant_unit",
        "geofence_states",
        ["tenant_id", "tracked_unit_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "geofence_facts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("tracked_unit_id", sa.Uuid(), nullable=False),
        sa.Column("geofence_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("transition", sa.String(16), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_geofence_facts_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tracked_unit_id"],
            ["mod_pos.tracked_units.tenant_id", "mod_pos.tracked_units.id"],
            ondelete="CASCADE",
            name="fk_geofence_facts_tracked_unit",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "geofence_id"],
            ["mod_pos.geofences.tenant_id", "mod_pos.geofences.id"],
            ondelete="CASCADE",
            name="fk_geofence_facts_geofence",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                "mod_pos.position_observations.tenant_id",
                "mod_pos.position_observations.id",
            ],
            ondelete="CASCADE",
            name="fk_geofence_facts_observation",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_geofence_facts_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "tracked_unit_id",
            "geofence_id",
            "observation_id",
            "transition",
            name="uq_geofence_facts_observation_transition",
        ),
        sa.CheckConstraint(
            "transition IN ('entry', 'exit')",
            name="ck_geofence_facts_transition",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_geofence_facts_tenant_unit_occurred",
        "geofence_facts",
        ["tenant_id", "tracked_unit_id", "occurred_at"],
        schema=_SCHEMA,
    )

    op.execute("ALTER TABLE mod_pos.tracked_units ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.tracked_units FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY tracked_units_tenant_isolation ON mod_pos.tracked_units "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_pos.tracked_units TO app_user;"
    )

    op.execute("ALTER TABLE mod_pos.source_identities ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.source_identities FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY source_identities_tenant_isolation "
        "ON mod_pos.source_identities "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_pos.source_identities TO app_user;"
    )

    op.execute("ALTER TABLE mod_pos.source_assignments ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.source_assignments FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY source_assignments_tenant_isolation "
        "ON mod_pos.source_assignments "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_pos.source_assignments TO app_user;"
    )

    op.execute("ALTER TABLE mod_pos.collection_grants ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.collection_grants FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY collection_grants_tenant_isolation "
        "ON mod_pos.collection_grants "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_pos.collection_grants TO app_user;"
    )

    op.execute("ALTER TABLE mod_pos.position_observations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.position_observations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY position_observations_tenant_isolation "
        "ON mod_pos.position_observations "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_pos.position_observations TO app_user;"
    )

    op.execute("ALTER TABLE mod_pos.current_positions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.current_positions FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY current_positions_tenant_isolation "
        "ON mod_pos.current_positions "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_pos.current_positions TO app_user;"
    )

    op.execute("ALTER TABLE mod_pos.geofences ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.geofences FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY geofences_tenant_isolation ON mod_pos.geofences "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON mod_pos.geofences TO app_user;")

    op.execute("ALTER TABLE mod_pos.geofence_states ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.geofence_states FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY geofence_states_tenant_isolation "
        "ON mod_pos.geofence_states "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON mod_pos.geofence_states TO app_user;"
    )

    op.execute("ALTER TABLE mod_pos.geofence_facts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_pos.geofence_facts FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY geofence_facts_tenant_isolation "
        "ON mod_pos.geofence_facts "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE " "ON mod_pos.geofence_facts TO app_user;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_pos.geofence_facts CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_pos.geofence_states CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_pos.geofences CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_pos.current_positions CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_pos.position_observations CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_pos.collection_grants CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_pos.source_assignments CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_pos.source_identities CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_pos.tracked_units CASCADE;")
