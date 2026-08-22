"""Create the tenant-only fulfillment saga owner.

Revision ID: fu_0001_fulfillment
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "fu_0001_fulfillment"
down_revision = None
branch_labels = ("fulfillment",)
REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_fulfillment"
_TABLES = (
    "fulfillment_runs",
    "fulfillment_steps",
    "fulfillment_attempts",
    "fulfillment_outcome_receipts",
    "fulfillment_compensation_requests",
    "fulfillment_compensation_receipts",
)


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], ["public.tenants.id"], name=name, ondelete="CASCADE"
    )


def _json() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def _secure_append_only_evidence() -> None:
    """Keep every security statement literal so the static gate can inspect it."""
    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_runs ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_fulfillment.fulfillment_runs FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY fulfillment_runs_tenant_isolation ON "
        "mod_fulfillment.fulfillment_runs "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("REVOKE ALL ON mod_fulfillment.fulfillment_runs FROM app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_fulfillment.fulfillment_runs TO app_user;")
    op.execute(
        "CREATE TRIGGER fulfillment_runs_append_only "
        "BEFORE UPDATE OR DELETE ON mod_fulfillment.fulfillment_runs "
        "FOR EACH ROW EXECUTE FUNCTION mod_fulfillment.reject_evidence_rewrite();"
    )

    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_steps ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_steps FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY fulfillment_steps_tenant_isolation ON "
        "mod_fulfillment.fulfillment_steps "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("REVOKE ALL ON mod_fulfillment.fulfillment_steps FROM app_user;")
    op.execute("GRANT SELECT, INSERT ON mod_fulfillment.fulfillment_steps TO app_user;")
    op.execute(
        "CREATE TRIGGER fulfillment_steps_append_only "
        "BEFORE UPDATE OR DELETE ON mod_fulfillment.fulfillment_steps "
        "FOR EACH ROW EXECUTE FUNCTION mod_fulfillment.reject_evidence_rewrite();"
    )

    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_attempts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_attempts FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY fulfillment_attempts_tenant_isolation ON "
        "mod_fulfillment.fulfillment_attempts "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute("REVOKE ALL ON mod_fulfillment.fulfillment_attempts FROM app_user;")
    op.execute(
        "GRANT SELECT, INSERT ON mod_fulfillment.fulfillment_attempts TO app_user;"
    )
    op.execute(
        "CREATE TRIGGER fulfillment_attempts_append_only "
        "BEFORE UPDATE OR DELETE ON mod_fulfillment.fulfillment_attempts "
        "FOR EACH ROW EXECUTE FUNCTION mod_fulfillment.reject_evidence_rewrite();"
    )

    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_outcome_receipts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_outcome_receipts FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY fulfillment_outcome_receipts_tenant_isolation ON "
        "mod_fulfillment.fulfillment_outcome_receipts "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "REVOKE ALL ON mod_fulfillment.fulfillment_outcome_receipts FROM app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_fulfillment.fulfillment_outcome_receipts TO app_user;"
    )
    op.execute(
        "CREATE TRIGGER fulfillment_outcome_receipts_append_only "
        "BEFORE UPDATE OR DELETE ON mod_fulfillment.fulfillment_outcome_receipts "
        "FOR EACH ROW EXECUTE FUNCTION mod_fulfillment.reject_evidence_rewrite();"
    )

    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_compensation_requests ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_compensation_requests FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY fulfillment_compensation_requests_tenant_isolation ON "
        "mod_fulfillment.fulfillment_compensation_requests "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "REVOKE ALL ON mod_fulfillment.fulfillment_compensation_requests "
        "FROM app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_fulfillment.fulfillment_compensation_requests TO app_user;"
    )
    op.execute(
        "CREATE TRIGGER fulfillment_compensation_requests_append_only "
        "BEFORE UPDATE OR DELETE ON mod_fulfillment.fulfillment_compensation_requests "
        "FOR EACH ROW EXECUTE FUNCTION mod_fulfillment.reject_evidence_rewrite();"
    )

    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_compensation_receipts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_fulfillment.fulfillment_compensation_receipts FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY fulfillment_compensation_receipts_tenant_isolation ON "
        "mod_fulfillment.fulfillment_compensation_receipts "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "REVOKE ALL ON mod_fulfillment.fulfillment_compensation_receipts "
        "FROM app_user;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_fulfillment.fulfillment_compensation_receipts TO app_user;"
    )
    op.execute(
        "CREATE TRIGGER fulfillment_compensation_receipts_append_only "
        "BEFORE UPDATE OR DELETE ON mod_fulfillment.fulfillment_compensation_receipts "
        "FOR EACH ROW EXECUTE FUNCTION mod_fulfillment.reject_evidence_rewrite();"
    )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_fulfillment;")
    op.execute("GRANT USAGE ON SCHEMA mod_fulfillment TO app_user, platform_api;")
    op.execute(
        "CREATE FUNCTION mod_fulfillment.reject_evidence_rewrite() "
        "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "RAISE EXCEPTION 'fulfillment evidence is append-only' "
        "USING ERRCODE = '55000'; END; $$;"
    )

    op.create_table(
        "fulfillment_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("intent_ref", sa.String(255), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("correlation_id", sa.String(200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _tenant_fk("fk_fulfillment_runs_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_fulfillment_runs_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "intent_ref", name="uq_fulfillment_runs_tenant_intent"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_fulfillment_runs_tenant_idempotency",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fulfillment_runs_tenant_created",
        "fulfillment_runs",
        ["tenant_id", "created_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "fulfillment_steps",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.String(120), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("participant_code", sa.String(120), nullable=False),
        sa.Column("command_type", sa.String(120), nullable=False),
        sa.Column("line_ref", sa.String(255), nullable=True),
        sa.Column("spec", _json(), nullable=False),
        sa.Column("spec_fingerprint", sa.String(64), nullable=False),
        _tenant_fk("fk_fulfillment_steps_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [
                "mod_fulfillment.fulfillment_runs.tenant_id",
                "mod_fulfillment.fulfillment_runs.id",
            ],
            name="fk_fulfillment_steps_run",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_steps_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "id",
            name="uq_fulfillment_steps_tenant_run_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            name="uq_fulfillment_steps_run_step",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "sequence",
            name="uq_fulfillment_steps_run_sequence",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fulfillment_steps_participant",
        "fulfillment_steps",
        ["tenant_id", "participant_code"],
        schema=_SCHEMA,
    )

    op.create_table(
        "fulfillment_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("command_id", sa.String(200), nullable=False),
        sa.Column("operation_id", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("correlation_id", sa.String(200), nullable=False),
        sa.Column("causation_id", sa.String(200), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("fk_fulfillment_attempts_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id", "step_id"],
            [
                "mod_fulfillment.fulfillment_steps.tenant_id",
                "mod_fulfillment.fulfillment_steps.run_id",
                "mod_fulfillment.fulfillment_steps.id",
            ],
            name="fk_fulfillment_attempts_step",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_attempts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "step_id",
            "sequence",
            name="uq_fulfillment_attempts_step_sequence",
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_fulfillment_attempts_command"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_fulfillment_attempts_idempotency",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fulfillment_attempts_latest",
        "fulfillment_attempts",
        ["tenant_id", "run_id", "step_id", "sequence"],
        schema=_SCHEMA,
    )

    op.create_table(
        "fulfillment_outcome_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_id", sa.Uuid(), nullable=False),
        sa.Column("outcome_id", sa.String(200), nullable=False),
        sa.Column("participant_code", sa.String(120), nullable=False),
        sa.Column("command_id", sa.String(200), nullable=False),
        sa.Column("operation_id", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("classification", sa.String(32), nullable=False),
        sa.Column("provider_status", sa.String(120), nullable=True),
        sa.Column("error_class", sa.String(120), nullable=True),
        sa.Column("reason_code", sa.String(120), nullable=True),
        sa.Column("detail", _json(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reviewed_by_type", sa.String(length=32), nullable=True),
        sa.Column("reviewed_by_id", sa.String(length=120), nullable=True),
        _tenant_fk("fk_fulfillment_outcomes_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "attempt_id"],
            [
                "mod_fulfillment.fulfillment_attempts.tenant_id",
                "mod_fulfillment.fulfillment_attempts.id",
            ],
            name="fk_fulfillment_outcomes_attempt",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_outcomes_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "attempt_id", name="uq_fulfillment_outcomes_attempt"
        ),
        sa.UniqueConstraint(
            "tenant_id", "outcome_id", name="uq_fulfillment_outcomes_identity"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "participant_code",
            "command_id",
            name="uq_fulfillment_outcomes_participant_command",
        ),
        sa.CheckConstraint(
            "classification IN ('succeeded', 'retryable', "
            "'reconciliation_required', 'terminal')",
            name="ck_fulfillment_outcomes_classification",
        ),
        sa.CheckConstraint(
            "(reviewed_by_type IS NULL) = (reviewed_by_id IS NULL)",
            name="ck_fulfillment_outcomes_reviewer_pair",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fulfillment_outcomes_run",
        "fulfillment_outcome_receipts",
        ["tenant_id", "run_id", "step_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "fulfillment_compensation_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("original_attempt_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("participant_code", sa.String(120), nullable=False),
        sa.Column("command_id", sa.String(200), nullable=False),
        sa.Column("operation_id", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("fk_fulfillment_comp_requests_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "original_attempt_id"],
            [
                "mod_fulfillment.fulfillment_attempts.tenant_id",
                "mod_fulfillment.fulfillment_attempts.id",
            ],
            name="fk_fulfillment_comp_requests_attempt",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_comp_requests_tenant_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "original_attempt_id",
            name="uq_fulfillment_comp_requests_attempt",
        ),
        sa.UniqueConstraint(
            "tenant_id", "command_id", name="uq_fulfillment_comp_requests_command"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_fulfillment_comp_requests_idempotency",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fulfillment_comp_requests_run",
        "fulfillment_compensation_requests",
        ["tenant_id", "run_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "fulfillment_compensation_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("outcome_id", sa.String(200), nullable=False),
        sa.Column("participant_code", sa.String(120), nullable=False),
        sa.Column("command_id", sa.String(200), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("disposition", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(120), nullable=True),
        sa.Column("detail", _json(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _tenant_fk("fk_fulfillment_comp_receipts_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "request_id"],
            [
                "mod_fulfillment.fulfillment_compensation_requests.tenant_id",
                "mod_fulfillment.fulfillment_compensation_requests.id",
            ],
            name="fk_fulfillment_comp_receipts_request",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_fulfillment_comp_receipts_tenant_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "request_id", name="uq_fulfillment_comp_receipts_request"
        ),
        sa.UniqueConstraint(
            "tenant_id", "outcome_id", name="uq_fulfillment_comp_receipts_outcome"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "participant_code",
            "command_id",
            name="uq_fulfillment_comp_receipts_participant_command",
        ),
        sa.CheckConstraint(
            "disposition IN ('succeeded', 'refused', 'not_supported', "
            "'manual_required', 'retryable', 'reconciliation_required')",
            name="ck_fulfillment_comp_receipts_disposition",
        ),
        schema=_SCHEMA,
    )

    _secure_append_only_evidence()


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP FUNCTION IF EXISTS mod_fulfillment.reject_evidence_rewrite();")
    op.execute("DROP SCHEMA IF EXISTS mod_fulfillment;")
