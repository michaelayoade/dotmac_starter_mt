"""Create qualification cases, evidence, and decisions.

Revision ID: qu_0001_qualification_evidence
Revises: (lineage root)
Create Date: 2026-08-20
"""

from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "qu_0001_qualification_evidence"
down_revision = None
branch_labels = ("qualification",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)
_SCHEMA = "mod_qual"


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


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_qual;")
    op.execute("REVOKE ALL ON SCHEMA mod_qual FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_qual TO app_user, app_admin;")
    op.create_table(
        "qualification_cases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("subject_reference", sa.String(160), nullable=False),
        sa.Column("specification_reference", sa.String(160), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_qualification_cases_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_qualification_cases_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_qualification_cases_tenant_subject",
        "qualification_cases",
        ["tenant_id", "subject_reference"],
        schema=_SCHEMA,
    )
    op.create_table(
        "qualification_evidence",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(60), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_qualification_evidence_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                "mod_qual.qualification_cases.tenant_id",
                "mod_qual.qualification_cases.id",
            ],
            ondelete="CASCADE",
            name="fk_qualification_evidence_tenant_case",
        ),
        sa.CheckConstraint(
            "valid_until > observed_at", name="ck_qualification_evidence_validity"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_qualification_evidence_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_qualification_evidence_tenant_case_valid",
        "qualification_evidence",
        ["tenant_id", "case_id", "valid_until"],
        schema=_SCHEMA,
    )
    op.create_table(
        "qualification_decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_qualification_decisions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "case_id"],
            [
                "mod_qual.qualification_cases.tenant_id",
                "mod_qual.qualification_cases.id",
            ],
            ondelete="CASCADE",
            name="fk_qualification_decisions_tenant_case",
        ),
        sa.CheckConstraint(
            "outcome IN ('ELIGIBLE', 'INELIGIBLE', 'MANUAL_REVIEW')",
            name="ck_qualification_decisions_outcome",
        ),
        sa.CheckConstraint(
            "expires_at > decided_at", name="ck_qualification_decisions_expiry"
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_qualification_decisions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "case_id", name="uq_qualification_decisions_tenant_case"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_qualification_decisions_tenant_expiry",
        "qualification_decisions",
        ["tenant_id", "expires_at"],
        schema=_SCHEMA,
    )
    op.execute("ALTER TABLE mod_qual.qualification_cases ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_qual.qualification_cases FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY qualification_cases_tenant_isolation ON mod_qual.qualification_cases USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_qual.qualification_cases TO app_user;"
    )
    op.execute("ALTER TABLE mod_qual.qualification_evidence ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_qual.qualification_evidence FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY qualification_evidence_tenant_isolation ON mod_qual.qualification_evidence USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_qual.qualification_evidence TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_qual.qualification_decisions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_qual.qualification_decisions FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY qualification_decisions_tenant_isolation ON mod_qual.qualification_decisions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_qual.qualification_decisions TO app_user;"
    )


def downgrade() -> None:
    for table in (
        "qualification_decisions",
        "qualification_evidence",
        "qualification_cases",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_qual;")
