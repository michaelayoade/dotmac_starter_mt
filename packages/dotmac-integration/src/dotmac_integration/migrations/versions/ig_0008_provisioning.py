"""Approval-bound provisioning commands, steps and immutable evidence.

All five tables are control-plane state: no ``tenant_id``, no RLS, grants to
the two platform roles and a table-wide revoke from ``app_user``. Receipts are
append-only in the database as well as in the ORM, so a raw SQL repair cannot
silently rewrite the evidence chain.

Revision ID: ig_0008_provisioning
Revises: ig_0007_idempotency_ledger
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0008_provisioning"
down_revision = "ig_0007_idempotency_ledger"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"


def upgrade() -> None:
    op.add_column(
        "capability_bindings",
        sa.Column("capability_instance_ref", sa.String(200), nullable=True),
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "uq_capability_bindings_installation_capability",
        "capability_bindings",
        schema=_SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_capability_bindings_installation_capability_instance",
        "capability_bindings",
        ["installation_id", "capability_id", "capability_instance_ref"],
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_capability_bindings_instance_ref",
        "capability_bindings",
        "capability_instance_ref IS NULL OR capability_instance_ref ~ "
        "'^[a-z][a-z0-9]*([.-][a-z0-9]+)*$'",
        schema=_SCHEMA,
    )
    op.add_column(
        "connector_installations",
        sa.Column("connector_artifact_digest", sa.String(71), nullable=True),
        schema=_SCHEMA,
    )
    op.create_table(
        "provisioning_operations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("apply_command_id", sa.String(240), nullable=False),
        sa.Column("deployment_ref", sa.String(240), nullable=False),
        sa.Column("capability_id", sa.String(160), nullable=False),
        sa.Column("capability_instance_ref", sa.String(200), nullable=False),
        sa.Column("capability_binding_id", sa.Uuid(), nullable=False),
        sa.Column("desired_state_revision", sa.Integer(), nullable=False),
        sa.Column("desired_state_version_id", sa.Uuid(), nullable=False),
        sa.Column("desired_state_hash", sa.String(71), nullable=False),
        sa.Column("saved_plan_id", sa.Uuid(), nullable=False),
        sa.Column("approval_request_id", sa.Uuid(), nullable=False),
        sa.Column("approval_request_binding_hash", sa.String(71), nullable=False),
        sa.Column("plan_command_id", sa.String(240), nullable=False),
        sa.Column("plan_validation_receipt_id", sa.Uuid(), nullable=False),
        sa.Column("plan_validation_receipt_digest", sa.String(71), nullable=False),
        sa.Column("plan_validation_request_body_digest", sa.String(71), nullable=False),
        sa.Column("module_plan_receipt_hash", sa.String(71), nullable=False),
        sa.Column("profile_version_id", sa.Uuid(), nullable=False),
        sa.Column("profile_code", sa.String(120), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("profile_schema_version", sa.Integer(), nullable=False),
        sa.Column("profile_content_hash", sa.String(71), nullable=False),
        sa.Column("command_schema_version", sa.String(64), nullable=False),
        sa.Column("capability_owner_code", sa.String(120), nullable=False),
        sa.Column("capability_schema_version", sa.Integer(), nullable=False),
        sa.Column("capability_contract_attestation_id", sa.Uuid(), nullable=False),
        sa.Column("capability_contract_digest", sa.String(71), nullable=False),
        sa.Column("capability_operations_json", sa.JSON(), nullable=False),
        sa.Column("capability_schemas_json", sa.JSON(), nullable=False),
        sa.Column("prerequisite_evidence_bindings_json", sa.JSON(), nullable=False),
        sa.Column("prerequisite_receipt_pins_json", sa.JSON(), nullable=False),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("installation_ref", sa.String(160), nullable=False),
        sa.Column("expected_plan_hash", sa.String(71), nullable=False),
        sa.Column("approval_grant_ref", sa.String(240), nullable=False),
        sa.Column("approval_digest", sa.String(71), nullable=False),
        sa.Column("approval_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("artifact_digest", sa.String(128), nullable=False),
        sa.Column("connector_key", sa.String(120), nullable=False),
        sa.Column("connector_version", sa.String(32), nullable=False),
        sa.Column("manifest_digest", sa.String(71), nullable=False),
        sa.Column("config_revision_id", sa.Uuid(), nullable=False),
        sa.Column("config_digest", sa.String(71), nullable=False),
        sa.Column("configuration_snapshot_ref", sa.String(320), nullable=False),
        sa.Column("configuration_schema_version", sa.Integer(), nullable=False),
        sa.Column("configuration_hash", sa.String(71), nullable=False),
        sa.Column("component_artifact_digest", sa.String(71), nullable=True),
        sa.Column("execution_policy_digest", sa.String(71), nullable=False),
        sa.Column("approved_command_template_digest", sa.String(71), nullable=False),
        sa.Column("state", sa.String(32), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["capability_binding_id"],
            [f"{_SCHEMA}.capability_bindings.id"],
            ondelete="RESTRICT",
            name="fk_provisioning_operations_binding",
        ),
        sa.ForeignKeyConstraint(
            ["config_revision_id"],
            [f"{_SCHEMA}.connector_config_revisions.id"],
            ondelete="RESTRICT",
            name="fk_provisioning_operations_config_revision",
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            [f"{_SCHEMA}.connector_installations.id"],
            ondelete="RESTRICT",
            name="fk_provisioning_operations_installation",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'in_flight', 'observing', 'retryable', "
            "'observe_in_flight', 'observe_retryable', 'cancel_in_flight', "
            "'cancel_retryable', "
            "'succeeded', 'terminal', 'reconciliation_required', 'cancelled')",
            name="ck_provisioning_operations_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_provisioning_operations_attempts"
        ),
        sa.CheckConstraint(
            "desired_state_revision >= 1",
            name="ck_provisioning_operations_desired_revision",
        ),
        sa.CheckConstraint(
            "profile_version >= 1 AND profile_schema_version >= 1",
            name="ck_provisioning_operations_profile_versions",
        ),
        sa.CheckConstraint(
            "capability_schema_version >= 1 AND configuration_schema_version >= 1",
            name="ck_provisioning_operations_schema_versions",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_provisioning_operations_due",
        "provisioning_operations",
        ["state", "next_attempt_at", "leased_until"],
        schema=_SCHEMA,
    )

    op.create_table(
        "provisioning_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("step_key", sa.String(160), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("endpoint_code", sa.String(160), nullable=False),
        sa.Column("depends_on_json", sa.JSON(), nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("resolved_input_digest", sa.String(71), nullable=True),
        sa.Column("state", sa.String(32), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_operation_ref", sa.String(320), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            [f"{_SCHEMA}.provisioning_operations.id"],
            ondelete="CASCADE",
            name="fk_provisioning_steps_operation",
        ),
        sa.UniqueConstraint(
            "operation_id", "step_key", name="uq_provisioning_steps_key"
        ),
        sa.UniqueConstraint(
            "operation_id", "ordinal", name="uq_provisioning_steps_ordinal"
        ),
        sa.CheckConstraint("ordinal >= 1", name="ck_provisioning_steps_ordinal"),
        sa.CheckConstraint(
            "state IN ('pending', 'in_flight', 'observing', 'retryable', "
            "'observe_in_flight', 'observe_retryable', 'cancel_in_flight', "
            "'cancel_retryable', "
            "'succeeded', 'terminal', 'reconciliation_required', 'cancelled')",
            name="ck_provisioning_steps_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_provisioning_steps_attempts"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_provisioning_steps_due",
        "provisioning_steps",
        ["operation_id", "state", "next_attempt_at", "leased_until"],
        schema=_SCHEMA,
    )

    op.create_table(
        "provisioning_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("receipt_kind", sa.String(64), nullable=False),
        sa.Column("step_key", sa.String(160), nullable=True),
        sa.Column("provider_operation_ref", sa.String(320), nullable=True),
        sa.Column("previous_receipt_hash", sa.String(71), nullable=True),
        sa.Column("receipt_hash", sa.String(71), nullable=False),
        sa.Column("plan_hash", sa.String(71), nullable=False),
        sa.Column("capability_instance_ref", sa.String(200), nullable=False),
        sa.Column("connector_key", sa.String(120), nullable=False),
        sa.Column("connector_version", sa.String(32), nullable=False),
        sa.Column("manifest_digest", sa.String(71), nullable=False),
        sa.Column("artifact_digest", sa.String(128), nullable=False),
        sa.Column("config_digest", sa.String(71), nullable=False),
        sa.Column("approval_digest", sa.String(71), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            [f"{_SCHEMA}.provisioning_operations.id"],
            ondelete="RESTRICT",
            name="fk_provisioning_receipts_operation",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            [f"{_SCHEMA}.provisioning_steps.id"],
            ondelete="RESTRICT",
            name="fk_provisioning_receipts_step",
        ),
        sa.UniqueConstraint(
            "operation_id", "sequence", name="uq_provisioning_receipts_sequence"
        ),
        sa.UniqueConstraint("receipt_hash", name="uq_provisioning_receipts_hash"),
        sa.CheckConstraint("sequence >= 1", name="ck_provisioning_receipts_sequence"),
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE FUNCTION mod_intg.refuse_provisioning_receipt_mutation() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'provisioning receipts are immutable'; END; $$;"
    )
    op.execute(
        "CREATE TRIGGER provisioning_receipts_immutable "
        "BEFORE UPDATE OR DELETE ON mod_intg.provisioning_receipts "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_intg.refuse_provisioning_receipt_mutation();"
    )

    op.create_table(
        "provisioning_commands",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("command_id", sa.String(240), nullable=False),
        sa.Column("command_kind", sa.String(16), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("operation_id", sa.Uuid(), nullable=True),
        sa.Column("step_id", sa.Uuid(), nullable=True),
        sa.Column("request_json", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(16), server_default="accepted", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            [f"{_SCHEMA}.provisioning_operations.id"],
            ondelete="RESTRICT",
            name="fk_provisioning_commands_operation",
        ),
        sa.ForeignKeyConstraint(
            ["step_id"],
            [f"{_SCHEMA}.provisioning_steps.id"],
            ondelete="RESTRICT",
            name="fk_provisioning_commands_step",
        ),
        sa.UniqueConstraint("command_id", name="uq_provisioning_commands_command_id"),
        sa.CheckConstraint(
            "command_kind IN ('plan', 'apply', 'observe', 'cancel')",
            name="ck_provisioning_commands_kind",
        ),
        sa.CheckConstraint(
            "state IN ('accepted', 'in_flight', 'settled')",
            name="ck_provisioning_commands_state",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_provisioning_commands_operation",
        "provisioning_commands",
        ["operation_id", "created_at"],
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE FUNCTION mod_intg.protect_provisioning_command_identity() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "IF TG_OP = 'DELETE' THEN RAISE EXCEPTION "
        "'provisioning command identities are durable'; END IF; "
        "IF NEW.command_id IS DISTINCT FROM OLD.command_id "
        "OR NEW.command_kind IS DISTINCT FROM OLD.command_kind "
        "OR NEW.command_fingerprint IS DISTINCT FROM OLD.command_fingerprint "
        "OR NEW.operation_id IS DISTINCT FROM OLD.operation_id "
        "OR NEW.step_id IS DISTINCT FROM OLD.step_id "
        "OR NEW.request_json::jsonb IS DISTINCT FROM OLD.request_json::jsonb "
        "OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN "
        "RAISE EXCEPTION 'provisioning command identities are immutable'; "
        "END IF; RETURN NEW; END; $$;"
    )
    op.execute(
        "CREATE TRIGGER provisioning_commands_identity_immutable "
        "BEFORE UPDATE OR DELETE ON mod_intg.provisioning_commands "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_intg.protect_provisioning_command_identity();"
    )

    op.create_table(
        "provisioning_command_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("command_record_id", sa.Uuid(), nullable=False),
        sa.Column("command_id", sa.String(240), nullable=False),
        sa.Column("command_fingerprint", sa.String(64), nullable=False),
        sa.Column("capability_instance_ref", sa.String(200), nullable=False),
        sa.Column("request_body_digest", sa.String(71), nullable=False),
        sa.Column("result_digest", sa.String(71), nullable=False),
        sa.Column("receipt_hash", sa.String(71), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["command_record_id"],
            [f"{_SCHEMA}.provisioning_commands.id"],
            ondelete="RESTRICT",
            name="fk_provisioning_command_receipts_command",
        ),
        sa.UniqueConstraint(
            "command_record_id", name="uq_provisioning_command_receipts_record"
        ),
        sa.UniqueConstraint(
            "command_id", name="uq_provisioning_command_receipts_command_id"
        ),
        sa.UniqueConstraint(
            "receipt_hash", name="uq_provisioning_command_receipts_hash"
        ),
        schema=_SCHEMA,
    )
    op.execute(
        "CREATE TRIGGER provisioning_command_receipts_immutable "
        "BEFORE UPDATE OR DELETE ON mod_intg.provisioning_command_receipts "
        "FOR EACH ROW EXECUTE FUNCTION "
        "mod_intg.refuse_provisioning_receipt_mutation();"
    )

    # Literal statements are deliberate: the composed migration gate audits
    # every declared platform table statically, so a helper must not hide the
    # table names that make the privilege boundary reviewable.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_operations TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_operations TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.provisioning_operations FROM app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_steps TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_steps TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.provisioning_steps FROM app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_receipts TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_receipts TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.provisioning_receipts FROM app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_commands TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_commands TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.provisioning_commands FROM app_user;")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_command_receipts TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.provisioning_command_receipts TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.provisioning_command_receipts FROM app_user;")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER provisioning_command_receipts_immutable "
        "ON mod_intg.provisioning_command_receipts;"
    )
    op.drop_table("provisioning_command_receipts", schema=_SCHEMA)
    op.execute(
        "DROP TRIGGER provisioning_commands_identity_immutable "
        "ON mod_intg.provisioning_commands;"
    )
    op.execute("DROP FUNCTION mod_intg.protect_provisioning_command_identity();")
    op.execute(
        "DROP TRIGGER provisioning_receipts_immutable "
        "ON mod_intg.provisioning_receipts;"
    )
    op.execute("DROP FUNCTION mod_intg.refuse_provisioning_receipt_mutation();")
    op.drop_index(
        "ix_provisioning_commands_operation",
        table_name="provisioning_commands",
        schema=_SCHEMA,
    )
    op.drop_table("provisioning_commands", schema=_SCHEMA)
    op.drop_table("provisioning_receipts", schema=_SCHEMA)
    op.drop_index(
        "ix_provisioning_steps_due", table_name="provisioning_steps", schema=_SCHEMA
    )
    op.drop_table("provisioning_steps", schema=_SCHEMA)
    op.drop_index(
        "ix_provisioning_operations_due",
        table_name="provisioning_operations",
        schema=_SCHEMA,
    )
    op.drop_table("provisioning_operations", schema=_SCHEMA)
    op.drop_column(
        "connector_installations", "connector_artifact_digest", schema=_SCHEMA
    )
    op.drop_constraint(
        "ck_capability_bindings_instance_ref",
        "capability_bindings",
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "uq_capability_bindings_installation_capability_instance",
        "capability_bindings",
        schema=_SCHEMA,
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_capability_bindings_installation_capability",
        "capability_bindings",
        ["installation_id", "capability_id"],
        schema=_SCHEMA,
    )
    op.drop_column("capability_bindings", "capability_instance_ref", schema=_SCHEMA)
