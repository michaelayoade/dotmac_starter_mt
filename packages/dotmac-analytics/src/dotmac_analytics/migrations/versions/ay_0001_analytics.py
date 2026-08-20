"""Create declared analytical evidence and projections (ADR-0043).

Revision ID: ay_0001_analytics
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ay_0001_analytics"
down_revision = None
branch_labels = ("analytics",)
REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_analytics"
_CATALOG = "metric_catalog_entries"
_RECEIPTS = "metric_ingest_receipts"
_OBSERVATIONS = "metric_observations"
_POINTS = "metric_points"
_REBUILDS = "metric_projection_rebuilds"
_IMMUTABLE_TABLES = (_CATALOG, _RECEIPTS, _OBSERVATIONS, _REBUILDS)


def _id_and_tenant(name: str) -> list[sa.Column[Any] | sa.Constraint]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id"),
    ]


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_analytics;")
    op.execute("GRANT USAGE ON SCHEMA mod_analytics TO app_user, app_admin;")

    op.create_table(
        _CATALOG,
        *_id_and_tenant("metric_catalog"),
        sa.Column("metric_code", sa.String(120), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("owner_code", sa.String(120), nullable=False),
        sa.Column("declaration_fingerprint", sa.String(71), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("value_kind", sa.String(16), nullable=False),
        sa.Column("unit_code", sa.String(120), nullable=False),
        sa.Column("granularities_json", postgresql.JSONB(), nullable=False),
        sa.Column("dimensions_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "metric_code",
            "schema_version",
            name="uq_metric_catalog_identity",
        ),
        sa.CheckConstraint("schema_version >= 1", name="ck_metric_catalog_version"),
        sa.CheckConstraint(
            "value_kind IN ('count', 'number', 'ratio', 'money')",
            name="ck_metric_catalog_value_kind",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_metric_catalog_owner",
        _CATALOG,
        ["tenant_id", "owner_code"],
        schema=_SCHEMA,
    )

    op.create_table(
        _RECEIPTS,
        *_id_and_tenant("metric_receipts"),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(255), nullable=False),
        sa.Column("source_schema_version", sa.Integer(), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("adapter_code", sa.String(120), nullable=False),
        sa.Column("delivery_id", sa.String(255), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_metric_receipts_source_event",
        ),
        sa.CheckConstraint(
            "source_schema_version >= 1", name="ck_metric_receipts_source_version"
        ),
        sa.CheckConstraint(
            "point_count BETWEEN 1 AND 250", name="ck_metric_receipts_point_count"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_metric_receipts_received",
        _RECEIPTS,
        ["tenant_id", "received_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        _OBSERVATIONS,
        *_id_and_tenant("metric_observations"),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("metric_code", sa.String(120), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granularity", sa.String(16), nullable=False),
        sa.Column("selector_digest", sa.String(71), nullable=False),
        sa.Column("dimensions_json", postgresql.JSONB(), nullable=False),
        sa.Column("value_numeric", sa.Numeric(38, 12), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "receipt_id"],
            [
                "mod_analytics.metric_ingest_receipts.tenant_id",
                "mod_analytics.metric_ingest_receipts.id",
            ],
            ondelete="RESTRICT",
            name="fk_metric_observations_receipt",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metric_code", "schema_version"],
            [
                "mod_analytics.metric_catalog_entries.tenant_id",
                "mod_analytics.metric_catalog_entries.metric_code",
                "mod_analytics.metric_catalog_entries.schema_version",
            ],
            ondelete="RESTRICT",
            name="fk_metric_observations_declaration",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "receipt_id",
            "metric_code",
            "schema_version",
            "period_start",
            "period_end",
            "granularity",
            "selector_digest",
            name="uq_metric_observations_receipt_coordinate",
        ),
        sa.CheckConstraint(
            "schema_version >= 1", name="ck_metric_observations_version"
        ),
        sa.CheckConstraint(
            "period_end > period_start", name="ck_metric_observations_period"
        ),
        sa.CheckConstraint(
            "granularity IN ('hour', 'day', 'week', 'month', 'quarter', 'year')",
            name="ck_metric_observations_granularity",
        ),
        sa.CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_metric_observations_currency",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_metric_observations_series",
        _OBSERVATIONS,
        ["tenant_id", "metric_code", "schema_version", "period_start"],
        schema=_SCHEMA,
    )

    op.create_table(
        _POINTS,
        *_id_and_tenant("metric_points"),
        sa.Column("metric_code", sa.String(120), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("granularity", sa.String(16), nullable=False),
        sa.Column("selector_digest", sa.String(71), nullable=False),
        sa.Column("dimensions_json", postgresql.JSONB(), nullable=False),
        sa.Column("value_numeric", sa.Numeric(38, 12), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=True),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metric_code", "schema_version"],
            [
                "mod_analytics.metric_catalog_entries.tenant_id",
                "mod_analytics.metric_catalog_entries.metric_code",
                "mod_analytics.metric_catalog_entries.schema_version",
            ],
            ondelete="RESTRICT",
            name="fk_metric_points_declaration",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                "mod_analytics.metric_observations.tenant_id",
                "mod_analytics.metric_observations.id",
            ],
            ondelete="RESTRICT",
            name="fk_metric_points_observation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "metric_code",
            "schema_version",
            "period_start",
            "period_end",
            "granularity",
            "selector_digest",
            name="uq_metric_points_coordinate",
        ),
        sa.CheckConstraint("schema_version >= 1", name="ck_metric_points_version"),
        sa.CheckConstraint("period_end > period_start", name="ck_metric_points_period"),
        sa.CheckConstraint(
            "granularity IN ('hour', 'day', 'week', 'month', 'quarter', 'year')",
            name="ck_metric_points_granularity",
        ),
        sa.CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_metric_points_currency",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_metric_points_latest",
        _POINTS,
        [
            "tenant_id",
            "metric_code",
            "schema_version",
            "granularity",
            "period_start",
        ],
        schema=_SCHEMA,
    )

    op.create_table(
        _REBUILDS,
        *_id_and_tenant("metric_rebuilds"),
        sa.Column("before_digest", sa.String(71), nullable=False),
        sa.Column("after_digest", sa.String(71), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("rebuilt_by", sa.String(255), nullable=False),
        sa.Column("rebuilt_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("point_count >= 0", name="ck_metric_rebuilds_point_count"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_metric_rebuilds_time",
        _REBUILDS,
        ["tenant_id", "rebuilt_at"],
        schema=_SCHEMA,
    )

    # Written out so the composed gate can see every table/policy literally.
    op.execute(
        "ALTER TABLE mod_analytics.metric_catalog_entries ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_analytics.metric_catalog_entries FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY metric_catalog_entries_tenant_isolation
            ON mod_analytics.metric_catalog_entries
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "ALTER TABLE mod_analytics.metric_ingest_receipts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_analytics.metric_ingest_receipts FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY metric_ingest_receipts_tenant_isolation
            ON mod_analytics.metric_ingest_receipts
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "ALTER TABLE mod_analytics.metric_observations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_analytics.metric_observations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY metric_observations_tenant_isolation
            ON mod_analytics.metric_observations
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute("ALTER TABLE mod_analytics.metric_points ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_analytics.metric_points FORCE ROW LEVEL SECURITY;")
    op.execute(
        """
        CREATE POLICY metric_points_tenant_isolation
            ON mod_analytics.metric_points
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    op.execute(
        "ALTER TABLE mod_analytics.metric_projection_rebuilds ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_analytics.metric_projection_rebuilds FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        """
        CREATE POLICY metric_projection_rebuilds_tenant_isolation
            ON mod_analytics.metric_projection_rebuilds
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )

    op.execute(
        """
        CREATE FUNCTION mod_analytics.refuse_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'analytics evidence is append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_append_only "
            f"BEFORE UPDATE OR DELETE ON mod_analytics.{table} "
            "FOR EACH ROW EXECUTE FUNCTION mod_analytics.refuse_mutation();"
        )
        op.execute(f"GRANT SELECT, INSERT ON mod_analytics.{table} TO app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_analytics.metric_points "
        "TO app_user;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_analytics.metric_projection_rebuilds CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_analytics.metric_points CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_analytics.metric_observations CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_analytics.metric_ingest_receipts CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_analytics.metric_catalog_entries CASCADE;")
    op.execute("DROP FUNCTION IF EXISTS mod_analytics.refuse_mutation();")
