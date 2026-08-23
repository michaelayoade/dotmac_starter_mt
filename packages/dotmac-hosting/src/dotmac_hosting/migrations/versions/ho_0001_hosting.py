"""Create the tenant-only hosting-service lifecycle owner.

Revision ID: ho_0001_hosting
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

revision = "ho_0001_hosting"
down_revision = None
branch_labels = ("hosting",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "tenant_audit_log.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
    "outbox_relay.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_hosting"


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


def _tenant_index(table: str) -> None:
    op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"], schema=_SCHEMA)


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE mod_hosting.{table} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE mod_hosting.{table} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON mod_hosting.{table} "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_hosting;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_hosting TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        "hosting_specifications",
        *_identity(),
        sa.Column("specification_code", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("hosting_specifications"),
        sa.UniqueConstraint(
            "tenant_id",
            "specification_code",
            name="uq_hosting_specifications_tenant_code",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            "specification_code",
            name="uq_hosting_specifications_tenant_id_code",
        ),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_specifications")

    op.create_table(
        "hosting_specification_versions",
        *_identity(),
        sa.Column("specification_id", sa.Uuid(), nullable=False),
        sa.Column("specification_code", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("package_ref", sa.String(255), nullable=False),
        sa.Column("package_rank", sa.Integer(), nullable=False),
        sa.Column("allowances", postgresql.JSONB(), nullable=False),
        sa.Column("included_artifacts", postgresql.JSONB(), nullable=False),
        sa.Column("capability_codes", postgresql.JSONB(), nullable=False),
        sa.Column("change_rules", postgresql.JSONB(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("previous_content_digest", sa.String(64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("hosting_specification_versions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "specification_id", "specification_code"],
            [
                "mod_hosting.hosting_specifications.tenant_id",
                "mod_hosting.hosting_specifications.id",
                "mod_hosting.hosting_specifications.specification_code",
            ],
            ondelete="RESTRICT",
            name="fk_hosting_specification_versions_specification",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "specification_code",
            "version",
            name="uq_hosting_specification_versions_tenant_code_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "specification_code",
            "version",
            "content_digest",
            name="uq_hosting_specification_versions_chain_identity",
        ),
        sa.ForeignKeyConstraint(
            [
                "tenant_id",
                "specification_code",
                "previous_version",
                "previous_content_digest",
            ],
            [
                "mod_hosting.hosting_specification_versions.tenant_id",
                "mod_hosting.hosting_specification_versions.specification_code",
                "mod_hosting.hosting_specification_versions.version",
                "mod_hosting.hosting_specification_versions.content_digest",
            ],
            ondelete="RESTRICT",
            name="fk_hosting_specification_versions_previous",
        ),
        sa.CheckConstraint("version > 0", name="ck_hosting_specification_versions_version"),
        sa.CheckConstraint(
            "package_rank >= 0",
            name="ck_hosting_specification_versions_package_rank",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(change_rules) = 'object' "
            "AND change_rules ?& ARRAY['upgrade_allowed','downgrade_allowed',"
            "'downgrade_requires_review','same_level_allowed'] "
            "AND (change_rules - ARRAY['upgrade_allowed','downgrade_allowed',"
            "'downgrade_requires_review','same_level_allowed']) = '{}'::jsonb "
            "AND jsonb_typeof(change_rules->'upgrade_allowed') = 'boolean' "
            "AND jsonb_typeof(change_rules->'downgrade_allowed') = 'boolean' "
            "AND jsonb_typeof(change_rules->'downgrade_requires_review') = 'boolean' "
            "AND jsonb_typeof(change_rules->'same_level_allowed') = 'boolean'",
            name="ck_hosting_specification_versions_change_rules_shape",
        ),
        sa.CheckConstraint(
            "(version = 1 AND previous_version IS NULL AND previous_content_digest IS NULL) "
            "OR (version > 1 AND previous_version = version - 1 AND previous_content_digest IS NOT NULL)",
            name="ck_hosting_specification_versions_previous_link",
        ),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_specification_versions")

    op.create_table(
        "hosting_services",
        *_identity(),
        sa.Column("customer_ref", sa.String(255), nullable=False),
        sa.Column("order_line_ref", sa.String(255), nullable=False),
        sa.Column("offer_version_ref", sa.String(255), nullable=False),
        sa.Column("specification_code", sa.String(120), nullable=False),
        sa.Column("specification_version", sa.Integer(), nullable=False),
        sa.Column("primary_domain", sa.String(253), nullable=False),
        sa.Column("account_label", sa.String(160), nullable=False),
        sa.Column("administrative_email", sa.String(254), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("capability_binding_ref", sa.String(255), nullable=True),
        sa.Column("provider_account_ref", sa.String(255), nullable=True),
        sa.Column("lifecycle_state", sa.String(48), nullable=False),
        sa.Column("state_effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("hosting_services"),
        sa.UniqueConstraint(
            "tenant_id", "order_line_ref", name="uq_hosting_services_order_line"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "capability_binding_ref",
            "provider_account_ref",
            name="uq_hosting_services_binding_account",
        ),
        sa.CheckConstraint(
            "(capability_binding_ref IS NULL) = (provider_account_ref IS NULL)",
            name="ck_hosting_services_provider_pair",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "specification_code", "specification_version"],
            [
                "mod_hosting.hosting_specification_versions.tenant_id",
                "mod_hosting.hosting_specification_versions.specification_code",
                "mod_hosting.hosting_specification_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_hosting_services_specification_version",
        ),
        sa.CheckConstraint("row_version >= 0", name="ck_hosting_services_row_version"),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_services")
    op.create_index(
        "ix_hosting_services_tenant_state",
        "hosting_services",
        ["tenant_id", "lifecycle_state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "hosting_desired_revisions",
        *_identity(),
        sa.Column("hosting_service_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("desired_account_state", sa.String(32), nullable=False),
        sa.Column("specification_code", sa.String(120), nullable=False),
        sa.Column("specification_version", sa.Integer(), nullable=False),
        sa.Column("package_ref", sa.String(255), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("hosting_desired_revisions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            ["mod_hosting.hosting_services.tenant_id", "mod_hosting.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_desired_revisions_service",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "specification_code", "specification_version"],
            [
                "mod_hosting.hosting_specification_versions.tenant_id",
                "mod_hosting.hosting_specification_versions.specification_code",
                "mod_hosting.hosting_specification_versions.version",
            ],
            ondelete="RESTRICT",
            name="fk_hosting_desired_revisions_specification_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "hosting_service_id",
            "version",
            name="uq_hosting_desired_revisions_service_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_hosting_desired_revisions_version"),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_desired_revisions")
    op.create_index(
        "ix_hosting_desired_revisions_tenant_service",
        "hosting_desired_revisions",
        ["tenant_id", "hosting_service_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "hosting_commands",
        *_identity(),
        sa.Column("hosting_service_id", sa.Uuid(), nullable=False),
        sa.Column("command_kind", sa.String(48), nullable=False),
        sa.Column("idempotency_scope", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(120), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("hosting_commands"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            ["mod_hosting.hosting_services.tenant_id", "mod_hosting.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_commands_service",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_scope",
            "idempotency_key",
            name="uq_hosting_commands_tenant_scope_key",
        ),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_commands")
    op.create_index(
        "ix_hosting_commands_tenant_service",
        "hosting_commands",
        ["tenant_id", "hosting_service_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "hosting_command_outcomes",
        *_identity(),
        sa.Column("hosting_service_id", sa.Uuid(), nullable=False),
        sa.Column("hosting_command_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_key", sa.String(255), nullable=False),
        sa.Column("outcome_kind", sa.String(32), nullable=False),
        sa.Column("outcome_class", sa.String(40), nullable=False),
        sa.Column("provider_reference", sa.String(255), nullable=True),
        sa.Column("reason_code", sa.String(160), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("hosting_command_outcomes"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            ["mod_hosting.hosting_services.tenant_id", "mod_hosting.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_command_outcomes_service",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hosting_command_id"],
            ["mod_hosting.hosting_commands.tenant_id", "mod_hosting.hosting_commands.id"],
            ondelete="RESTRICT",
            name="fk_hosting_command_outcomes_command",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "hosting_command_id",
            "evidence_key",
            name="uq_hosting_command_outcomes_command_evidence",
        ),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_command_outcomes")
    op.create_index(
        "ix_hosting_command_outcomes_tenant_service",
        "hosting_command_outcomes",
        ["tenant_id", "hosting_service_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "hosting_observations",
        *_identity(),
        sa.Column("hosting_service_id", sa.Uuid(), nullable=True),
        sa.Column("operation_reference", sa.String(120), nullable=True),
        sa.Column("provider_account_ref", sa.String(255), nullable=False),
        sa.Column("capability_binding_ref", sa.String(255), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("observation_kind", sa.String(120), nullable=False),
        sa.Column("provider_statuses", postgresql.JSONB(), nullable=False),
        sa.Column("observed_package_ref", sa.String(255), nullable=True),
        sa.Column("source_mode", sa.String(16), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("hosting_observations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            ["mod_hosting.hosting_services.tenant_id", "mod_hosting.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_observations_service",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "capability_binding_ref",
            "provider_event_id",
            name="uq_hosting_observations_binding_event",
        ),
        sa.CheckConstraint(
            "source_mode IN ('ingress', 'poll')",
            name="ck_hosting_observations_source_mode",
        ),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_observations")
    op.create_index(
        "ix_hosting_observations_tenant_service_time",
        "hosting_observations",
        ["tenant_id", "hosting_service_id", "observed_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_hosting_observations_tenant_operation",
        "hosting_observations",
        ["tenant_id", "operation_reference", "observed_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "hosting_observation_resources",
        *_identity(),
        sa.Column("hosting_observation_id", sa.Uuid(), nullable=False),
        sa.Column("resource_kind", sa.String(120), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 12), nullable=False),
        sa.Column("unit", sa.String(48), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("period_identity", sa.String(130), nullable=False),
        *_tenant_constraints("hosting_observation_resources"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hosting_observation_id"],
            ["mod_hosting.hosting_observations.tenant_id", "mod_hosting.hosting_observations.id"],
            ondelete="RESTRICT",
            name="fk_hosting_observation_resources_observation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "hosting_observation_id",
            "resource_kind",
            "unit",
            "period_identity",
            name="uq_hosting_observation_resources_fact",
        ),
        sa.CheckConstraint("quantity >= 0", name="ck_hosting_observation_resources_quantity"),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_observation_resources")
    op.create_index(
        "ix_hosting_observation_resources_tenant_observation",
        "hosting_observation_resources",
        ["tenant_id", "hosting_observation_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "hosting_suspension_locks",
        *_identity(),
        sa.Column("hosting_service_id", sa.Uuid(), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("allowed_restorer_codes", postgresql.JSONB(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_by", sa.String(120), nullable=True),
        *_tenant_constraints("hosting_suspension_locks"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            ["mod_hosting.hosting_services.tenant_id", "mod_hosting.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_suspension_locks_service",
        ),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_suspension_locks")
    op.create_index(
        "uq_hosting_suspension_locks_one_active",
        "hosting_suspension_locks",
        ["tenant_id", "hosting_service_id", "reason_code"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("cleared_at IS NULL"),
    )

    op.create_table(
        "hosting_retention_holds",
        *_identity(),
        sa.Column("hosting_service_id", sa.Uuid(), nullable=False),
        sa.Column("hold_code", sa.String(120), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("reason_code", sa.String(160), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cleared_reason", sa.String(160), nullable=True),
        *_tenant_constraints("hosting_retention_holds"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            ["mod_hosting.hosting_services.tenant_id", "mod_hosting.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_retention_holds_service",
        ),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_retention_holds")
    op.create_index(
        "uq_hosting_retention_holds_one_active",
        "hosting_retention_holds",
        ["tenant_id", "hosting_service_id", "hold_code"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("cleared_at IS NULL"),
    )

    op.create_table(
        "hosting_termination_approval_evidence",
        *_identity(),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(120), nullable=False),
        sa.Column("subject_id", sa.String(255), nullable=False),
        sa.Column("policy_code", sa.String(160), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.String(71), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("event_digest", sa.String(64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("hosting_termination_approval_evidence"),
        sa.UniqueConstraint(
            "tenant_id",
            "request_id",
            name="uq_hosting_termination_approval_evidence_request",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_event_id",
            name="uq_hosting_termination_approval_evidence_source_event",
        ),
        sa.CheckConstraint(
            "subject_type = 'hosting_service'",
            name="ck_hosting_termination_approval_evidence_subject",
        ),
        sa.CheckConstraint(
            "policy_code = 'hosting.termination.v1' AND policy_version = 1",
            name="ck_hosting_termination_approval_evidence_policy",
        ),
        sa.CheckConstraint(
            "state = 'approved'",
            name="ck_hosting_termination_approval_evidence_state",
        ),
        sa.CheckConstraint(
            "event_type = 'approval.approved' AND state = 'approved'",
            name="ck_hosting_termination_approval_evidence_event_state",
        ),
        sa.CheckConstraint(
            "content_digest ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_hosting_termination_approval_evidence_content_digest",
        ),
        sa.CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name="ck_hosting_termination_approval_evidence_event_digest",
        ),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_termination_approval_evidence")

    op.create_table(
        "hosting_attention_conditions",
        *_identity(),
        sa.Column("hosting_service_id", sa.Uuid(), nullable=False),
        sa.Column("source_command_id", sa.Uuid(), nullable=True),
        sa.Column("condition_code", sa.String(120), nullable=False),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column("reason_code", sa.String(160), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_code", sa.String(160), nullable=True),
        *_tenant_constraints("hosting_attention_conditions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "hosting_service_id"],
            ["mod_hosting.hosting_services.tenant_id", "mod_hosting.hosting_services.id"],
            ondelete="RESTRICT",
            name="fk_hosting_attention_conditions_service",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_command_id"],
            ["mod_hosting.hosting_commands.tenant_id", "mod_hosting.hosting_commands.id"],
            ondelete="RESTRICT",
            name="fk_hosting_attention_conditions_command",
        ),
        schema=_SCHEMA,
    )
    _tenant_index("hosting_attention_conditions")
    op.create_index(
        "uq_hosting_attention_conditions_one_open",
        "hosting_attention_conditions",
        ["tenant_id", "hosting_service_id", "condition_code"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("resolved_at IS NULL"),
    )

    op.execute(
        """
        CREATE FUNCTION mod_hosting.refuse_evidence_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'hosting specification, desired, command, outcome and observation evidence is immutable'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER hosting_specifications_immutable BEFORE UPDATE OR DELETE ON mod_hosting.hosting_specifications FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_evidence_mutation();")
    op.execute("CREATE TRIGGER hosting_specification_versions_immutable BEFORE UPDATE OR DELETE ON mod_hosting.hosting_specification_versions FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_evidence_mutation();")
    op.execute("CREATE TRIGGER hosting_desired_revisions_immutable BEFORE UPDATE OR DELETE ON mod_hosting.hosting_desired_revisions FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_evidence_mutation();")
    op.execute("CREATE TRIGGER hosting_commands_immutable BEFORE UPDATE OR DELETE ON mod_hosting.hosting_commands FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_evidence_mutation();")
    op.execute("CREATE TRIGGER hosting_command_outcomes_immutable BEFORE UPDATE OR DELETE ON mod_hosting.hosting_command_outcomes FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_evidence_mutation();")
    op.execute("CREATE TRIGGER hosting_observations_immutable BEFORE UPDATE OR DELETE ON mod_hosting.hosting_observations FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_evidence_mutation();")
    op.execute("CREATE TRIGGER hosting_observation_resources_immutable BEFORE UPDATE OR DELETE ON mod_hosting.hosting_observation_resources FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_evidence_mutation();")
    op.execute("CREATE TRIGGER hosting_termination_approval_evidence_immutable BEFORE UPDATE OR DELETE ON mod_hosting.hosting_termination_approval_evidence FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_evidence_mutation();")

    op.execute(
        """
        CREATE FUNCTION mod_hosting.enforce_hosting_service_transition()
        RETURNS trigger AS $$
        DECLARE
            lifecycle_changed boolean := NEW.lifecycle_state IS DISTINCT FROM OLD.lifecycle_state;
            specification_changed boolean :=
                NEW.specification_code IS DISTINCT FROM OLD.specification_code OR
                NEW.specification_version IS DISTINCT FROM OLD.specification_version;
            provider_changed boolean :=
                NEW.capability_binding_ref IS DISTINCT FROM OLD.capability_binding_ref OR
                NEW.provider_account_ref IS DISTINCT FROM OLD.provider_account_ref;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'a hosting service is terminated by transition, never deleted'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
               OR NEW.id IS DISTINCT FROM OLD.id
               OR NEW.customer_ref IS DISTINCT FROM OLD.customer_ref
               OR NEW.order_line_ref IS DISTINCT FROM OLD.order_line_ref
               OR NEW.offer_version_ref IS DISTINCT FROM OLD.offer_version_ref
               OR NEW.primary_domain IS DISTINCT FROM OLD.primary_domain
               OR NEW.account_label IS DISTINCT FROM OLD.account_label
               OR NEW.administrative_email IS DISTINCT FROM OLD.administrative_email
               OR NEW.country_code IS DISTINCT FROM OLD.country_code
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'hosting service identity is immutable'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF provider_changed AND NOT (
                OLD.capability_binding_ref IS NULL
                AND OLD.provider_account_ref IS NULL
                AND NEW.capability_binding_ref IS NOT NULL
                AND NEW.provider_account_ref IS NOT NULL
            ) THEN
                RAISE EXCEPTION 'hosting provider correlation may only be assigned once as a pair'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF specification_changed AND (lifecycle_changed OR provider_changed) THEN
                RAISE EXCEPTION 'a hosting specification change is one isolated owner transition'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF provider_changed AND lifecycle_changed AND NOT (
                OLD.lifecycle_state = 'provisioning'
                AND NEW.lifecycle_state = 'active'
            ) THEN
                RAISE EXCEPTION 'provider correlation and lifecycle may combine only on first activation'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF lifecycle_changed AND NOT (
                (OLD.lifecycle_state = 'provisioning' AND NEW.lifecycle_state = 'active') OR
                (OLD.lifecycle_state = 'active' AND NEW.lifecycle_state IN ('suspension_requested', 'terminating')) OR
                (OLD.lifecycle_state = 'suspension_requested' AND NEW.lifecycle_state IN ('suspended', 'restoration_requested')) OR
                (OLD.lifecycle_state = 'suspended' AND NEW.lifecycle_state IN ('restoration_requested', 'terminating')) OR
                (OLD.lifecycle_state = 'restoration_requested' AND NEW.lifecycle_state IN ('active', 'suspension_requested')) OR
                (OLD.lifecycle_state = 'terminating' AND NEW.lifecycle_state = 'terminated')
            ) THEN
                RAISE EXCEPTION 'invalid hosting lifecycle transition % -> %', OLD.lifecycle_state, NEW.lifecycle_state
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NEW.row_version <> OLD.row_version + 1 OR NEW.updated_at < OLD.updated_at THEN
                RAISE EXCEPTION 'hosting service mutation requires one monotonic version step'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF lifecycle_changed <> (NEW.state_effective_at IS DISTINCT FROM OLD.state_effective_at) THEN
                RAISE EXCEPTION 'state_effective_at changes exactly with lifecycle state'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            IF NOT lifecycle_changed AND NOT specification_changed AND NOT provider_changed THEN
                RAISE EXCEPTION 'hosting service update has no owner-permitted state change'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER hosting_services_controlled_update BEFORE UPDATE OR DELETE ON mod_hosting.hosting_services FOR EACH ROW EXECUTE FUNCTION mod_hosting.enforce_hosting_service_transition();")

    op.execute(
        """
        CREATE FUNCTION mod_hosting.mutate_hosting_service(
            p_tenant_id uuid,
            p_hosting_service_id uuid,
            p_expected_row_version integer,
            p_mutation_kind text,
            p_updated_at timestamp with time zone,
            p_specification_code text,
            p_specification_version integer,
            p_lifecycle_state text,
            p_state_effective_at timestamp with time zone,
            p_observation_id uuid
        )
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path TO pg_catalog, pg_temp
        AS $$
        DECLARE
            tenant_setting text;
            current_row mod_hosting.hosting_services%ROWTYPE;
            observed mod_hosting.hosting_observations%ROWTYPE;
            desired_state text;
            next_version integer;
        BEGIN
            tenant_setting := pg_catalog.current_setting('app.current_tenant', true);
            IF tenant_setting IS NULL
               OR tenant_setting = ''
               OR tenant_setting::uuid IS DISTINCT FROM p_tenant_id THEN
                RAISE EXCEPTION 'hosting mutation tenant does not match the session tenant'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF p_expected_row_version < 0 OR p_updated_at IS NULL THEN
                RAISE EXCEPTION 'hosting mutation requires an expected version and update instant'
                    USING ERRCODE = 'restrict_violation';
            END IF;

            SELECT * INTO current_row
            FROM mod_hosting.hosting_services
            WHERE tenant_id = p_tenant_id AND id = p_hosting_service_id
            FOR UPDATE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'hosting service was not found'
                    USING ERRCODE = 'no_data_found';
            END IF;
            IF current_row.row_version <> p_expected_row_version THEN
                RAISE EXCEPTION 'stale hosting service version'
                    USING ERRCODE = 'serialization_failure';
            END IF;
            IF p_updated_at < current_row.updated_at THEN
                RAISE EXCEPTION 'hosting mutation time cannot move backwards'
                    USING ERRCODE = 'restrict_violation';
            END IF;

            CASE
            WHEN p_mutation_kind = 'specification_change' THEN
                IF p_specification_code IS NULL
                   OR p_specification_version IS NULL
                   OR p_lifecycle_state IS NOT NULL
                   OR p_state_effective_at IS NOT NULL
                   OR p_observation_id IS NOT NULL
                   OR current_row.lifecycle_state NOT IN ('active', 'suspended')
                   OR (
                       p_specification_code = current_row.specification_code
                       AND p_specification_version = current_row.specification_version
                   ) THEN
                    RAISE EXCEPTION 'invalid hosting specification mutation contract'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM mod_hosting.hosting_desired_revisions AS desired
                    WHERE desired.tenant_id = p_tenant_id
                      AND desired.hosting_service_id = p_hosting_service_id
                      AND desired.specification_code = p_specification_code
                      AND desired.specification_version = p_specification_version
                      AND desired.requested_at = p_updated_at
                ) THEN
                    RAISE EXCEPTION 'hosting specification mutation lacks desired-state evidence'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                UPDATE mod_hosting.hosting_services
                SET specification_code = p_specification_code,
                    specification_version = p_specification_version,
                    row_version = row_version + 1,
                    updated_at = p_updated_at
                WHERE tenant_id = p_tenant_id AND id = p_hosting_service_id
                RETURNING row_version INTO next_version;

            WHEN p_mutation_kind = 'lifecycle_request' THEN
                IF p_specification_code IS NOT NULL
                   OR p_specification_version IS NOT NULL
                   OR p_lifecycle_state IS NULL
                   OR p_state_effective_at IS NULL
                   OR p_observation_id IS NOT NULL THEN
                    RAISE EXCEPTION 'invalid hosting lifecycle-request contract'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                IF NOT (
                    (current_row.lifecycle_state IN ('active', 'restoration_requested')
                     AND p_lifecycle_state = 'suspension_requested')
                    OR (current_row.lifecycle_state IN ('suspended', 'suspension_requested')
                        AND p_lifecycle_state = 'restoration_requested')
                    OR (current_row.lifecycle_state IN ('active', 'suspended')
                        AND p_lifecycle_state = 'terminating')
                ) THEN
                    RAISE EXCEPTION 'invalid hosting lifecycle request transition'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                desired_state := CASE p_lifecycle_state
                    WHEN 'suspension_requested' THEN 'suspended'
                    WHEN 'restoration_requested' THEN 'active'
                    WHEN 'terminating' THEN 'terminated'
                END;
                IF NOT EXISTS (
                    SELECT 1
                    FROM mod_hosting.hosting_desired_revisions AS desired
                    WHERE desired.tenant_id = p_tenant_id
                      AND desired.hosting_service_id = p_hosting_service_id
                      AND desired.desired_account_state = desired_state
                      AND desired.requested_at = p_updated_at
                ) THEN
                    RAISE EXCEPTION 'hosting lifecycle request lacks desired-state evidence'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                UPDATE mod_hosting.hosting_services
                SET lifecycle_state = p_lifecycle_state,
                    state_effective_at = p_state_effective_at,
                    row_version = row_version + 1,
                    updated_at = p_updated_at
                WHERE tenant_id = p_tenant_id AND id = p_hosting_service_id
                RETURNING row_version INTO next_version;

            WHEN p_mutation_kind IN ('provider_correlation', 'observation_confirmation') THEN
                IF p_specification_code IS NOT NULL
                   OR p_specification_version IS NOT NULL
                   OR p_observation_id IS NULL
                   OR (
                       p_mutation_kind = 'provider_correlation'
                       AND (p_lifecycle_state IS NOT NULL OR p_state_effective_at IS NOT NULL)
                   )
                   OR (
                       p_mutation_kind = 'observation_confirmation'
                       AND (p_lifecycle_state IS NULL OR p_state_effective_at IS NULL)
                   ) THEN
                    RAISE EXCEPTION 'invalid hosting observation mutation contract'
                        USING ERRCODE = 'restrict_violation';
                END IF;
                SELECT * INTO observed
                FROM mod_hosting.hosting_observations
                WHERE tenant_id = p_tenant_id AND id = p_observation_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'hosting observation was not found'
                        USING ERRCODE = 'no_data_found';
                END IF;
                IF current_row.provider_account_ref IS NULL THEN
                    IF observed.hosting_service_id IS NOT NULL
                       AND observed.hosting_service_id <> p_hosting_service_id THEN
                        RAISE EXCEPTION 'hosting observation hints another service'
                            USING ERRCODE = 'restrict_violation';
                    END IF;
                    IF observed.operation_reference IS NULL OR NOT EXISTS (
                        SELECT 1
                        FROM mod_hosting.hosting_commands AS command
                        WHERE command.tenant_id = p_tenant_id
                          AND command.hosting_service_id = p_hosting_service_id
                          AND command.id::text = observed.operation_reference
                    ) THEN
                        RAISE EXCEPTION 'first hosting correlation requires its command operation'
                            USING ERRCODE = 'restrict_violation';
                    END IF;
                ELSIF observed.capability_binding_ref <> current_row.capability_binding_ref
                   OR observed.provider_account_ref <> current_row.provider_account_ref THEN
                    RAISE EXCEPTION 'hosting observation contradicts the frozen provider pair'
                        USING ERRCODE = 'restrict_violation';
                END IF;

                IF p_mutation_kind = 'provider_correlation' THEN
                    IF current_row.provider_account_ref IS NOT NULL THEN
                        RAISE EXCEPTION 'hosting provider pair is already assigned'
                            USING ERRCODE = 'restrict_violation';
                    END IF;
                    UPDATE mod_hosting.hosting_services
                    SET capability_binding_ref = observed.capability_binding_ref,
                        provider_account_ref = observed.provider_account_ref,
                        row_version = row_version + 1,
                        updated_at = p_updated_at
                    WHERE tenant_id = p_tenant_id AND id = p_hosting_service_id
                    RETURNING row_version INTO next_version;
                ELSE
                    IF observed.observed_at <> p_state_effective_at
                       OR observed.observed_at < current_row.state_effective_at
                       OR NOT (
                           (current_row.lifecycle_state = 'provisioning'
                            AND p_lifecycle_state = 'active'
                            AND observed.observation_kind = 'active')
                           OR (current_row.lifecycle_state = 'suspension_requested'
                               AND p_lifecycle_state = 'suspended'
                               AND observed.observation_kind = 'suspended')
                           OR (current_row.lifecycle_state = 'restoration_requested'
                               AND p_lifecycle_state = 'active'
                               AND observed.observation_kind = 'active')
                           OR (current_row.lifecycle_state = 'terminating'
                               AND p_lifecycle_state = 'terminated'
                               AND observed.observation_kind = 'terminated')
                       ) THEN
                        RAISE EXCEPTION 'observation does not confirm the hosting transition'
                            USING ERRCODE = 'restrict_violation';
                    END IF;
                    UPDATE mod_hosting.hosting_services
                    SET capability_binding_ref = COALESCE(
                            capability_binding_ref, observed.capability_binding_ref
                        ),
                        provider_account_ref = COALESCE(
                            provider_account_ref, observed.provider_account_ref
                        ),
                        lifecycle_state = p_lifecycle_state,
                        state_effective_at = p_state_effective_at,
                        row_version = row_version + 1,
                        updated_at = p_updated_at
                    WHERE tenant_id = p_tenant_id AND id = p_hosting_service_id
                    RETURNING row_version INTO next_version;
                END IF;

            ELSE
                RAISE EXCEPTION 'unknown hosting mutation kind'
                    USING ERRCODE = 'restrict_violation';
            END CASE;
            RETURN next_version;
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_hosting.mutate_hosting_service("
        "uuid, uuid, integer, text, timestamp with time zone, text, integer, "
        "text, timestamp with time zone, uuid) FROM PUBLIC;"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION mod_hosting.mutate_hosting_service("
        "uuid, uuid, integer, text, timestamp with time zone, text, integer, "
        "text, timestamp with time zone, uuid) TO app_user;"
    )

    op.execute(
        """
        CREATE FUNCTION mod_hosting.refuse_lock_rewrite()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR OLD.cleared_at IS NOT NULL
               OR NEW.cleared_at IS NULL
               OR NEW.cleared_by IS NULL
               OR (to_jsonb(NEW) - 'cleared_at' - 'cleared_by')
                  IS DISTINCT FROM (to_jsonb(OLD) - 'cleared_at' - 'cleared_by') THEN
                RAISE EXCEPTION 'a hosting suspension lock may only move once from open to cleared'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER hosting_suspension_locks_controlled_update BEFORE UPDATE OR DELETE ON mod_hosting.hosting_suspension_locks FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_lock_rewrite();")
    op.execute(
        """
        CREATE FUNCTION mod_hosting.refuse_hold_rewrite()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR OLD.cleared_at IS NOT NULL
               OR NEW.cleared_at IS NULL
               OR NEW.cleared_reason IS NULL
               OR (to_jsonb(NEW) - 'cleared_at' - 'cleared_reason')
                  IS DISTINCT FROM (to_jsonb(OLD) - 'cleared_at' - 'cleared_reason') THEN
                RAISE EXCEPTION 'a hosting retention hold may only move once from open to cleared'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER hosting_retention_holds_controlled_update BEFORE UPDATE OR DELETE ON mod_hosting.hosting_retention_holds FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_hold_rewrite();")
    op.execute(
        """
        CREATE FUNCTION mod_hosting.refuse_attention_rewrite()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE'
               OR OLD.resolved_at IS NOT NULL
               OR NEW.resolved_at IS NULL
               OR NEW.resolution_code IS NULL
               OR (to_jsonb(NEW) - 'resolved_at' - 'resolution_code')
                  IS DISTINCT FROM (to_jsonb(OLD) - 'resolved_at' - 'resolution_code') THEN
                RAISE EXCEPTION 'hosting attention may only move once from open to resolved'
                    USING ERRCODE = 'restrict_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute("CREATE TRIGGER hosting_attention_conditions_controlled_update BEFORE UPDATE OR DELETE ON mod_hosting.hosting_attention_conditions FOR EACH ROW EXECUTE FUNCTION mod_hosting.refuse_attention_rewrite();")

    for table in (
        "hosting_specifications",
        "hosting_specification_versions",
        "hosting_services",
        "hosting_desired_revisions",
        "hosting_commands",
        "hosting_command_outcomes",
        "hosting_observations",
        "hosting_observation_resources",
        "hosting_suspension_locks",
        "hosting_retention_holds",
        "hosting_termination_approval_evidence",
        "hosting_attention_conditions",
    ):
        _rls(table)

    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_specifications TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_specification_versions TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_services TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_desired_revisions TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_commands TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_command_outcomes TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_observations TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_observation_resources TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_suspension_locks TO app_user;")
    op.execute("GRANT UPDATE (cleared_at, cleared_by) ON mod_hosting.hosting_suspension_locks TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_retention_holds TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_termination_approval_evidence TO app_user;")
    op.execute("GRANT UPDATE (cleared_at, cleared_reason) ON mod_hosting.hosting_retention_holds TO app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_hosting.hosting_attention_conditions TO app_user;")
    op.execute("GRANT UPDATE (resolved_at, resolution_code) ON mod_hosting.hosting_attention_conditions TO app_user;")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS mod_hosting CASCADE;")
