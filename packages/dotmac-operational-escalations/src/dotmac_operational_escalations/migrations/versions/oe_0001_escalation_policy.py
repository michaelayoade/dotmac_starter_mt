"""Create escalation policies, immutable versions and instances.

Revision ID: oe_0001_escalation_policy
Revises: (lineage root)
Create Date: 2026-08-22
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "oe_0001_escalation_policy"
down_revision = None
branch_labels = ("operational_escalations",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_escalations"

_VERSION_STATES = ("DRAFT", "ACTIVE", "RETIRED")
_STATUSES = ("OPEN", "ACKNOWLEDGED", "RESOLVED", "CANCELLED")
_TENANT_TABLES = (
    "escalation_policies",
    "escalation_policy_versions",
    "escalation_instances",
)


def _timestamps() -> tuple[sa.Column[datetime], sa.Column[datetime]]:
    return (
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_escalations;")
    op.execute("REVOKE ALL ON SCHEMA mod_escalations FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_escalations TO app_user, app_admin;")

    op.create_table(
        "escalation_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("subject_type", sa.String(80), nullable=False),
        sa.Column("trigger", sa.String(120), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_escalation_policies_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_escalation_policies_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_escalation_policies_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_escalation_policies_tenant_subject_trigger",
        "escalation_policies",
        ["tenant_id", "subject_type", "trigger"],
        schema=_SCHEMA,
    )

    op.create_table(
        "escalation_policy_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("channels", sa.JSON(), nullable=False),
        sa.Column("minimum_severity", sa.String(40), nullable=True),
        sa.Column("unowned_after_seconds", sa.Integer(), nullable=True),
        sa.Column("unresolved_after_seconds", sa.Integer(), nullable=True),
        sa.Column("cooldown_seconds", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_escalation_policy_versions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "mod_escalations.escalation_policies.tenant_id",
                "mod_escalations.escalation_policies.id",
            ],
            ondelete="CASCADE",
            name="fk_escalation_policy_versions_tenant_policy",
        ),
        sa.CheckConstraint(
            _in_list("state", _VERSION_STATES),
            name="ck_escalation_policy_versions_state",
        ),
        sa.CheckConstraint(
            "version >= 1", name="ck_escalation_policy_versions_version"
        ),
        sa.CheckConstraint("level >= 1", name="ck_escalation_policy_versions_level"),
        sa.CheckConstraint(
            "cooldown_seconds >= 0", name="ck_escalation_policy_versions_cooldown"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_escalation_policy_versions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "policy_id",
            "version",
            name="uq_escalation_policy_versions_tenant_policy_version",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_escalation_policy_versions_tenant_policy_state",
        "escalation_policy_versions",
        ["tenant_id", "policy_id", "state"],
        schema=_SCHEMA,
    )
    # Exactly one ACTIVE version per policy, enforced in the database rather than
    # by the writer alone: "which terms apply now" must not be resolvable by
    # ordering, and a concurrent activation would otherwise leave two.
    op.create_index(
        "uq_escalation_policy_versions_one_active",
        "escalation_policy_versions",
        ["tenant_id", "policy_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("state = 'ACTIVE'"),
        sqlite_where=sa.text("state = 'ACTIVE'"),
    )

    op.create_table(
        "escalation_instances",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("policy_version_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(80), nullable=False),
        sa.Column("subject_reference", sa.String(160), nullable=False),
        sa.Column("trigger", sa.String(120), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(40), nullable=True),
        sa.Column("dedup_key", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("raised_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_reference", sa.String(160), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("settlement_reason", sa.Text(), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_escalation_instances_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_version_id"],
            [
                "mod_escalations.escalation_policy_versions.tenant_id",
                "mod_escalations.escalation_policy_versions.id",
            ],
            ondelete="RESTRICT",
            name="fk_escalation_instances_tenant_policy_version",
        ),
        sa.CheckConstraint(
            _in_list("status", _STATUSES), name="ck_escalation_instances_status"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_escalation_instances_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "dedup_key", name="uq_escalation_instances_tenant_dedup_key"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_escalation_instances_tenant_subject_status",
        "escalation_instances",
        ["tenant_id", "subject_reference", "status"],
        schema=_SCHEMA,
    )

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE mod_escalations.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_escalations.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_escalations.{table} "
            "USING (tenant_id = public.app_current_tenant_id()) "
            "WITH CHECK (tenant_id = public.app_current_tenant_id());"
        )
        op.execute(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON "
            f"mod_escalations.{table} TO app_user;"
        )


def downgrade() -> None:
    for table in reversed(_TENANT_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_escalations;")
