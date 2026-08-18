"""Create immutable tenant media observations and rebuildable projections.

Revision ID: mo_0001_media_observations
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

revision = "mo_0001_media_observations"
down_revision = None
branch_labels = ("media_observations",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_mediaobs"
_APPEND_ONLY = (
    "node_definitions",
    "metric_definitions",
    "observations",
    "observation_receipts",
    "entity_observations",
    "hierarchy_observations",
    "metric_periods",
    "metric_observations",
    "reconciliation_evidence",
)
_PROJECTIONS = ("current_entities", "current_hierarchy", "current_metrics")


def _id_and_tenant(unique_name: str) -> list[Any]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{unique_name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{unique_name}_tenant_id"),
    ]


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_mediaobs;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_mediaobs TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        "node_definitions",
        *_id_and_tenant("media_node_defs"),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("traits", postgresql.JSONB(), nullable=False),
        sa.Column("definition_fingerprint", sa.String(64), nullable=False),
        sa.Column("declared_by", sa.String(255), nullable=False),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "tenant_id", "code", "version", name="uq_media_node_defs_identity"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_node_defs_tenant_code",
        "node_definitions",
        ["tenant_id", "code"],
        schema=_SCHEMA,
    )

    op.create_table(
        "metric_definitions",
        *_id_and_tenant("media_metric_defs"),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(80), nullable=False),
        sa.Column("semantic", sa.String(40), nullable=False),
        sa.Column(
            "observation_origin",
            sa.String(24),
            nullable=False,
            server_default="provider_reported",
        ),
        sa.Column("definition_fingerprint", sa.String(64), nullable=False),
        sa.Column("declared_by", sa.String(255), nullable=False),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "tenant_id", "code", "version", name="uq_media_metric_defs_identity"
        ),
        sa.CheckConstraint(
            "value_type IN ('count','decimal','money','duration','ratio')",
            name="ck_media_metric_defs_value_type",
        ),
        sa.CheckConstraint(
            "observation_origin = 'provider_reported'",
            name="ck_media_metric_defs_provider_origin",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_metric_defs_tenant_code",
        "metric_definitions",
        ["tenant_id", "code"],
        schema=_SCHEMA,
    )

    op.create_table(
        "observations",
        *_id_and_tenant("media_observations"),
        sa.Column("installation_ref", sa.String(255), nullable=False),
        sa.Column("source_system", sa.String(255), nullable=False),
        sa.Column("source_observation_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalization_version", sa.Integer(), nullable=False),
        sa.Column("restates_observation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "restatement_depth", sa.Integer(), nullable=False, server_default="0"
        ),
        _created_at(),
        sa.UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "source_system",
            "source_observation_id",
            name="uq_media_observations_source_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "restates_observation_id"],
            ["mod_mediaobs.observations.tenant_id", "mod_mediaobs.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_observations_restates",
        ),
        sa.CheckConstraint(
            "kind IN ('entity','hierarchy','metric')",
            name="ck_media_observations_kind",
        ),
        sa.CheckConstraint(
            "normalization_version >= 1", name="ck_media_observations_normalization"
        ),
        sa.CheckConstraint(
            "restatement_depth >= 0", name="ck_media_observations_restatement_depth"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_observations_tenant_source_time",
        "observations",
        ["tenant_id", "source_observed_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "observation_receipts",
        *_id_and_tenant("media_receipts"),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("installation_ref", sa.String(255), nullable=False),
        sa.Column("transport_receipt_ref", sa.String(255), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "transport_receipt_ref",
            name="uq_media_receipts_transport_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["mod_mediaobs.observations.tenant_id", "mod_mediaobs.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_receipts_observation",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_receipts_tenant_observation",
        "observation_receipts",
        ["tenant_id", "observation_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "entity_observations",
        *_id_and_tenant("media_entity_facts"),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("external_account_ref", sa.String(255), nullable=False),
        sa.Column("entity_ref", sa.String(255), nullable=False),
        sa.Column("node_code", sa.String(80), nullable=False),
        sa.Column("node_version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(500), nullable=True),
        sa.Column("state", sa.String(120), nullable=False),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("properties", postgresql.JSONB(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "tenant_id", "observation_id", name="uq_media_entity_facts_observation"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["mod_mediaobs.observations.tenant_id", "mod_mediaobs.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_entity_facts_observation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "node_code", "node_version"],
            [
                "mod_mediaobs.node_definitions.tenant_id",
                "mod_mediaobs.node_definitions.code",
                "mod_mediaobs.node_definitions.version",
            ],
            ondelete="RESTRICT",
            name="fk_media_entity_facts_node_definition",
        ),
        sa.CheckConstraint(
            "disposition IN ('present','archived','deleted')",
            name="ck_media_entity_facts_disposition",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_entity_facts_identity",
        "entity_observations",
        ["tenant_id", "external_account_ref", "entity_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "hierarchy_observations",
        *_id_and_tenant("media_hierarchy_facts"),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("external_account_ref", sa.String(255), nullable=False),
        sa.Column("child_entity_ref", sa.String(255), nullable=False),
        sa.Column("parent_entity_ref", sa.String(255), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "tenant_id",
            "observation_id",
            name="uq_media_hierarchy_facts_observation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["mod_mediaobs.observations.tenant_id", "mod_mediaobs.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_hierarchy_facts_observation",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_hierarchy_facts_child",
        "hierarchy_observations",
        ["tenant_id", "external_account_ref", "child_entity_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "metric_periods",
        *_id_and_tenant("media_periods"),
        sa.Column("installation_ref", sa.String(255), nullable=False),
        sa.Column("source_system", sa.String(255), nullable=False),
        sa.Column("external_account_ref", sa.String(255), nullable=False),
        sa.Column("entity_ref", sa.String(255), nullable=False),
        sa.Column("metric_code", sa.String(80), nullable=False),
        sa.Column("metric_version", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "source_system",
            "external_account_ref",
            "entity_ref",
            "metric_code",
            "metric_version",
            "period_start",
            "period_end",
            name="uq_media_periods_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metric_code", "metric_version"],
            [
                "mod_mediaobs.metric_definitions.tenant_id",
                "mod_mediaobs.metric_definitions.code",
                "mod_mediaobs.metric_definitions.version",
            ],
            ondelete="RESTRICT",
            name="fk_media_periods_metric_definition",
        ),
        sa.CheckConstraint("period_start < period_end", name="ck_media_periods_order"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_periods_entity",
        "metric_periods",
        ["tenant_id", "external_account_ref", "entity_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "metric_observations",
        *_id_and_tenant("media_metric_facts"),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("count_value", sa.BigInteger(), nullable=True),
        sa.Column("decimal_value", sa.Numeric(38, 18), nullable=True),
        sa.Column("money_amount", sa.Numeric(38, 18), nullable=True),
        sa.Column("money_minor_units", sa.BigInteger(), nullable=True),
        sa.Column("money_currency", sa.String(3), nullable=True),
        sa.Column("money_minor_unit", sa.SmallInteger(), nullable=True),
        sa.Column("duration_value", sa.BigInteger(), nullable=True),
        sa.Column("ratio_value", sa.Numeric(38, 18), nullable=True),
        sa.Column(
            "claim_status",
            sa.String(24),
            nullable=False,
            server_default="provider_reported",
        ),
        _created_at(),
        sa.UniqueConstraint(
            "tenant_id", "observation_id", name="uq_media_metric_facts_observation"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["mod_mediaobs.observations.tenant_id", "mod_mediaobs.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_metric_facts_observation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "period_id"],
            ["mod_mediaobs.metric_periods.tenant_id", "mod_mediaobs.metric_periods.id"],
            ondelete="RESTRICT",
            name="fk_media_metric_facts_period",
        ),
        sa.CheckConstraint(
            "value_type IN ('count','decimal','money','duration','ratio')",
            name="ck_media_metric_facts_value_type",
        ),
        sa.CheckConstraint(
            "claim_status = 'provider_reported'",
            name="ck_media_metric_facts_claim_status",
        ),
        sa.CheckConstraint(
            "(value_type='count' AND count_value IS NOT NULL AND decimal_value IS NULL AND money_amount IS NULL AND money_minor_units IS NULL AND money_currency IS NULL AND money_minor_unit IS NULL AND duration_value IS NULL AND ratio_value IS NULL) OR "
            "(value_type='decimal' AND count_value IS NULL AND decimal_value IS NOT NULL AND money_amount IS NULL AND money_minor_units IS NULL AND money_currency IS NULL AND money_minor_unit IS NULL AND duration_value IS NULL AND ratio_value IS NULL) OR "
            "(value_type='money' AND count_value IS NULL AND decimal_value IS NULL AND money_amount IS NOT NULL AND money_minor_units IS NOT NULL AND money_currency IS NOT NULL AND money_minor_unit IS NOT NULL AND duration_value IS NULL AND ratio_value IS NULL) OR "
            "(value_type='duration' AND count_value IS NULL AND decimal_value IS NULL AND money_amount IS NULL AND money_minor_units IS NULL AND money_currency IS NULL AND money_minor_unit IS NULL AND duration_value IS NOT NULL AND ratio_value IS NULL) OR "
            "(value_type='ratio' AND count_value IS NULL AND decimal_value IS NULL AND money_amount IS NULL AND money_minor_units IS NULL AND money_currency IS NULL AND money_minor_unit IS NULL AND duration_value IS NULL AND ratio_value IS NOT NULL)",
            name="ck_media_metric_facts_typed_value",
        ),
        sa.CheckConstraint(
            "count_value IS NULL OR count_value >= 0",
            name="ck_media_metric_facts_count_nonnegative",
        ),
        sa.CheckConstraint(
            "money_minor_unit IS NULL OR money_minor_unit BETWEEN 0 AND 9",
            name="ck_media_metric_facts_minor_unit",
        ),
        sa.CheckConstraint(
            "money_currency IS NULL OR "
            "(length(money_currency) = 3 AND money_currency = upper(money_currency))",
            name="ck_media_metric_facts_currency",
        ),
        sa.CheckConstraint(
            "(value_type!='money') OR (money_amount * CASE money_minor_unit WHEN 0 THEN 1 WHEN 1 THEN 10 WHEN 2 THEN 100 WHEN 3 THEN 1000 WHEN 4 THEN 10000 WHEN 5 THEN 100000 WHEN 6 THEN 1000000 WHEN 7 THEN 10000000 WHEN 8 THEN 100000000 WHEN 9 THEN 1000000000 END = money_minor_units)",
            name="ck_media_metric_facts_exact_money",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_media_metric_facts_period",
        "metric_observations",
        ["tenant_id", "period_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "current_entities",
        *_id_and_tenant("media_current_entities"),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("installation_ref", sa.String(255), nullable=False),
        sa.Column("source_system", sa.String(255), nullable=False),
        sa.Column("external_account_ref", sa.String(255), nullable=False),
        sa.Column("entity_ref", sa.String(255), nullable=False),
        sa.Column("node_code", sa.String(80), nullable=False),
        sa.Column("node_version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(500), nullable=True),
        sa.Column("state", sa.String(120), nullable=False),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("properties", postgresql.JSONB(), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "source_system",
            "external_account_ref",
            "entity_ref",
            name="uq_media_current_entities_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["mod_mediaobs.observations.tenant_id", "mod_mediaobs.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_current_entities_observation",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "current_hierarchy",
        *_id_and_tenant("media_current_hierarchy"),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("installation_ref", sa.String(255), nullable=False),
        sa.Column("source_system", sa.String(255), nullable=False),
        sa.Column("external_account_ref", sa.String(255), nullable=False),
        sa.Column("child_entity_ref", sa.String(255), nullable=False),
        sa.Column("parent_entity_ref", sa.String(255), nullable=False),
        sa.Column("drift_code", sa.String(40), nullable=True),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "installation_ref",
            "source_system",
            "external_account_ref",
            "child_entity_ref",
            name="uq_media_current_hierarchy_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["mod_mediaobs.observations.tenant_id", "mod_mediaobs.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_current_hierarchy_observation",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "current_metrics",
        *_id_and_tenant("media_current_metrics"),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("projection_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id", "period_id", name="uq_media_current_metrics_period"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            ["mod_mediaobs.observations.tenant_id", "mod_mediaobs.observations.id"],
            ondelete="RESTRICT",
            name="fk_media_current_metrics_observation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "period_id"],
            ["mod_mediaobs.metric_periods.tenant_id", "mod_mediaobs.metric_periods.id"],
            ondelete="RESTRICT",
            name="fk_media_current_metrics_period",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "reconciliation_evidence",
        *_id_and_tenant("media_reconcile"),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("drift_count", sa.Integer(), nullable=False),
        sa.Column("before_digest", sa.String(64), nullable=False),
        sa.Column("expected_digest", sa.String(64), nullable=False),
        sa.Column("applied", sa.Boolean(), nullable=False),
        sa.Column(
            "reconciled_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_mediaobs.refuse_append_only_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'mod_mediaobs.% is append-only; % is refused',
                TG_TABLE_NAME, TG_OP USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_mediaobs.refuse_metric_period_overlap()
        RETURNS trigger AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    concat_ws(E'\\x1f', NEW.tenant_id::text, NEW.installation_ref,
                        NEW.source_system, NEW.external_account_ref, NEW.entity_ref,
                        NEW.metric_code, NEW.metric_version::text), 0
                )
            );
            IF EXISTS (
                SELECT 1 FROM mod_mediaobs.metric_periods p
                WHERE p.tenant_id = NEW.tenant_id
                  AND p.installation_ref = NEW.installation_ref
                  AND p.source_system = NEW.source_system
                  AND p.external_account_ref = NEW.external_account_ref
                  AND p.entity_ref = NEW.entity_ref
                  AND p.metric_code = NEW.metric_code
                  AND p.metric_version = NEW.metric_version
                  AND p.period_start < NEW.period_end
                  AND p.period_end > NEW.period_start
                  AND (p.period_start, p.period_end)
                      IS DISTINCT FROM (NEW.period_start, NEW.period_end)
            ) THEN
                RAISE EXCEPTION 'metric periods use non-overlapping [start,end) semantics'
                    USING ERRCODE = 'exclusion_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER node_definitions_append_only BEFORE UPDATE OR DELETE ON mod_mediaobs.node_definitions FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_append_only_mutation();
        CREATE TRIGGER metric_definitions_append_only BEFORE UPDATE OR DELETE ON mod_mediaobs.metric_definitions FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_append_only_mutation();
        CREATE TRIGGER observations_append_only BEFORE UPDATE OR DELETE ON mod_mediaobs.observations FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_append_only_mutation();
        CREATE TRIGGER observation_receipts_append_only BEFORE UPDATE OR DELETE ON mod_mediaobs.observation_receipts FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_append_only_mutation();
        CREATE TRIGGER entity_observations_append_only BEFORE UPDATE OR DELETE ON mod_mediaobs.entity_observations FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_append_only_mutation();
        CREATE TRIGGER hierarchy_observations_append_only BEFORE UPDATE OR DELETE ON mod_mediaobs.hierarchy_observations FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_append_only_mutation();
        CREATE TRIGGER metric_periods_append_only BEFORE UPDATE OR DELETE ON mod_mediaobs.metric_periods FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_append_only_mutation();
        CREATE TRIGGER metric_observations_append_only BEFORE UPDATE OR DELETE ON mod_mediaobs.metric_observations FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_append_only_mutation();
        CREATE TRIGGER reconciliation_evidence_append_only BEFORE UPDATE OR DELETE ON mod_mediaobs.reconciliation_evidence FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_append_only_mutation();
        CREATE TRIGGER metric_periods_no_overlap BEFORE INSERT ON mod_mediaobs.metric_periods FOR EACH ROW EXECUTE FUNCTION mod_mediaobs.refuse_metric_period_overlap();
        """
    )

    _install_tenant_security()


def _install_tenant_security() -> None:
    op.execute(
        """
        ALTER TABLE mod_mediaobs.node_definitions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.node_definitions FORCE ROW LEVEL SECURITY;
        CREATE POLICY node_definitions_tenant_isolation ON mod_mediaobs.node_definitions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_mediaobs.node_definitions TO app_user;
        GRANT SELECT, INSERT ON mod_mediaobs.node_definitions TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.metric_definitions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.metric_definitions FORCE ROW LEVEL SECURITY;
        CREATE POLICY metric_definitions_tenant_isolation ON mod_mediaobs.metric_definitions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_mediaobs.metric_definitions TO app_user;
        GRANT SELECT, INSERT ON mod_mediaobs.metric_definitions TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.observations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.observations FORCE ROW LEVEL SECURITY;
        CREATE POLICY observations_tenant_isolation ON mod_mediaobs.observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_mediaobs.observations TO app_user;
        GRANT SELECT, INSERT ON mod_mediaobs.observations TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.observation_receipts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.observation_receipts FORCE ROW LEVEL SECURITY;
        CREATE POLICY observation_receipts_tenant_isolation ON mod_mediaobs.observation_receipts USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_mediaobs.observation_receipts TO app_user;
        GRANT SELECT, INSERT ON mod_mediaobs.observation_receipts TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.entity_observations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.entity_observations FORCE ROW LEVEL SECURITY;
        CREATE POLICY entity_observations_tenant_isolation ON mod_mediaobs.entity_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_mediaobs.entity_observations TO app_user;
        GRANT SELECT, INSERT ON mod_mediaobs.entity_observations TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.hierarchy_observations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.hierarchy_observations FORCE ROW LEVEL SECURITY;
        CREATE POLICY hierarchy_observations_tenant_isolation ON mod_mediaobs.hierarchy_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_mediaobs.hierarchy_observations TO app_user;
        GRANT SELECT, INSERT ON mod_mediaobs.hierarchy_observations TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.metric_periods ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.metric_periods FORCE ROW LEVEL SECURITY;
        CREATE POLICY metric_periods_tenant_isolation ON mod_mediaobs.metric_periods USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_mediaobs.metric_periods TO app_user;
        GRANT SELECT, INSERT ON mod_mediaobs.metric_periods TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.metric_observations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.metric_observations FORCE ROW LEVEL SECURITY;
        CREATE POLICY metric_observations_tenant_isolation ON mod_mediaobs.metric_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_mediaobs.metric_observations TO app_user;
        GRANT SELECT, INSERT ON mod_mediaobs.metric_observations TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.reconciliation_evidence ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.reconciliation_evidence FORCE ROW LEVEL SECURITY;
        CREATE POLICY reconciliation_evidence_tenant_isolation ON mod_mediaobs.reconciliation_evidence USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_mediaobs.reconciliation_evidence TO app_user;
        GRANT SELECT, INSERT ON mod_mediaobs.reconciliation_evidence TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.current_entities ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.current_entities FORCE ROW LEVEL SECURITY;
        CREATE POLICY current_entities_tenant_isolation ON mod_mediaobs.current_entities USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_mediaobs.current_entities TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_mediaobs.current_entities TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.current_hierarchy ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.current_hierarchy FORCE ROW LEVEL SECURITY;
        CREATE POLICY current_hierarchy_tenant_isolation ON mod_mediaobs.current_hierarchy USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_mediaobs.current_hierarchy TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_mediaobs.current_hierarchy TO platform_api;
        """
    )
    op.execute(
        """
        ALTER TABLE mod_mediaobs.current_metrics ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_mediaobs.current_metrics FORCE ROW LEVEL SECURITY;
        CREATE POLICY current_metrics_tenant_isolation ON mod_mediaobs.current_metrics USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_mediaobs.current_metrics TO app_user;
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_mediaobs.current_metrics TO platform_api;
        """
    )


def downgrade() -> None:
    for table in reversed((*_APPEND_ONLY, *_PROJECTIONS)):
        op.execute(f"DROP TABLE IF EXISTS mod_mediaobs.{table} CASCADE;")
    op.execute(
        "DROP FUNCTION IF EXISTS mod_mediaobs.refuse_metric_period_overlap() CASCADE;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mod_mediaobs.refuse_append_only_mutation() CASCADE;"
    )
