"""Create configurable tenant banking, matching and reconciliation.

Revision ID: bk_0001_banking
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "bk_0001_banking"
down_revision = None
branch_labels = ("banking",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_banking"
_MONEY = sa.Numeric(20, 6)


def _identity(name: str) -> tuple[sa.Column[Any], ...]:
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


def _timestamps() -> tuple[sa.Column[Any], ...]:
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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_banking;")
    op.execute("REVOKE ALL ON SCHEMA mod_banking FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_banking TO app_user;")

    op.create_table(
        "bank_institutions",
        *_identity("bank_institutions"),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("clearing_code", sa.String(40), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("bank_institutions"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_bank_institutions_code"),
        sa.CheckConstraint(
            "status IN ('active','retired')", name="ck_bank_institutions_status"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "bank_accounts",
        *_identity("bank_accounts"),
        sa.Column("institution_id", sa.Uuid(), nullable=False),
        sa.Column("account_code", sa.String(60), nullable=False),
        sa.Column("account_name", sa.String(200), nullable=False),
        sa.Column("account_identifier", sa.String(100), nullable=False),
        sa.Column("account_type_code", sa.String(60), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("cash_account_ref", sa.String(240), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("bank_accounts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "institution_id"],
            [
                "mod_banking.bank_institutions.tenant_id",
                "mod_banking.bank_institutions.id",
            ],
            ondelete="RESTRICT",
            name="fk_bank_accounts_institution",
        ),
        sa.UniqueConstraint(
            "tenant_id", "account_code", name="uq_bank_accounts_account_code"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "institution_id",
            "account_identifier",
            name="uq_bank_accounts_identifier",
        ),
        sa.CheckConstraint(
            "status IN ('active','suspended','closed')", name="ck_bank_accounts_status"
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_bank_accounts_minor_units"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_bank_accounts_status",
        "bank_accounts",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )
    op.create_table(
        "bank_statements",
        *_identity("bank_statements"),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("statement_ref", sa.String(160), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("opening_balance", _MONEY, nullable=True),
        sa.Column("closing_balance", _MONEY, nullable=True),
        sa.Column("total_credits", _MONEY, nullable=False),
        sa.Column("total_debits", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("total_lines", sa.Integer(), nullable=False),
        sa.Column("matched_lines", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("imported_by_id", sa.Uuid(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("bank_statements"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["mod_banking.bank_accounts.tenant_id", "mod_banking.bank_accounts.id"],
            ondelete="RESTRICT",
            name="fk_bank_statements_account",
        ),
        sa.UniqueConstraint(
            "tenant_id", "account_id", "statement_ref", name="uq_bank_statements_ref"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "source_version",
            name="uq_bank_statements_source",
        ),
        sa.CheckConstraint(
            "period_end >= period_start", name="ck_bank_statements_period"
        ),
        sa.CheckConstraint(
            "status IN ('imported','reconciled','closed')",
            name="ck_bank_statements_status",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_bank_statements_minor_units"
        ),
        sa.CheckConstraint(
            "total_credits >= 0 AND total_debits >= 0 AND total_lines >= 0 AND "
            "matched_lines >= 0 AND matched_lines <= total_lines",
            name="ck_bank_statements_totals",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_bank_statements_period",
        "bank_statements",
        ["tenant_id", "account_id", "period_end"],
        schema=_SCHEMA,
    )
    op.create_table(
        "bank_statement_lines",
        *_identity("bank_statement_lines"),
        sa.Column("statement_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("transaction_date", sa.Date(), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("external_ref", sa.String(200), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("reference", sa.String(200), nullable=True),
        sa.Column("counterparty", sa.String(240), nullable=True),
        sa.Column("bank_transaction_code", sa.String(80), nullable=True),
        sa.Column("is_matched", sa.Boolean(), nullable=False),
        sa.Column("matched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("bank_statement_lines"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "statement_id"],
            ["mod_banking.bank_statements.tenant_id", "mod_banking.bank_statements.id"],
            ondelete="CASCADE",
            name="fk_statement_lines_statement",
        ),
        sa.UniqueConstraint(
            "tenant_id", "statement_id", "line_number", name="uq_statement_line_no"
        ),
        sa.CheckConstraint(
            "direction IN ('credit','debit')", name="ck_statement_lines_direction"
        ),
        sa.CheckConstraint("amount > 0", name="ck_statement_lines_amount"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_statement_lines_date",
        "bank_statement_lines",
        ["tenant_id", "transaction_date"],
        schema=_SCHEMA,
    )
    op.create_table(
        "cash_account_observations",
        *_identity("cash_account_observations"),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("direction", sa.String(12), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("reference", sa.String(200), nullable=True),
        sa.Column("counterparty_ref", sa.String(240), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("cash_account_observations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["mod_banking.bank_accounts.tenant_id", "mod_banking.bank_accounts.id"],
            ondelete="RESTRICT",
            name="fk_cash_observations_account",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "source_version",
            name="uq_cash_observations_source",
        ),
        sa.CheckConstraint(
            "direction IN ('credit','debit')", name="ck_cash_observations_direction"
        ),
        sa.CheckConstraint("amount > 0", name="ck_cash_observations_amount"),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_cash_observations_minor_units"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_cash_observations_date",
        "cash_account_observations",
        ["tenant_id", "account_id", "effective_on"],
        schema=_SCHEMA,
    )
    op.create_table(
        "match_policies",
        *_identity("match_policies"),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("amount_tolerance", _MONEY, nullable=False),
        sa.Column("date_window_days", sa.Integer(), nullable=False),
        sa.Column("reference_match_mode", sa.String(20), nullable=False),
        sa.Column("amount_weight", sa.Integer(), nullable=False),
        sa.Column("date_weight", sa.Integer(), nullable=False),
        sa.Column("reference_weight", sa.Integer(), nullable=False),
        sa.Column("minimum_confidence", sa.Integer(), nullable=False),
        sa.Column("direction", sa.String(12), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        *_tenant_constraints("match_policies"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_match_policies_code"),
        sa.CheckConstraint("amount_tolerance >= 0", name="ck_match_policy_tolerance"),
        sa.CheckConstraint("date_window_days >= 0", name="ck_match_policy_window"),
        sa.CheckConstraint(
            "reference_match_mode IN ('none','contains','exact')",
            name="ck_match_policy_reference_mode",
        ),
        sa.CheckConstraint(
            "direction IS NULL OR direction IN ('credit','debit')",
            name="ck_match_policy_direction",
        ),
        sa.CheckConstraint(
            "amount_weight + date_weight + reference_weight = 100",
            name="ck_match_policy_weights",
        ),
        sa.CheckConstraint(
            "minimum_confidence BETWEEN 0 AND 100", name="ck_match_policy_confidence"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "match_decisions",
        *_identity("match_decisions"),
        sa.Column("statement_line_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decided_by_id", sa.Uuid(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("match_decisions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "statement_line_id"],
            [
                "mod_banking.bank_statement_lines.tenant_id",
                "mod_banking.bank_statement_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_match_decisions_line",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "policy_id"],
            [
                "mod_banking.match_policies.tenant_id",
                "mod_banking.match_policies.id",
            ],
            ondelete="RESTRICT",
            name="fk_match_decisions_policy",
        ),
        sa.UniqueConstraint(
            "tenant_id", "statement_line_id", name="uq_match_decisions_line"
        ),
        sa.CheckConstraint(
            "status IN ('accepted','reversed')", name="ck_match_decisions_status"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "match_allocations",
        *_identity("match_allocations"),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        *_tenant_constraints("match_allocations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "decision_id"],
            ["mod_banking.match_decisions.tenant_id", "mod_banking.match_decisions.id"],
            ondelete="CASCADE",
            name="fk_match_allocations_decision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                "mod_banking.cash_account_observations.tenant_id",
                "mod_banking.cash_account_observations.id",
            ],
            ondelete="RESTRICT",
            name="fk_match_allocations_observation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "decision_id",
            "observation_id",
            name="uq_match_allocations_pair",
        ),
        sa.CheckConstraint("amount > 0", name="ck_match_allocations_amount"),
        schema=_SCHEMA,
    )
    op.create_table(
        "reconciliations",
        *_identity("reconciliations"),
        sa.Column("statement_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("cash_opening_balance", _MONEY, nullable=False),
        sa.Column("cash_closing_balance", _MONEY, nullable=False),
        sa.Column("statement_closing_balance", _MONEY, nullable=False),
        sa.Column("difference", _MONEY, nullable=False),
        sa.Column("total_lines", sa.Integer(), nullable=False),
        sa.Column("matched_lines", sa.Integer(), nullable=False),
        sa.Column("snapshot_ref", sa.String(240), nullable=False),
        sa.Column("prepared_by_id", sa.Uuid(), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant_constraints("reconciliations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "statement_id"],
            ["mod_banking.bank_statements.tenant_id", "mod_banking.bank_statements.id"],
            ondelete="RESTRICT",
            name="fk_reconciliations_statement",
        ),
        sa.UniqueConstraint(
            "tenant_id", "statement_id", name="uq_reconciliations_statement"
        ),
        sa.CheckConstraint(
            "status IN ('prepared','approved','rejected')",
            name="ck_reconciliations_status",
        ),
        sa.CheckConstraint(
            "total_lines >= 0 AND matched_lines >= 0 AND matched_lines <= total_lines",
            name="ck_reconciliations_counts",
        ),
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_banking.protect_banking_evidence() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'banking evidence is append-only';
        END; $$;
        """
    )
    op.execute(
        "CREATE TRIGGER protect_banking_evidence BEFORE UPDATE OR DELETE ON "
        "mod_banking.cash_account_observations FOR EACH ROW EXECUTE FUNCTION "
        "mod_banking.protect_banking_evidence();"
    )
    op.execute(
        "CREATE TRIGGER protect_banking_evidence BEFORE UPDATE OR DELETE ON "
        "mod_banking.match_allocations FOR EACH ROW EXECUTE FUNCTION "
        "mod_banking.protect_banking_evidence();"
    )

    op.execute(
        """
        ALTER TABLE mod_banking.bank_institutions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_banking.bank_institutions FORCE ROW LEVEL SECURITY;
        CREATE POLICY bank_institutions_tenant_isolation ON mod_banking.bank_institutions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_banking.bank_institutions TO app_user;
        ALTER TABLE mod_banking.bank_accounts ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_banking.bank_accounts FORCE ROW LEVEL SECURITY;
        CREATE POLICY bank_accounts_tenant_isolation ON mod_banking.bank_accounts USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_banking.bank_accounts TO app_user;
        ALTER TABLE mod_banking.bank_statements ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_banking.bank_statements FORCE ROW LEVEL SECURITY;
        CREATE POLICY bank_statements_tenant_isolation ON mod_banking.bank_statements USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_banking.bank_statements TO app_user;
        ALTER TABLE mod_banking.bank_statement_lines ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_banking.bank_statement_lines FORCE ROW LEVEL SECURITY;
        CREATE POLICY bank_statement_lines_tenant_isolation ON mod_banking.bank_statement_lines USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_banking.bank_statement_lines TO app_user;
        ALTER TABLE mod_banking.cash_account_observations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_banking.cash_account_observations FORCE ROW LEVEL SECURITY;
        CREATE POLICY cash_account_observations_tenant_isolation ON mod_banking.cash_account_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_banking.cash_account_observations TO app_user;
        ALTER TABLE mod_banking.match_policies ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_banking.match_policies FORCE ROW LEVEL SECURITY;
        CREATE POLICY match_policies_tenant_isolation ON mod_banking.match_policies USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_banking.match_policies TO app_user;
        ALTER TABLE mod_banking.match_decisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_banking.match_decisions FORCE ROW LEVEL SECURITY;
        CREATE POLICY match_decisions_tenant_isolation ON mod_banking.match_decisions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_banking.match_decisions TO app_user;
        ALTER TABLE mod_banking.match_allocations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_banking.match_allocations FORCE ROW LEVEL SECURITY;
        CREATE POLICY match_allocations_tenant_isolation ON mod_banking.match_allocations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_banking.match_allocations TO app_user;
        ALTER TABLE mod_banking.reconciliations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_banking.reconciliations FORCE ROW LEVEL SECURITY;
        CREATE POLICY reconciliations_tenant_isolation ON mod_banking.reconciliations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_banking.reconciliations TO app_user;
        """
    )


def downgrade() -> None:
    for table in (
        "reconciliations",
        "match_allocations",
        "match_decisions",
        "match_policies",
        "cash_account_observations",
        "bank_statement_lines",
        "bank_statements",
        "bank_accounts",
        "bank_institutions",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP FUNCTION IF EXISTS mod_banking.protect_banking_evidence();")
    op.execute("DROP SCHEMA IF EXISTS mod_banking;")
