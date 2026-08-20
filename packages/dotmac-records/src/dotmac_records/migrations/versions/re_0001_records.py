"""Create the tenant managed-records authority.

Revision ID: re_0001_records
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "re_0001_records"
down_revision = None
branch_labels = ("records",)

REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_records"


def _identity() -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
    )


def _tenant_constraints(name: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id"),
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_records;")
    op.execute("GRANT USAGE ON SCHEMA mod_records TO app_user, app_admin;")

    op.create_table(
        "retention_schedule_versions",
        *_identity(),
        sa.Column("schedule_code", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trigger_event_type", sa.String(160), nullable=False),
        sa.Column("duration_days", sa.Integer(), nullable=True),
        sa.Column("permanent", sa.Boolean(), nullable=False),
        sa.Column("cutoff_rule", sa.String(40), nullable=False),
        sa.Column("final_action", sa.String(40), nullable=False),
        sa.Column("disposition_approval_policy", sa.String(160), nullable=False),
        sa.Column("review_cadence_days", sa.Integer(), nullable=False),
        sa.Column("authority", sa.Text(), nullable=False),
        sa.Column("accountable_owner", sa.String(160), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("retention_schedule_versions"),
        sa.UniqueConstraint(
            "tenant_id",
            "schedule_code",
            "version",
            name="uq_retention_schedule_versions_code_version",
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_retention_schedule_versions_positive"
        ),
        sa.CheckConstraint(
            "(permanent AND duration_days IS NULL) OR (NOT permanent AND duration_days IS NOT NULL AND duration_days >= 0)",
            name="ck_retention_schedule_versions_duration",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_retention_schedule_versions_tenant_id",
        "retention_schedule_versions",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "record_series_versions",
        *_identity(),
        sa.Column("series_code", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("parent_series_code", sa.String(120), nullable=True),
        sa.Column("parent_series_version", sa.Integer(), nullable=True),
        sa.Column("responsible_owner", sa.String(160), nullable=False),
        sa.Column("custodian", sa.String(160), nullable=False),
        sa.Column("jurisdiction", sa.String(80), nullable=False),
        sa.Column("regulatory_basis", sa.Text(), nullable=False),
        sa.Column("default_schedule_code", sa.String(120), nullable=False),
        sa.Column("default_schedule_version", sa.Integer(), nullable=False),
        sa.Column("vital_record", sa.Boolean(), nullable=False),
        sa.Column("confidentiality", sa.String(80), nullable=False),
        sa.Column("transfer_destination", sa.String(255), nullable=False),
        sa.Column("required_fields", postgresql.JSONB(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("record_series_versions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "default_schedule_code", "default_schedule_version"],
            [
                "mod_records.retention_schedule_versions.tenant_id",
                "mod_records.retention_schedule_versions.schedule_code",
                "mod_records.retention_schedule_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_record_series_versions_default_schedule",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_series_code", "parent_series_version"],
            [
                "mod_records.record_series_versions.tenant_id",
                "mod_records.record_series_versions.series_code",
                "mod_records.record_series_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_record_series_versions_parent",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "series_code",
            "version",
            name="uq_record_series_versions_code_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_record_series_versions_positive"),
        sa.CheckConstraint(
            "(parent_series_code IS NULL AND parent_series_version IS NULL) OR "
            "(parent_series_code IS NOT NULL AND parent_series_version IS NOT NULL)",
            name="ck_record_series_versions_parent_complete",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_record_series_versions_tenant_id",
        "record_series_versions",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "records",
        *_identity(),
        sa.Column("source_owner", sa.String(160), nullable=False),
        sa.Column("source_type", sa.String(160), nullable=False),
        sa.Column("source_id", sa.String(500), nullable=False),
        sa.Column("source_version", sa.String(255), nullable=False),
        sa.Column("source_authority", sa.String(255), nullable=False),
        sa.Column("source_provenance", postgresql.JSONB(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=True),
        sa.Column("checksum_sha256", sa.String(71), nullable=True),
        sa.Column("media_type", sa.String(200), nullable=True),
        sa.Column("byte_length", sa.BigInteger(), nullable=True),
        sa.Column("series_code", sa.String(120), nullable=False),
        sa.Column("series_version", sa.Integer(), nullable=False),
        sa.Column("schedule_code", sa.String(120), nullable=False),
        sa.Column("schedule_version", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column("sensitivity", sa.String(80), nullable=False),
        sa.Column("access_restrictions", postgresql.JSONB(), nullable=False),
        sa.Column("declaration_fingerprint", sa.String(64), nullable=False),
        sa.Column("declared_by", sa.Uuid(), nullable=False),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("supersedes_record_id", sa.Uuid(), nullable=True),
        sa.Column("state", sa.String(40), nullable=False),
        sa.Column("retention_triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retention_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("final_evidence_ref", sa.String(500), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("records"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "series_code", "series_version"],
            [
                "mod_records.record_series_versions.tenant_id",
                "mod_records.record_series_versions.series_code",
                "mod_records.record_series_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_records_series_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "schedule_code", "schedule_version"],
            [
                "mod_records.retention_schedule_versions.tenant_id",
                "mod_records.retention_schedule_versions.schedule_code",
                "mod_records.retention_schedule_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_records_schedule_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "supersedes_record_id"],
            ["mod_records.records.tenant_id", "mod_records.records.id"],
            ondelete="RESTRICT",
            name="fk_records_supersedes",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_type",
            "source_id",
            "source_version",
            name="uq_records_exact_source_version",
        ),
        sa.CheckConstraint(
            "(file_id IS NULL AND checksum_sha256 IS NULL AND media_type IS NULL AND byte_length IS NULL) OR "
            "(file_id IS NOT NULL AND checksum_sha256 IS NOT NULL AND media_type IS NOT NULL AND byte_length IS NOT NULL AND byte_length >= 0)",
            name="ck_records_complete_file_snapshot",
        ),
        schema=_SCHEMA,
    )
    op.create_index("ix_records_tenant_id", "records", ["tenant_id"], schema=_SCHEMA)
    op.create_index(
        "ix_records_tenant_state", "records", ["tenant_id", "state"], schema=_SCHEMA
    )
    op.create_index(
        "ix_records_retention_due",
        "records",
        ["tenant_id", "retention_due_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "record_trigger_observations",
        *_identity(),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(160), nullable=False),
        sa.Column("source_event_id", sa.String(500), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("source_version", sa.String(160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        *_tenant_constraints("record_trigger_observations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            ["mod_records.records.tenant_id", "mod_records.records.id"],
            ondelete="RESTRICT",
            name="fk_record_trigger_observations_record",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_record_trigger_observations_source_event",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_record_trigger_observations_tenant_id",
        "record_trigger_observations",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "legal_hold_cases",
        *_identity(),
        sa.Column("case_code", sa.String(120), nullable=False),
        sa.Column("authority", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("responsible_officer", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("ongoing_capture_rule", postgresql.JSONB(), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.Uuid(), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        *_tenant_constraints("legal_hold_cases"),
        sa.UniqueConstraint("tenant_id", "case_code", name="uq_legal_hold_cases_code"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_legal_hold_cases_tenant_id",
        "legal_hold_cases",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "legal_hold_targets",
        *_identity(),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("target_kind", sa.String(32), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=True),
        sa.Column("series_code", sa.String(120), nullable=True),
        sa.Column("series_version", sa.Integer(), nullable=True),
        sa.Column("cohort_fingerprint", sa.String(64), nullable=True),
        sa.Column("cohort_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("placed_by", sa.Uuid(), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.Uuid(), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        *_tenant_constraints("legal_hold_targets"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                "mod_records.legal_hold_cases.tenant_id",
                "mod_records.legal_hold_cases.id",
            ],
            ondelete="RESTRICT",
            name="fk_legal_hold_targets_case",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            ["mod_records.records.tenant_id", "mod_records.records.id"],
            ondelete="RESTRICT",
            name="fk_legal_hold_targets_record",
        ),
        sa.CheckConstraint(
            "(target_kind = 'record' AND record_id IS NOT NULL AND series_code IS NULL AND series_version IS NULL AND cohort_fingerprint IS NULL AND cohort_snapshot IS NULL) OR "
            "(target_kind = 'series' AND record_id IS NULL AND series_code IS NOT NULL AND series_version IS NOT NULL AND cohort_fingerprint IS NULL AND cohort_snapshot IS NULL) OR "
            "(target_kind = 'cohort' AND record_id IS NULL AND series_code IS NULL AND series_version IS NULL AND cohort_fingerprint IS NOT NULL AND cohort_snapshot IS NOT NULL)",
            name="ck_legal_hold_targets_exact_kind",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_legal_hold_targets_tenant_id",
        "legal_hold_targets",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_legal_hold_targets_record",
        "legal_hold_targets",
        ["tenant_id", "record_id", "released_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "disposition_batches",
        *_identity(),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_request_id", sa.Uuid(), nullable=True),
        sa.Column("approval_digest", sa.String(71), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Uuid(), nullable=True),
        *_tenant_constraints("disposition_batches"),
        sa.UniqueConstraint(
            "tenant_id", "content_digest", name="uq_disposition_batches_digest"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_disposition_batches_tenant_id",
        "disposition_batches",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "disposition_items",
        *_identity(),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("eligibility_fingerprint", sa.String(64), nullable=False),
        sa.Column("eligibility_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("final_action", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("outcome", sa.String(40), nullable=True),
        sa.Column("authorization_id", sa.Uuid(), nullable=True),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("physical_state", sa.String(80), nullable=True),
        sa.Column("provider_evidence_ref", sa.String(500), nullable=True),
        *_tenant_constraints("disposition_items"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "batch_id"],
            [
                "mod_records.disposition_batches.tenant_id",
                "mod_records.disposition_batches.id",
            ],
            ondelete="RESTRICT",
            name="fk_disposition_items_batch",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            ["mod_records.records.tenant_id", "mod_records.records.id"],
            ondelete="RESTRICT",
            name="fk_disposition_items_record",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "batch_id",
            "record_id",
            name="uq_disposition_items_batch_record",
        ),
        sa.UniqueConstraint(
            "tenant_id", "authorization_id", name="uq_disposition_items_authorization"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_disposition_items_tenant_id",
        "disposition_items",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "custody_transfers",
        *_identity(),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("from_custodian", sa.String(255), nullable=False),
        sa.Column("to_custodian", sa.String(255), nullable=False),
        sa.Column("manifest_fingerprint", sa.String(64), nullable=False),
        sa.Column("manifest_file_id", sa.Uuid(), nullable=True),
        sa.Column("manifest_checksum_sha256", sa.String(71), nullable=True),
        sa.Column("transferred_by", sa.Uuid(), nullable=False),
        sa.Column("transferred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_evidence", postgresql.JSONB(), nullable=True),
        *_tenant_constraints("custody_transfers"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            ["mod_records.records.tenant_id", "mod_records.records.id"],
            ondelete="RESTRICT",
            name="fk_custody_transfers_record",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_custody_transfers_tenant_id",
        "custody_transfers",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "preservation_checks",
        *_identity(),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(160), nullable=False),
        sa.Column("source_observation_id", sa.String(500), nullable=False),
        sa.Column("expected_checksum_sha256", sa.String(71), nullable=False),
        sa.Column("observed_checksum_sha256", sa.String(71), nullable=True),
        sa.Column("physical_state", sa.String(80), nullable=False),
        sa.Column("storage_location_observation", sa.String(500), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        *_tenant_constraints("preservation_checks"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            ["mod_records.records.tenant_id", "mod_records.records.id"],
            ondelete="RESTRICT",
            name="fk_preservation_checks_record",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "record_id",
            "source_owner",
            "source_observation_id",
            name="uq_preservation_checks_observation",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_preservation_checks_tenant_id",
        "preservation_checks",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "record_events",
        *_identity(),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("record_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "record_id"],
            ["mod_records.records.tenant_id", "mod_records.records.id"],
            ondelete="RESTRICT",
            name="fk_record_events_record",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_record_events_tenant_id", "record_events", ["tenant_id"], schema=_SCHEMA
    )
    op.create_index(
        "ix_record_events_timeline",
        "record_events",
        ["tenant_id", "record_id", "occurred_at"],
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_records.protect_record_definition() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'record series and retention schedule versions are immutable'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER record_series_versions_immutable BEFORE UPDATE OR DELETE ON mod_records.record_series_versions FOR EACH ROW EXECUTE FUNCTION mod_records.protect_record_definition();"
    )
    op.execute(
        "CREATE TRIGGER retention_schedule_versions_immutable BEFORE UPDATE OR DELETE ON mod_records.retention_schedule_versions FOR EACH ROW EXECUTE FUNCTION mod_records.protect_record_definition();"
    )
    op.execute(
        "CREATE TRIGGER record_trigger_observations_immutable BEFORE UPDATE OR DELETE ON mod_records.record_trigger_observations FOR EACH ROW EXECUTE FUNCTION mod_records.protect_record_definition();"
    )
    op.execute(
        "CREATE TRIGGER custody_transfers_immutable BEFORE UPDATE OR DELETE ON mod_records.custody_transfers FOR EACH ROW EXECUTE FUNCTION mod_records.protect_record_definition();"
    )
    op.execute(
        "CREATE TRIGGER preservation_checks_immutable BEFORE UPDATE OR DELETE ON mod_records.preservation_checks FOR EACH ROW EXECUTE FUNCTION mod_records.protect_record_definition();"
    )
    op.execute(
        """
        CREATE FUNCTION mod_records.protect_record_snapshot() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'declared record evidence snapshot cannot be deleted'
                    USING ERRCODE = 'restrict_violation';
            ELSIF (to_jsonb(NEW) - ARRAY['state','retention_triggered_at','retention_due_at','review_at','final_evidence_ref','updated_at']::text[])
               <> (to_jsonb(OLD) - ARRAY['state','retention_triggered_at','retention_due_at','review_at','final_evidence_ref','updated_at']::text[]) THEN
                RAISE EXCEPTION 'declared record evidence snapshot is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER records_snapshot_immutable BEFORE UPDATE OR DELETE ON mod_records.records FOR EACH ROW EXECUTE FUNCTION mod_records.protect_record_snapshot();"
    )
    op.execute(
        """
        CREATE FUNCTION mod_records.protect_disposition_membership() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' OR
               (to_jsonb(NEW) - ARRAY['status','outcome','authorization_id','authorized_at','executed_at','physical_state','provider_evidence_ref']::text[])
               <> (to_jsonb(OLD) - ARRAY['status','outcome','authorization_id','authorized_at','executed_at','physical_state','provider_evidence_ref']::text[]) THEN
                RAISE EXCEPTION 'disposition item membership is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER disposition_items_immutable_membership BEFORE UPDATE OR DELETE ON mod_records.disposition_items FOR EACH ROW EXECUTE FUNCTION mod_records.protect_disposition_membership();"
    )
    op.execute(
        """
        CREATE FUNCTION mod_records.protect_record_event() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'record events are append-only'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER record_events_append_only BEFORE UPDATE OR DELETE ON mod_records.record_events FOR EACH ROW EXECUTE FUNCTION mod_records.protect_record_event();"
    )

    op.execute(
        "ALTER TABLE mod_records.record_series_versions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_records.record_series_versions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY record_series_versions_tenant_isolation ON mod_records.record_series_versions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.record_series_versions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_records.retention_schedule_versions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_records.retention_schedule_versions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY retention_schedule_versions_tenant_isolation ON mod_records.retention_schedule_versions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.retention_schedule_versions TO app_user;"
    )
    op.execute("ALTER TABLE mod_records.records ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_records.records FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY records_tenant_isolation ON mod_records.records USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.records TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_records.record_trigger_observations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_records.record_trigger_observations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY record_trigger_observations_tenant_isolation ON mod_records.record_trigger_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.record_trigger_observations TO app_user;"
    )
    op.execute("ALTER TABLE mod_records.legal_hold_cases ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_records.legal_hold_cases FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY legal_hold_cases_tenant_isolation ON mod_records.legal_hold_cases USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.legal_hold_cases TO app_user;"
    )
    op.execute("ALTER TABLE mod_records.legal_hold_targets ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_records.legal_hold_targets FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY legal_hold_targets_tenant_isolation ON mod_records.legal_hold_targets USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.legal_hold_targets TO app_user;"
    )
    op.execute("ALTER TABLE mod_records.disposition_batches ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_records.disposition_batches FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY disposition_batches_tenant_isolation ON mod_records.disposition_batches USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.disposition_batches TO app_user;"
    )
    op.execute("ALTER TABLE mod_records.disposition_items ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_records.disposition_items FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY disposition_items_tenant_isolation ON mod_records.disposition_items USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.disposition_items TO app_user;"
    )
    op.execute("ALTER TABLE mod_records.custody_transfers ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_records.custody_transfers FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY custody_transfers_tenant_isolation ON mod_records.custody_transfers USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.custody_transfers TO app_user;"
    )
    op.execute("ALTER TABLE mod_records.preservation_checks ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_records.preservation_checks FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY preservation_checks_tenant_isolation ON mod_records.preservation_checks USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.preservation_checks TO app_user;"
    )
    op.execute("ALTER TABLE mod_records.record_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_records.record_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY record_events_tenant_isolation ON mod_records.record_events USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_records.record_events TO app_user;"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS mod_records CASCADE;")
