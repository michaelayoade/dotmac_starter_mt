"""Create data-driven payroll calculations and employee liabilities.

Revision ID: py_0001_payroll
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "py_0001_payroll"
down_revision = None
branch_labels = ("payroll",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_payroll"
_MONEY = sa.Numeric(20, 6)
_RATE = sa.Numeric(12, 8)


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_payroll;")
    op.execute("REVOKE ALL ON SCHEMA mod_payroll FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_payroll TO app_user;")

    op.create_table(
        "pay_components",
        *_identity("pay_components"),
        sa.Column("component_code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("expense_account_ref", sa.String(240), nullable=True),
        sa.Column("liability_account_ref", sa.String(240), nullable=True),
        sa.Column("liability_destination_ref", sa.String(240), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("pay_components"),
        sa.UniqueConstraint(
            "tenant_id", "component_code", name="uq_pay_components_code"
        ),
        sa.CheckConstraint(
            "kind IN ('earning','deduction','employer_liability','information')",
            name="ck_pay_components_kind",
        ),
        sa.CheckConstraint(
            "status IN ('active','retired')", name="ck_pay_components_status"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "pay_structures",
        *_identity("pay_structures"),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("pay_structures"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_pay_structures_code"),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_pay_structures_minor_units"
        ),
        sa.CheckConstraint(
            "status IN ('active','retired')", name="ck_pay_structures_status"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "pay_structure_revisions",
        *_identity("pay_structure_revisions"),
        sa.Column("structure_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("net_payable_account_ref", sa.String(240), nullable=False),
        sa.Column("published_by_id", sa.Uuid(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("pay_structure_revisions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "structure_id"],
            ["mod_payroll.pay_structures.tenant_id", "mod_payroll.pay_structures.id"],
            ondelete="RESTRICT",
            name="fk_pay_structure_revisions_structure",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "structure_id",
            "version",
            name="uq_pay_structure_revisions_version",
        ),
        sa.CheckConstraint("version > 0", name="ck_pay_structure_revisions_version"),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_pay_structure_revisions_effective",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "pay_structure_rules",
        *_identity("pay_structure_rules"),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("component_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("component_code", sa.String(80), nullable=False),
        sa.Column("component_name", sa.String(240), nullable=False),
        sa.Column("component_kind", sa.String(30), nullable=False),
        sa.Column("expense_account_ref", sa.String(240), nullable=True),
        sa.Column("liability_account_ref", sa.String(240), nullable=True),
        sa.Column("liability_destination_ref", sa.String(240), nullable=True),
        sa.Column("calculation_method", sa.String(24), nullable=False),
        sa.Column("fixed_amount", _MONEY, nullable=True),
        sa.Column("rate", _RATE, nullable=True),
        sa.Column("prorates", sa.Boolean(), nullable=False),
        *_tenant_constraints("pay_structure_rules"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_payroll.pay_structure_revisions.tenant_id",
                "mod_payroll.pay_structure_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_pay_structure_rules_revision",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "component_id"],
            ["mod_payroll.pay_components.tenant_id", "mod_payroll.pay_components.id"],
            ondelete="RESTRICT",
            name="fk_pay_structure_rules_component",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "revision_id",
            "component_id",
            name="uq_pay_structure_rules_component",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "revision_id",
            "sequence",
            name="uq_pay_structure_rules_sequence",
        ),
        sa.CheckConstraint(
            "calculation_method IN ('input','fixed','percentage')",
            name="ck_pay_structure_rules_method",
        ),
        sa.CheckConstraint(
            "rate IS NULL OR rate >= 0", name="ck_pay_structure_rules_rate"
        ),
        sa.CheckConstraint(
            "fixed_amount IS NULL OR fixed_amount >= 0",
            name="ck_pay_structure_rules_fixed_amount",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "pay_rule_bases",
        *_identity("pay_rule_bases"),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("component_id", sa.Uuid(), nullable=False),
        *_tenant_constraints("pay_rule_bases"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            [
                "mod_payroll.pay_structure_rules.tenant_id",
                "mod_payroll.pay_structure_rules.id",
            ],
            ondelete="CASCADE",
            name="fk_pay_rule_bases_rule",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "component_id"],
            ["mod_payroll.pay_components.tenant_id", "mod_payroll.pay_components.id"],
            ondelete="RESTRICT",
            name="fk_pay_rule_bases_component",
        ),
        sa.UniqueConstraint(
            "tenant_id", "rule_id", "component_id", name="uq_pay_rule_bases_component"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "employee_pay_assignments",
        *_identity("employee_pay_assignments"),
        sa.Column("employee_ref", sa.String(240), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("employee_pay_assignments"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_payroll.pay_structure_revisions.tenant_id",
                "mod_payroll.pay_structure_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_employee_pay_assignments_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "employee_ref",
            "effective_from",
            name="uq_employee_pay_assignments_effective",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_employee_pay_assignments_effective",
        ),
        sa.CheckConstraint(
            "status IN ('active','ended')", name="ck_employee_pay_assignments_status"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_employee_pay_assignments_lookup",
        "employee_pay_assignments",
        ["tenant_id", "employee_ref", "effective_from"],
        schema=_SCHEMA,
    )
    op.create_table(
        "payroll_runs",
        *_identity("payroll_runs"),
        sa.Column("run_ref", sa.String(160), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_by_id", sa.Uuid(), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        *_tenant_constraints("payroll_runs"),
        sa.UniqueConstraint("tenant_id", "run_ref", name="uq_payroll_runs_ref"),
        sa.CheckConstraint("period_end >= period_start", name="ck_payroll_runs_period"),
        sa.CheckConstraint(
            "status IN ('draft','approved','finalized','cancelled')",
            name="ck_payroll_runs_status",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_payroll_runs_minor_units"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "payroll_calculations",
        *_identity("payroll_calculations"),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("employee_ref", sa.String(240), nullable=False),
        sa.Column("assignment_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_version", sa.Integer(), nullable=False),
        sa.Column("proration_factor", _RATE, nullable=False),
        sa.Column("gross_amount", _MONEY, nullable=False),
        sa.Column("deduction_amount", _MONEY, nullable=False),
        sa.Column("net_amount", _MONEY, nullable=False),
        sa.Column("employer_liability_amount", _MONEY, nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("payroll_calculations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["mod_payroll.payroll_runs.tenant_id", "mod_payroll.payroll_runs.id"],
            ondelete="RESTRICT",
            name="fk_payroll_calculations_run",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "assignment_id"],
            [
                "mod_payroll.employee_pay_assignments.tenant_id",
                "mod_payroll.employee_pay_assignments.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_calculations_assignment",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_payroll.pay_structure_revisions.tenant_id",
                "mod_payroll.pay_structure_revisions.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_calculations_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "employee_ref",
            name="uq_payroll_calculations_employee",
        ),
        sa.CheckConstraint(
            "proration_factor BETWEEN 0 AND 1", name="ck_payroll_calculations_proration"
        ),
        sa.CheckConstraint(
            "gross_amount >= 0 AND deduction_amount >= 0 AND net_amount >= 0 AND "
            "employer_liability_amount >= 0",
            name="ck_payroll_calculations_amounts",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "payroll_calculation_lines",
        *_identity("payroll_calculation_lines"),
        sa.Column("calculation_id", sa.Uuid(), nullable=False),
        sa.Column("component_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("component_code", sa.String(80), nullable=False),
        sa.Column("component_name", sa.String(240), nullable=False),
        sa.Column("component_kind", sa.String(30), nullable=False),
        sa.Column("calculation_method", sa.String(24), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("expense_account_ref", sa.String(240), nullable=True),
        sa.Column("liability_account_ref", sa.String(240), nullable=True),
        sa.Column("liability_destination_ref", sa.String(240), nullable=True),
        *_tenant_constraints("payroll_calculation_lines"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "calculation_id"],
            [
                "mod_payroll.payroll_calculations.tenant_id",
                "mod_payroll.payroll_calculations.id",
            ],
            ondelete="CASCADE",
            name="fk_payroll_calculation_lines_calculation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "component_id"],
            [
                "mod_payroll.pay_components.tenant_id",
                "mod_payroll.pay_components.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_calculation_lines_component",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "calculation_id",
            "sequence",
            name="uq_payroll_calculation_lines_sequence",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_payroll_calculation_lines_amount"),
        schema=_SCHEMA,
    )
    op.create_table(
        "payroll_liabilities",
        *_identity("payroll_liabilities"),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("calculation_id", sa.Uuid(), nullable=False),
        sa.Column("liability_key", sa.String(180), nullable=False),
        sa.Column("beneficiary_type", sa.String(20), nullable=False),
        sa.Column("beneficiary_ref", sa.String(240), nullable=False),
        sa.Column("component_id", sa.Uuid(), nullable=True),
        sa.Column("liability_account_ref", sa.String(240), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("amount_settled", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("payroll_liabilities"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["mod_payroll.payroll_runs.tenant_id", "mod_payroll.payroll_runs.id"],
            ondelete="RESTRICT",
            name="fk_payroll_liabilities_run",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "calculation_id"],
            [
                "mod_payroll.payroll_calculations.tenant_id",
                "mod_payroll.payroll_calculations.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_liabilities_calculation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "component_id"],
            ["mod_payroll.pay_components.tenant_id", "mod_payroll.pay_components.id"],
            ondelete="RESTRICT",
            name="fk_payroll_liabilities_component",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "calculation_id",
            "liability_key",
            name="uq_payroll_liabilities_key",
        ),
        sa.CheckConstraint(
            "beneficiary_type IN ('employee','external')",
            name="ck_payroll_liabilities_beneficiary",
        ),
        sa.CheckConstraint(
            "status IN ('outstanding','partially_settled','settled')",
            name="ck_payroll_liabilities_status",
        ),
        sa.CheckConstraint(
            "amount >= 0 AND amount_settled >= 0 AND amount_settled <= amount",
            name="ck_payroll_liabilities_amounts",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6",
            name="ck_payroll_liabilities_minor_units",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "payroll_liability_settlements",
        *_identity("payroll_liability_settlements"),
        sa.Column("liability_id", sa.Uuid(), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("settlement_ref", sa.String(240), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("recorded_by_id", sa.Uuid(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("payroll_liability_settlements"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "liability_id"],
            [
                "mod_payroll.payroll_liabilities.tenant_id",
                "mod_payroll.payroll_liabilities.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_liability_settlements_liability",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "liability_id",
            "settlement_ref",
            name="uq_payroll_liability_settlements_ref",
        ),
        sa.CheckConstraint(
            "amount > 0", name="ck_payroll_liability_settlements_amount"
        ),
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_payroll.protect_payroll_evidence() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'payroll evidence is append-only';
        END; $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER protect_payroll_evidence BEFORE UPDATE OR DELETE ON mod_payroll.pay_structure_revisions FOR EACH ROW EXECUTE FUNCTION mod_payroll.protect_payroll_evidence();
        CREATE TRIGGER protect_payroll_evidence BEFORE UPDATE OR DELETE ON mod_payroll.pay_structure_rules FOR EACH ROW EXECUTE FUNCTION mod_payroll.protect_payroll_evidence();
        CREATE TRIGGER protect_payroll_evidence BEFORE UPDATE OR DELETE ON mod_payroll.pay_rule_bases FOR EACH ROW EXECUTE FUNCTION mod_payroll.protect_payroll_evidence();
        CREATE TRIGGER protect_payroll_evidence BEFORE UPDATE OR DELETE ON mod_payroll.payroll_calculations FOR EACH ROW EXECUTE FUNCTION mod_payroll.protect_payroll_evidence();
        CREATE TRIGGER protect_payroll_evidence BEFORE UPDATE OR DELETE ON mod_payroll.payroll_calculation_lines FOR EACH ROW EXECUTE FUNCTION mod_payroll.protect_payroll_evidence();
        CREATE TRIGGER protect_payroll_evidence BEFORE UPDATE OR DELETE ON mod_payroll.payroll_liability_settlements FOR EACH ROW EXECUTE FUNCTION mod_payroll.protect_payroll_evidence();
        """
    )

    op.execute(
        """
        ALTER TABLE mod_payroll.pay_components ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.pay_components FORCE ROW LEVEL SECURITY;
        CREATE POLICY pay_components_tenant_isolation ON mod_payroll.pay_components USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payroll.pay_components TO app_user;
        ALTER TABLE mod_payroll.pay_structures ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.pay_structures FORCE ROW LEVEL SECURITY;
        CREATE POLICY pay_structures_tenant_isolation ON mod_payroll.pay_structures USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payroll.pay_structures TO app_user;
        ALTER TABLE mod_payroll.pay_structure_revisions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.pay_structure_revisions FORCE ROW LEVEL SECURITY;
        CREATE POLICY pay_structure_revisions_tenant_isolation ON mod_payroll.pay_structure_revisions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_payroll.pay_structure_revisions TO app_user;
        ALTER TABLE mod_payroll.pay_structure_rules ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.pay_structure_rules FORCE ROW LEVEL SECURITY;
        CREATE POLICY pay_structure_rules_tenant_isolation ON mod_payroll.pay_structure_rules USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_payroll.pay_structure_rules TO app_user;
        ALTER TABLE mod_payroll.pay_rule_bases ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.pay_rule_bases FORCE ROW LEVEL SECURITY;
        CREATE POLICY pay_rule_bases_tenant_isolation ON mod_payroll.pay_rule_bases USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_payroll.pay_rule_bases TO app_user;
        ALTER TABLE mod_payroll.employee_pay_assignments ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.employee_pay_assignments FORCE ROW LEVEL SECURITY;
        CREATE POLICY employee_pay_assignments_tenant_isolation ON mod_payroll.employee_pay_assignments USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payroll.employee_pay_assignments TO app_user;
        ALTER TABLE mod_payroll.payroll_runs ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.payroll_runs FORCE ROW LEVEL SECURITY;
        CREATE POLICY payroll_runs_tenant_isolation ON mod_payroll.payroll_runs USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payroll.payroll_runs TO app_user;
        ALTER TABLE mod_payroll.payroll_calculations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.payroll_calculations FORCE ROW LEVEL SECURITY;
        CREATE POLICY payroll_calculations_tenant_isolation ON mod_payroll.payroll_calculations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_payroll.payroll_calculations TO app_user;
        ALTER TABLE mod_payroll.payroll_calculation_lines ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.payroll_calculation_lines FORCE ROW LEVEL SECURITY;
        CREATE POLICY payroll_calculation_lines_tenant_isolation ON mod_payroll.payroll_calculation_lines USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_payroll.payroll_calculation_lines TO app_user;
        ALTER TABLE mod_payroll.payroll_liabilities ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.payroll_liabilities FORCE ROW LEVEL SECURITY;
        CREATE POLICY payroll_liabilities_tenant_isolation ON mod_payroll.payroll_liabilities USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payroll.payroll_liabilities TO app_user;
        ALTER TABLE mod_payroll.payroll_liability_settlements ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_payroll.payroll_liability_settlements FORCE ROW LEVEL SECURITY;
        CREATE POLICY payroll_liability_settlements_tenant_isolation ON mod_payroll.payroll_liability_settlements USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_payroll.payroll_liability_settlements TO app_user;
        """
    )


def downgrade() -> None:
    for table in (
        "payroll_liability_settlements",
        "payroll_liabilities",
        "payroll_calculation_lines",
        "payroll_calculations",
        "payroll_runs",
        "employee_pay_assignments",
        "pay_rule_bases",
        "pay_structure_rules",
        "pay_structure_revisions",
        "pay_structures",
        "pay_components",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP FUNCTION IF EXISTS mod_payroll.protect_payroll_evidence();")
    op.execute("DROP SCHEMA IF EXISTS mod_payroll;")
