"""Create the platform health observation and projection owner.

Revision ID: ph_0001_platform_health
Revises: (lineage root)
Create Date: 2026-08-21
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ph_0001_platform_health"
down_revision = None
branch_labels = ("platform_health",)
REQUIRES = ("module_database_roles.v1",)
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_health"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_health;")
    op.execute("GRANT USAGE ON SCHEMA mod_health TO platform_api, app_admin;")
    op.create_table(
        "health_components",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("freshness_seconds", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
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
        sa.UniqueConstraint("code", name="uq_health_components_code"),
        sa.CheckConstraint(
            "freshness_seconds > 0", name="ck_health_components_freshness"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "health_observations",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_ref", sa.String(200), nullable=False),
        sa.Column("observation_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("labels", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["component_id"],
            ["mod_health.health_components.id"],
            name="fk_health_observations_component",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "source_ref", "observation_key", name="uq_health_observations_source_key"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "health_projections",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("component_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("freshness_deadline", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["component_id"],
            ["mod_health.health_components.id"],
            name="fk_health_projections_component",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["observation_id"],
            ["mod_health.health_observations.id"],
            name="fk_health_projections_observation",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("component_id", name="uq_health_projections_component"),
        schema=_SCHEMA,
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_health.health_components TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_health.health_observations TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_health.health_projections TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA mod_health TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_health.health_components FROM app_user;")
    op.execute("REVOKE ALL ON mod_health.health_observations FROM app_user;")
    op.execute("REVOKE ALL ON mod_health.health_projections FROM app_user;")


def downgrade() -> None:
    op.drop_table("health_projections", schema=_SCHEMA)
    op.drop_table("health_observations", schema=_SCHEMA)
    op.drop_table("health_components", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_health;")
