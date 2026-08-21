"""Create tenant AI policy, execution and advisory evidence.

All tenant tables carry ``UNIQUE (tenant_id, id)`` for composite references.
Revision ID: ao_0001_ai_operations
Revises: (lineage root)
Create Date: 2026-08-21
"""
from __future__ import annotations
import sqlalchemy as sa
from alembic import op
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

revision = "ao_0001_ai_operations"; down_revision = None; branch_labels = ("ai_operations",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1"); depends_on = resolve_depends_on(REQUIRES); _SCHEMA = "mod_aiops"


def _tenant_columns() -> list[sa.Column[object]]:
    return [sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False), sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False)]


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES); op.execute("CREATE SCHEMA IF NOT EXISTS mod_aiops;"); op.execute("GRANT USAGE ON SCHEMA mod_aiops TO app_user, app_admin;")
    op.create_table("ai_policies", *_tenant_columns(), sa.Column("code", sa.String(120), nullable=False), sa.Column("title", sa.String(240), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"), sa.UniqueConstraint("tenant_id", "id", name="uq_ai_policies_tenant_id_id"), sa.UniqueConstraint("tenant_id", "code", name="uq_ai_policies_code"), schema=_SCHEMA)
    op.create_table("ai_policy_versions", *_tenant_columns(), sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("allowed_operation_kinds", postgresql.JSONB(), nullable=False), sa.Column("input_contract_ref", sa.String(240), nullable=False), sa.Column("policy_digest", sa.String(64), nullable=False), sa.Column("active", sa.Boolean(), nullable=False), sa.Column("published_at", sa.DateTime(timezone=True), nullable=False), sa.Column("activated_at", sa.DateTime(timezone=True)), sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["tenant_id", "policy_id"], ["mod_aiops.ai_policies.tenant_id", "mod_aiops.ai_policies.id"], ondelete="RESTRICT"), sa.UniqueConstraint("tenant_id", "id", name="uq_ai_policy_versions_tenant_id_id"), sa.UniqueConstraint("tenant_id", "policy_id", "version", name="uq_ai_policy_versions_version"), schema=_SCHEMA)
    op.create_table("ai_operations", *_tenant_columns(), sa.Column("operation_key", sa.String(200), nullable=False), sa.Column("request_fingerprint", sa.String(64), nullable=False), sa.Column("policy_version_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("operation_kind", sa.String(80), nullable=False), sa.Column("input_ref", sa.String(240), nullable=False), sa.Column("input_digest", sa.String(64), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("started_at", sa.DateTime(timezone=True), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["tenant_id", "policy_version_id"], ["mod_aiops.ai_policy_versions.tenant_id", "mod_aiops.ai_policy_versions.id"], ondelete="RESTRICT"), sa.UniqueConstraint("tenant_id", "id", name="uq_ai_operations_tenant_id_id"), sa.UniqueConstraint("tenant_id", "operation_key", name="uq_ai_operations_key"), schema=_SCHEMA)
    op.create_table("ai_execution_attempts", *_tenant_columns(), sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("attempt_key", sa.String(200), nullable=False), sa.Column("observation_digest", sa.String(64), nullable=False), sa.Column("outcome", sa.String(24), nullable=False), sa.Column("output_ref", sa.String(240)), sa.Column("output_digest", sa.String(64)), sa.Column("provider_observation", sa.String(160)), sa.Column("model_observation", sa.String(160)), sa.Column("request_observation", sa.String(200)), sa.Column("error_code", sa.String(120)), sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["tenant_id", "operation_id"], ["mod_aiops.ai_operations.tenant_id", "mod_aiops.ai_operations.id"], ondelete="RESTRICT"), sa.UniqueConstraint("tenant_id", "id", name="uq_ai_execution_attempts_tenant_id_id"), sa.UniqueConstraint("tenant_id", "attempt_key", name="uq_ai_execution_attempts_key"), schema=_SCHEMA)
    op.create_table("ai_insights", *_tenant_columns(), sa.Column("operation_id", postgresql.UUID(as_uuid=True), nullable=False), sa.Column("insight_key", sa.String(200), nullable=False), sa.Column("insight_type", sa.String(120), nullable=False), sa.Column("advisory_value", sa.Text(), nullable=False), sa.Column("confidence", sa.Float()), sa.Column("source_output_digest", sa.String(64), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("acknowledged_by_ref", sa.String(200)), sa.Column("acknowledged_at", sa.DateTime(timezone=True)), sa.Column("action_evidence_ref", sa.String(240)), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.ForeignKeyConstraint(["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["tenant_id", "operation_id"], ["mod_aiops.ai_operations.tenant_id", "mod_aiops.ai_operations.id"], ondelete="RESTRICT"), sa.UniqueConstraint("tenant_id", "id", name="uq_ai_insights_tenant_id_id"), sa.UniqueConstraint("tenant_id", "insight_key", name="uq_ai_insights_key"), schema=_SCHEMA)
    for table in ("ai_policies", "ai_policy_versions", "ai_operations", "ai_execution_attempts", "ai_insights"):
        op.execute(f"ALTER TABLE {_SCHEMA}.{table} ENABLE ROW LEVEL SECURITY;"); op.execute(f"ALTER TABLE {_SCHEMA}.{table} FORCE ROW LEVEL SECURITY;"); op.execute(f"CREATE POLICY {table}_tenant_isolation ON {_SCHEMA}.{table} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"); op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_SCHEMA}.{table} TO app_user, app_admin;")


def downgrade() -> None:
    for table in ("ai_insights", "ai_execution_attempts", "ai_operations", "ai_policy_versions", "ai_policies"): op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_aiops;")
