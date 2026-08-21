"""Create tenant-only first-party web analytics (ADR-0055).

Revision ID: wa_0001_web_analytics
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "wa_0001_web_analytics"
down_revision = None
branch_labels = ("web_analytics",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_webanalytics"


def _identity(unique_name: str) -> tuple[Any, ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{unique_name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{unique_name}_tenant_id"),
    )


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_webanalytics;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_webanalytics TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        "analytics_properties",
        *_identity("analytics_properties"),
        sa.Column("code", sa.String(96), nullable=False),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("allowed_origins", postgresql.JSONB(), nullable=False),
        sa.Column("timezone_name", sa.String(64), nullable=False),
        sa.Column("raw_retention_days", sa.Integer(), nullable=False),
        sa.Column("replay_evidence_days", sa.Integer(), nullable=False),
        sa.Column("active_generation_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_analytics_properties_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "analytics_streams",
        *_identity("analytics_streams"),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(96), nullable=False),
        sa.Column("accepted_protocol_versions", postgresql.JSONB(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "code",
            name="uq_analytics_streams_property_code",
        ),
        sa.UniqueConstraint(
            "tenant_id", "property_id", "id", name="uq_analytics_streams_property_id"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            [
                "mod_webanalytics.analytics_properties.tenant_id",
                "mod_webanalytics.analytics_properties.id",
            ],
            ondelete="CASCADE",
            name="fk_analytics_streams_property",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "event_observations",
        *_identity("event_observations"),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("event_code", sa.String(96), nullable=False),
        sa.Column("event_schema_version", sa.Integer(), nullable=False),
        sa.Column("content_fingerprint", sa.String(71), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("visitor_digest", sa.String(71), nullable=False),
        sa.Column("pseudonym_key_version", sa.Integer(), nullable=False),
        sa.Column("canonical_origin", sa.String(255), nullable=True),
        sa.Column("canonical_path", sa.String(2048), nullable=True),
        sa.Column("referrer_origin", sa.String(255), nullable=True),
        sa.Column("referrer_path", sa.String(2048), nullable=True),
        sa.Column("acquisition_source", sa.String(128), nullable=True),
        sa.Column("acquisition_medium", sa.String(128), nullable=True),
        sa.Column("acquisition_campaign", sa.String(128), nullable=True),
        sa.Column("acquisition_term", sa.String(128), nullable=True),
        sa.Column("acquisition_content", sa.String(128), nullable=True),
        sa.Column("device_class", sa.String(16), nullable=False),
        sa.Column("attributes_json", postgresql.JSONB(), nullable=False),
        sa.Column("privacy_policy_version", sa.String(80), nullable=False),
        sa.Column("consent_state", sa.String(16), nullable=False),
        sa.Column("global_privacy_control", sa.Boolean(), nullable=False),
        sa.Column("do_not_track", sa.Boolean(), nullable=False),
        sa.Column("privacy_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adapter_code", sa.String(96), nullable=False),
        sa.Column("admission_origin", sa.String(255), nullable=False),
        sa.Column("admission_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("transport_kind", sa.String(16), nullable=False),
        sa.Column("source_system", sa.String(96), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("delivery_id", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "stream_id",
            "event_id",
            name="uq_event_observations_identity",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id"],
            [
                "mod_webanalytics.analytics_properties.tenant_id",
                "mod_webanalytics.analytics_properties.id",
            ],
            ondelete="CASCADE",
            name="fk_event_observations_property",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "property_id", "stream_id"],
            [
                "mod_webanalytics.analytics_streams.tenant_id",
                "mod_webanalytics.analytics_streams.property_id",
                "mod_webanalytics.analytics_streams.id",
            ],
            ondelete="CASCADE",
            name="fk_event_observations_stream",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_event_observations_property_time",
        "event_observations",
        ["tenant_id", "property_id", "occurred_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_event_observations_expiry",
        "event_observations",
        ["tenant_id", "property_id", "expires_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_event_observations_visitor",
        "event_observations",
        ["tenant_id", "property_id", "visitor_digest"],
        schema=_SCHEMA,
    )
    op.create_table(
        "event_replay_tombstones",
        *_identity("event_replay_tombstones"),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("content_fingerprint", sa.String(71), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "stream_id",
            "event_id",
            name="uq_event_replay_tombstones_identity",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_event_replay_tombstones_expiry",
        "event_replay_tombstones",
        ["tenant_id", "property_id", "expires_at"],
        schema=_SCHEMA,
    )
    op.create_table(
        "event_conflict_evidence",
        *_identity("event_conflicts"),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("stream_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("existing_fingerprint", sa.String(71), nullable=False),
        sa.Column("presented_fingerprint", sa.String(71), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_system", sa.String(96), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("delivery_id", sa.String(255), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "stream_id",
            "event_id",
            "presented_fingerprint",
            name="uq_event_conflicts_presented",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "event_classification_evidence",
        *_identity("event_classifications"),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("classifier_code", sa.String(96), nullable=False),
        sa.Column("classifier_version", sa.Integer(), nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_bot", sa.Boolean(), nullable=False),
        sa.Column("analytically_included", sa.Boolean(), nullable=False),
        sa.Column("reasons_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "observation_id",
            "classifier_code",
            "classifier_version",
            name="uq_event_classifications_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                "mod_webanalytics.event_observations.tenant_id",
                "mod_webanalytics.event_observations.id",
            ],
            ondelete="CASCADE",
            name="fk_event_classifications_observation",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "sessionization_rules",
        *_identity("sessionization_rules"),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(96), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("inactivity_seconds", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "code",
            "version",
            name="uq_sessionization_rules_version",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "projection_generations",
        *_identity("projection_generations"),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("projection_version", sa.Integer(), nullable=False),
        sa.Column("session_rule_code", sa.String(96), nullable=False),
        sa.Column("session_rule_version", sa.Integer(), nullable=False),
        sa.Column("timezone_name", sa.String(64), nullable=False),
        sa.Column("authoritative_digest", sa.String(71), nullable=False),
        sa.Column("projection_digest", sa.String(71), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "id",
            name="uq_projection_generations_property_id",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "visitor_projections",
        *_identity("visitor_projections"),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("visitor_digest", sa.String(71), nullable=False),
        sa.Column("pseudonym_key_version", sa.Integer(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_id",
            "property_id",
            "visitor_digest",
            name="uq_visitor_projections_identity",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "session_projections",
        *_identity("session_projections"),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("session_key", sa.String(71), nullable=False),
        sa.Column("visitor_digest", sa.String(71), nullable=False),
        sa.Column("rule_code", sa.String(96), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_id",
            "property_id",
            "session_key",
            name="uq_session_projections_identity",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "session_event_links",
        *_identity("session_event_links"),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("session_key", sa.String(71), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_id",
            "observation_id",
            name="uq_session_event_links_observation",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "aggregate_metrics",
        *_identity("aggregate_metrics"),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dimension", sa.String(24), nullable=False),
        sa.Column("dimension_key", sa.String(255), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("visitor_count", sa.Integer(), nullable=False),
        sa.Column("session_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_id",
            "property_id",
            "bucket_start",
            "dimension",
            "dimension_key",
            name="uq_aggregate_metrics_bucket",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "funnel_definitions",
        *_identity("funnel_definitions"),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(96), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("steps_json", postgresql.JSONB(), nullable=False),
        sa.Column("within_seconds", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "code",
            "version",
            name="uq_funnel_definitions_version",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "funnel_results",
        *_identity("funnel_results"),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("definition_code", sa.String(96), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("entrants", sa.Integer(), nullable=False),
        sa.Column("completed_by_step_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "generation_id",
            "property_id",
            "definition_code",
            "definition_version",
            name="uq_funnel_results_generation",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "privacy_deletion_evidence",
        *_identity("privacy_deletions"),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("deleted_observations", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "request_id",
            name="uq_privacy_deletions_request",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "projection_drift_evidence",
        *_identity("projection_drift"),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=True),
        sa.Column("authoritative_digest", sa.String(71), nullable=False),
        sa.Column("projection_digest", sa.String(71), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("repaired_generation_id", sa.Uuid(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id",
            "property_id",
            "detected_at",
            "projection_digest",
            name="uq_projection_drift_observation",
        ),
        schema=_SCHEMA,
    )

    # Explicit statements are intentionally repetitive: the migration gate and
    # human review can see every tenant table, grant and policy without executing
    # Python or trusting a dynamically assembled table list.
    op.execute(
        "ALTER TABLE mod_webanalytics.analytics_properties ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.analytics_properties FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY analytics_properties_tenant_isolation ON mod_webanalytics.analytics_properties USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.analytics_properties TO app_user, platform_api;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.analytics_streams ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.analytics_streams FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY analytics_streams_tenant_isolation ON mod_webanalytics.analytics_streams USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.analytics_streams TO app_user, platform_api;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.event_observations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.event_observations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY event_observations_tenant_isolation ON mod_webanalytics.event_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_webanalytics.event_observations TO app_user, platform_api;"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON mod_webanalytics.event_observations FROM app_user;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.event_replay_tombstones ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.event_replay_tombstones FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY event_replay_tombstones_tenant_isolation ON mod_webanalytics.event_replay_tombstones USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_webanalytics.event_replay_tombstones TO app_user, platform_api;"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON mod_webanalytics.event_replay_tombstones FROM app_user;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.event_conflict_evidence ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.event_conflict_evidence FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY event_conflict_evidence_tenant_isolation ON mod_webanalytics.event_conflict_evidence USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_webanalytics.event_conflict_evidence TO app_user, platform_api;"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON mod_webanalytics.event_conflict_evidence FROM app_user;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.event_classification_evidence ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.event_classification_evidence FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY event_classification_evidence_tenant_isolation ON mod_webanalytics.event_classification_evidence USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_webanalytics.event_classification_evidence TO app_user, platform_api;"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON mod_webanalytics.event_classification_evidence FROM app_user;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.sessionization_rules ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.sessionization_rules FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY sessionization_rules_tenant_isolation ON mod_webanalytics.sessionization_rules USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.sessionization_rules TO app_user, platform_api;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.projection_generations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.projection_generations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY projection_generations_tenant_isolation ON mod_webanalytics.projection_generations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.projection_generations TO app_user, platform_api;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.visitor_projections ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.visitor_projections FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY visitor_projections_tenant_isolation ON mod_webanalytics.visitor_projections USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.visitor_projections TO app_user, platform_api;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.session_projections ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.session_projections FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY session_projections_tenant_isolation ON mod_webanalytics.session_projections USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.session_projections TO app_user, platform_api;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.session_event_links ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.session_event_links FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY session_event_links_tenant_isolation ON mod_webanalytics.session_event_links USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.session_event_links TO app_user, platform_api;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.aggregate_metrics ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.aggregate_metrics FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY aggregate_metrics_tenant_isolation ON mod_webanalytics.aggregate_metrics USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.aggregate_metrics TO app_user, platform_api;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.funnel_definitions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.funnel_definitions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY funnel_definitions_tenant_isolation ON mod_webanalytics.funnel_definitions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.funnel_definitions TO app_user, platform_api;"
    )
    op.execute("ALTER TABLE mod_webanalytics.funnel_results ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_webanalytics.funnel_results FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY funnel_results_tenant_isolation ON mod_webanalytics.funnel_results USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_webanalytics.funnel_results TO app_user, platform_api;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.privacy_deletion_evidence ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.privacy_deletion_evidence FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY privacy_deletion_evidence_tenant_isolation ON mod_webanalytics.privacy_deletion_evidence USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_webanalytics.privacy_deletion_evidence TO app_user, platform_api;"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON mod_webanalytics.privacy_deletion_evidence FROM app_user;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.projection_drift_evidence ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_webanalytics.projection_drift_evidence FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY projection_drift_evidence_tenant_isolation ON mod_webanalytics.projection_drift_evidence USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_webanalytics.projection_drift_evidence TO app_user, platform_api;"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON mod_webanalytics.projection_drift_evidence FROM app_user;"
    )

    op.execute(
        """
        CREATE FUNCTION mod_webanalytics.refuse_online_history_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF current_user = 'app_admin' THEN
                IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'mod_webanalytics.% is append-only for online roles', TG_TABLE_NAME;
        END;
        $$;
        """
    )
    op.execute(
        "CREATE TRIGGER event_observations_append_only BEFORE UPDATE OR DELETE ON mod_webanalytics.event_observations FOR EACH ROW EXECUTE FUNCTION mod_webanalytics.refuse_online_history_mutation();"
    )
    op.execute(
        "CREATE TRIGGER event_replay_tombstones_append_only BEFORE UPDATE OR DELETE ON mod_webanalytics.event_replay_tombstones FOR EACH ROW EXECUTE FUNCTION mod_webanalytics.refuse_online_history_mutation();"
    )
    op.execute(
        "CREATE TRIGGER event_conflict_evidence_append_only BEFORE UPDATE OR DELETE ON mod_webanalytics.event_conflict_evidence FOR EACH ROW EXECUTE FUNCTION mod_webanalytics.refuse_online_history_mutation();"
    )
    op.execute(
        "CREATE TRIGGER event_classification_evidence_append_only BEFORE UPDATE OR DELETE ON mod_webanalytics.event_classification_evidence FOR EACH ROW EXECUTE FUNCTION mod_webanalytics.refuse_online_history_mutation();"
    )
    op.execute(
        "CREATE TRIGGER privacy_deletion_evidence_append_only BEFORE UPDATE OR DELETE ON mod_webanalytics.privacy_deletion_evidence FOR EACH ROW EXECUTE FUNCTION mod_webanalytics.refuse_online_history_mutation();"
    )
    op.execute(
        "CREATE TRIGGER projection_drift_evidence_append_only BEFORE UPDATE OR DELETE ON mod_webanalytics.projection_drift_evidence FOR EACH ROW EXECUTE FUNCTION mod_webanalytics.refuse_online_history_mutation();"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS mod_webanalytics CASCADE;")
