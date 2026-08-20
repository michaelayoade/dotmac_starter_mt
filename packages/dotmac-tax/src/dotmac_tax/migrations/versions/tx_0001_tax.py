"""Create configurable tax determination, reporting and return evidence.

Revision ID: tx_0001_tax
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "tx_0001_tax"
down_revision = None
branch_labels = ("tax",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_tax"
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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_tax;")
    op.execute("REVOKE ALL ON SCHEMA mod_tax FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_tax TO app_user;")

    op.create_table(
        "tax_authorities",
        *_identity("tax_authorities"),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("authority_level_code", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("tax_authorities"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_tax_authorities_code"),
        sa.CheckConstraint(
            "status IN ('active','retired')", name="ck_tax_authorities_status"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "tax_jurisdictions",
        *_identity("tax_jurisdictions"),
        sa.Column("authority_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
        sa.Column("subdivision_code", sa.String(80), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("tax_jurisdictions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "authority_id"],
            ["mod_tax.tax_authorities.tenant_id", "mod_tax.tax_authorities.id"],
            ondelete="RESTRICT",
            name="fk_tax_jurisdictions_authority",
        ),
        sa.UniqueConstraint("tenant_id", "code", name="uq_tax_jurisdictions_code"),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_tax_jurisdictions_minor_units"
        ),
        sa.CheckConstraint(
            "status IN ('active','retired')", name="ck_tax_jurisdictions_status"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "tax_codes",
        *_identity("tax_codes"),
        sa.Column("jurisdiction_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("tax_kind_code", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("tax_codes"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "jurisdiction_id"],
            ["mod_tax.tax_jurisdictions.tenant_id", "mod_tax.tax_jurisdictions.id"],
            ondelete="RESTRICT",
            name="fk_tax_codes_jurisdiction",
        ),
        sa.UniqueConstraint(
            "tenant_id", "jurisdiction_id", "code", name="uq_tax_codes_code"
        ),
        sa.CheckConstraint(
            "status IN ('active','retired')", name="ck_tax_codes_status"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "tax_rules",
        *_identity("tax_rules"),
        sa.Column("tax_code_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("fact_kind", sa.String(100), nullable=False),
        sa.Column("recognition_basis_code", sa.String(100), nullable=False),
        sa.Column("transaction_side", sa.String(20), nullable=False),
        sa.Column("calculation_method", sa.String(24), nullable=False),
        sa.Column("rate", _RATE, nullable=True),
        sa.Column("fixed_amount", _MONEY, nullable=True),
        sa.Column("inclusive", sa.Boolean(), nullable=False),
        sa.Column("recoverable_rate", _RATE, nullable=False),
        sa.Column("party_category", sa.String(100), nullable=True),
        sa.Column("supply_category", sa.String(100), nullable=True),
        sa.Column("place_code", sa.String(100), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("tax_rules"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tax_code_id"],
            ["mod_tax.tax_codes.tenant_id", "mod_tax.tax_codes.id"],
            ondelete="RESTRICT",
            name="fk_tax_rules_code",
        ),
        sa.UniqueConstraint(
            "tenant_id", "tax_code_id", "version", name="uq_tax_rules_version"
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_tax_rules_effective_dates",
        ),
        sa.CheckConstraint("version > 0", name="ck_tax_rules_version"),
        sa.CheckConstraint(
            "transaction_side IN ('input','output','withholding','liability')",
            name="ck_tax_rules_side",
        ),
        sa.CheckConstraint(
            "calculation_method IN ('percentage','fixed','progressive')",
            name="ck_tax_rules_method",
        ),
        sa.CheckConstraint(
            "recoverable_rate BETWEEN 0 AND 1", name="ck_tax_rules_recovery"
        ),
        sa.CheckConstraint("rate IS NULL OR rate >= 0", name="ck_tax_rules_rate"),
        sa.CheckConstraint(
            "fixed_amount IS NULL OR fixed_amount >= 0",
            name="ck_tax_rules_fixed_amount",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_tax_rules_selection",
        "tax_rules",
        ["tenant_id", "fact_kind", "recognition_basis_code", "effective_from"],
        schema=_SCHEMA,
    )
    op.create_table(
        "tax_rule_bands",
        *_identity("tax_rule_bands"),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("lower_bound", _MONEY, nullable=False),
        sa.Column("upper_bound", _MONEY, nullable=True),
        sa.Column("rate", _RATE, nullable=False),
        *_tenant_constraints("tax_rule_bands"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["mod_tax.tax_rules.tenant_id", "mod_tax.tax_rules.id"],
            ondelete="CASCADE",
            name="fk_tax_rule_bands_rule",
        ),
        sa.UniqueConstraint(
            "tenant_id", "rule_id", "sequence", name="uq_tax_rule_bands_sequence"
        ),
        sa.CheckConstraint("sequence > 0", name="ck_tax_rule_bands_sequence"),
        sa.CheckConstraint("lower_bound >= 0", name="ck_tax_rule_bands_lower"),
        sa.CheckConstraint(
            "upper_bound IS NULL OR upper_bound > lower_bound",
            name="ck_tax_rule_bands_upper",
        ),
        sa.CheckConstraint("rate BETWEEN 0 AND 1", name="ck_tax_rule_bands_rate"),
        schema=_SCHEMA,
    )
    op.create_table(
        "tax_determinations",
        *_identity("tax_determinations"),
        sa.Column("jurisdiction_id", sa.Uuid(), nullable=False),
        sa.Column("tax_code_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("fact_kind", sa.String(100), nullable=False),
        sa.Column("recognition_basis_code", sa.String(100), nullable=False),
        sa.Column("transaction_side", sa.String(20), nullable=False),
        sa.Column("base_amount", _MONEY, nullable=False),
        sa.Column("tax_amount", _MONEY, nullable=False),
        sa.Column("recoverable_amount", _MONEY, nullable=False),
        sa.Column("non_recoverable_amount", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("counterparty_ref", sa.String(240), nullable=True),
        sa.Column("determined_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("tax_determinations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "jurisdiction_id"],
            ["mod_tax.tax_jurisdictions.tenant_id", "mod_tax.tax_jurisdictions.id"],
            ondelete="RESTRICT",
            name="fk_tax_determinations_jurisdiction",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tax_code_id"],
            ["mod_tax.tax_codes.tenant_id", "mod_tax.tax_codes.id"],
            ondelete="RESTRICT",
            name="fk_tax_determinations_code",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "rule_id"],
            ["mod_tax.tax_rules.tenant_id", "mod_tax.tax_rules.id"],
            ondelete="RESTRICT",
            name="fk_tax_determinations_rule",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "source_version",
            name="uq_tax_determinations_source",
        ),
        sa.CheckConstraint(
            "base_amount >= 0 AND tax_amount >= 0",
            name="ck_tax_determinations_amounts",
        ),
        sa.CheckConstraint(
            "recoverable_amount >= 0 AND non_recoverable_amount >= 0 AND "
            "recoverable_amount + non_recoverable_amount = tax_amount",
            name="ck_tax_determinations_recovery",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6",
            name="ck_tax_determinations_minor_units",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_tax_determinations_period",
        "tax_determinations",
        ["tenant_id", "jurisdiction_id", "occurred_on"],
        schema=_SCHEMA,
    )
    op.create_table(
        "tax_determination_lines",
        *_identity("tax_determination_lines"),
        sa.Column("determination_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("taxable_amount", _MONEY, nullable=False),
        sa.Column("rate", _RATE, nullable=True),
        sa.Column("tax_amount", _MONEY, nullable=False),
        *_tenant_constraints("tax_determination_lines"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "determination_id"],
            ["mod_tax.tax_determinations.tenant_id", "mod_tax.tax_determinations.id"],
            ondelete="CASCADE",
            name="fk_tax_determination_lines_determination",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "determination_id",
            "sequence",
            name="uq_tax_determination_lines_sequence",
        ),
        sa.CheckConstraint(
            "taxable_amount >= 0 AND tax_amount >= 0",
            name="ck_tax_determination_lines_amounts",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "statutory_report_definitions",
        *_identity("statutory_report_definitions"),
        sa.Column("jurisdiction_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("payable_box_code", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("statutory_report_definitions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "jurisdiction_id"],
            ["mod_tax.tax_jurisdictions.tenant_id", "mod_tax.tax_jurisdictions.id"],
            ondelete="RESTRICT",
            name="fk_statutory_report_defs_jurisdiction",
        ),
        sa.UniqueConstraint(
            "tenant_id", "jurisdiction_id", "code", name="uq_statutory_report_defs_code"
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6",
            name="ck_statutory_report_defs_minor_units",
        ),
        sa.CheckConstraint(
            "status IN ('active','retired')",
            name="ck_statutory_report_defs_status",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "statutory_report_boxes",
        *_identity("statutory_report_boxes"),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("box_code", sa.String(100), nullable=False),
        sa.Column("label", sa.String(240), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("tax_code_id", sa.Uuid(), nullable=False),
        sa.Column("value_source", sa.String(40), nullable=False),
        sa.Column("multiplier", _RATE, nullable=False),
        *_tenant_constraints("statutory_report_boxes"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "definition_id"],
            [
                "mod_tax.statutory_report_definitions.tenant_id",
                "mod_tax.statutory_report_definitions.id",
            ],
            ondelete="CASCADE",
            name="fk_statutory_report_boxes_definition",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tax_code_id"],
            ["mod_tax.tax_codes.tenant_id", "mod_tax.tax_codes.id"],
            ondelete="RESTRICT",
            name="fk_statutory_report_boxes_code",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "definition_id",
            "box_code",
            name="uq_statutory_report_boxes_code",
        ),
        sa.CheckConstraint(
            "value_source IN ('base_amount','tax_amount','recoverable_amount',"
            "'non_recoverable_amount')",
            name="ck_statutory_report_boxes_source",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "tax_filing_obligations",
        *_identity("tax_filing_obligations"),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_ref", sa.String(180), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("taxpayer_ref", sa.String(240), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *_timestamps(),
        *_tenant_constraints("tax_filing_obligations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "definition_id"],
            [
                "mod_tax.statutory_report_definitions.tenant_id",
                "mod_tax.statutory_report_definitions.id",
            ],
            ondelete="RESTRICT",
            name="fk_tax_filing_obligations_definition",
        ),
        sa.UniqueConstraint(
            "tenant_id", "obligation_ref", name="uq_tax_filing_obligations_ref"
        ),
        sa.CheckConstraint(
            "period_end >= period_start", name="ck_tax_filing_obligations_period"
        ),
        sa.CheckConstraint(
            "status IN ('open','filed','accepted','closed')",
            name="ck_tax_filing_obligations_status",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "statutory_reports",
        *_identity("statutory_reports"),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("total_payable", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("snapshot_ref", sa.String(240), nullable=False),
        sa.Column("generated_by_id", sa.Uuid(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("statutory_reports"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "definition_id"],
            [
                "mod_tax.statutory_report_definitions.tenant_id",
                "mod_tax.statutory_report_definitions.id",
            ],
            ondelete="RESTRICT",
            name="fk_statutory_reports_definition",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                "mod_tax.tax_filing_obligations.tenant_id",
                "mod_tax.tax_filing_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_statutory_reports_obligation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "obligation_id",
            "version",
            name="uq_statutory_reports_obligation_version",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6",
            name="ck_statutory_reports_minor_units",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "statutory_report_values",
        *_identity("statutory_report_values"),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("box_code", sa.String(100), nullable=False),
        sa.Column("label", sa.String(240), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        *_tenant_constraints("statutory_report_values"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "report_id"],
            ["mod_tax.statutory_reports.tenant_id", "mod_tax.statutory_reports.id"],
            ondelete="CASCADE",
            name="fk_statutory_report_values_report",
        ),
        sa.UniqueConstraint(
            "tenant_id", "report_id", "box_code", name="uq_statutory_report_values_box"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "tax_returns",
        *_identity("tax_returns"),
        sa.Column("report_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("report_amount", _MONEY, nullable=False),
        sa.Column("adjustment_amount", _MONEY, nullable=False),
        sa.Column("payable_amount", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prepared_by_id", sa.Uuid(), nullable=True),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filed_by_id", sa.Uuid(), nullable=True),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("filing_reference", sa.String(240), nullable=True),
        sa.Column("authority_reference", sa.String(240), nullable=True),
        sa.Column("original_return_id", sa.Uuid(), nullable=True),
        sa.Column("amendment_reason", sa.String(500), nullable=True),
        *_tenant_constraints("tax_returns"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "report_id"],
            ["mod_tax.statutory_reports.tenant_id", "mod_tax.statutory_reports.id"],
            ondelete="RESTRICT",
            name="fk_tax_returns_report",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                "mod_tax.tax_filing_obligations.tenant_id",
                "mod_tax.tax_filing_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_tax_returns_obligation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "original_return_id"],
            ["mod_tax.tax_returns.tenant_id", "mod_tax.tax_returns.id"],
            ondelete="RESTRICT",
            name="fk_tax_returns_original",
        ),
        sa.UniqueConstraint("tenant_id", "report_id", name="uq_tax_returns_report"),
        sa.CheckConstraint(
            "status IN ('draft','prepared','approved','filed','accepted','rejected','superseded')",
            name="ck_tax_returns_status",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "tax_return_events",
        *_identity("tax_return_events"),
        sa.Column("return_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("authority_reference", sa.String(240), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("tax_return_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "return_id"],
            ["mod_tax.tax_returns.tenant_id", "mod_tax.tax_returns.id"],
            ondelete="RESTRICT",
            name="fk_tax_return_events_return",
        ),
        sa.UniqueConstraint(
            "tenant_id", "return_id", "sequence", name="uq_tax_return_events_sequence"
        ),
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE FUNCTION mod_tax.protect_tax_evidence() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN
            RAISE EXCEPTION 'tax evidence is append-only';
        END; $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_rules FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_rule_bands FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_determinations FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_determination_lines FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.statutory_reports FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.statutory_report_values FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_return_events FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        """
    )

    op.execute(
        """
        ALTER TABLE mod_tax.tax_authorities ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_authorities FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_authorities_tenant_isolation ON mod_tax.tax_authorities USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tax.tax_authorities TO app_user;
        ALTER TABLE mod_tax.tax_jurisdictions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_jurisdictions FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_jurisdictions_tenant_isolation ON mod_tax.tax_jurisdictions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tax.tax_jurisdictions TO app_user;
        ALTER TABLE mod_tax.tax_codes ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_codes FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_codes_tenant_isolation ON mod_tax.tax_codes USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tax.tax_codes TO app_user;
        ALTER TABLE mod_tax.tax_rules ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_rules FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_rules_tenant_isolation ON mod_tax.tax_rules USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_rules TO app_user;
        ALTER TABLE mod_tax.tax_rule_bands ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_rule_bands FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_rule_bands_tenant_isolation ON mod_tax.tax_rule_bands USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_rule_bands TO app_user;
        ALTER TABLE mod_tax.tax_determinations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_determinations FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_determinations_tenant_isolation ON mod_tax.tax_determinations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_determinations TO app_user;
        ALTER TABLE mod_tax.tax_determination_lines ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_determination_lines FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_determination_lines_tenant_isolation ON mod_tax.tax_determination_lines USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_determination_lines TO app_user;
        ALTER TABLE mod_tax.statutory_report_definitions ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.statutory_report_definitions FORCE ROW LEVEL SECURITY;
        CREATE POLICY statutory_report_definitions_tenant_isolation ON mod_tax.statutory_report_definitions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tax.statutory_report_definitions TO app_user;
        ALTER TABLE mod_tax.statutory_report_boxes ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.statutory_report_boxes FORCE ROW LEVEL SECURITY;
        CREATE POLICY statutory_report_boxes_tenant_isolation ON mod_tax.statutory_report_boxes USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tax.statutory_report_boxes TO app_user;
        ALTER TABLE mod_tax.tax_filing_obligations ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_filing_obligations FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_filing_obligations_tenant_isolation ON mod_tax.tax_filing_obligations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tax.tax_filing_obligations TO app_user;
        ALTER TABLE mod_tax.statutory_reports ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.statutory_reports FORCE ROW LEVEL SECURITY;
        CREATE POLICY statutory_reports_tenant_isolation ON mod_tax.statutory_reports USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.statutory_reports TO app_user;
        ALTER TABLE mod_tax.statutory_report_values ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.statutory_report_values FORCE ROW LEVEL SECURITY;
        CREATE POLICY statutory_report_values_tenant_isolation ON mod_tax.statutory_report_values USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.statutory_report_values TO app_user;
        ALTER TABLE mod_tax.tax_returns ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_returns FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_returns_tenant_isolation ON mod_tax.tax_returns USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT, UPDATE, DELETE ON mod_tax.tax_returns TO app_user;
        ALTER TABLE mod_tax.tax_return_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_return_events FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_return_events_tenant_isolation ON mod_tax.tax_return_events USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_return_events TO app_user;
        """
    )


def downgrade() -> None:
    for table in (
        "tax_return_events",
        "tax_returns",
        "statutory_report_values",
        "statutory_reports",
        "tax_filing_obligations",
        "statutory_report_boxes",
        "statutory_report_definitions",
        "tax_determination_lines",
        "tax_determinations",
        "tax_rule_bands",
        "tax_rules",
        "tax_codes",
        "tax_jurisdictions",
        "tax_authorities",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP FUNCTION IF EXISTS mod_tax.protect_tax_evidence();")
    op.execute("DROP SCHEMA IF EXISTS mod_tax;")
