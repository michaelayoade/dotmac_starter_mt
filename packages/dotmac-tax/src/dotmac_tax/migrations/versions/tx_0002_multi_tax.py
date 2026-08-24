"""Add multi-component tax sets and effective-dated subject classifications.

Revision ID: tx_0002_multi_tax
Revises: tx_0001_tax
Create Date: 2026-08-23
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "tx_0002_multi_tax"
down_revision = "tx_0001_tax"
branch_labels = None
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_tax"
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


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)

    op.add_column(
        "tax_rules",
        sa.Column(
            "treatment_code",
            sa.String(24),
            nullable=False,
            server_default="standard_rated",
        ),
        schema=_SCHEMA,
    )
    op.add_column(
        "tax_rules",
        sa.Column(
            "calculation_sequence",
            sa.Integer(),
            nullable=False,
            server_default="100",
        ),
        schema=_SCHEMA,
    )
    op.add_column(
        "tax_rules",
        sa.Column(
            "calculation_base_code",
            sa.String(32),
            nullable=False,
            server_default="source_amount",
        ),
        schema=_SCHEMA,
    )
    op.alter_column("tax_rules", "treatment_code", server_default=None, schema=_SCHEMA)
    op.alter_column(
        "tax_rules", "calculation_sequence", server_default=None, schema=_SCHEMA
    )
    op.alter_column(
        "tax_rules", "calculation_base_code", server_default=None, schema=_SCHEMA
    )
    op.create_check_constraint(
        "ck_tax_rules_treatment",
        "tax_rules",
        "treatment_code IN " "('standard_rated','zero_rated','exempt','out_of_scope')",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_tax_rules_calculation_sequence",
        "tax_rules",
        "calculation_sequence > 0",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_tax_rules_calculation_base",
        "tax_rules",
        "calculation_base_code IN ('source_amount','source_plus_prior_tax')",
        schema=_SCHEMA,
    )

    op.create_table(
        "tax_subject_classifications",
        *_identity("tax_subject_classifications"),
        sa.Column("tax_code_id", sa.Uuid(), nullable=False),
        sa.Column("subject_kind", sa.String(20), nullable=False),
        sa.Column("subject_ref", sa.String(240), nullable=False),
        sa.Column("category_code", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("basis_code", sa.String(100), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("published_by_ref", sa.String(240), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("tax_subject_classifications"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "tax_code_id"],
            ["mod_tax.tax_codes.tenant_id", "mod_tax.tax_codes.id"],
            ondelete="RESTRICT",
            name="fk_tax_subject_classifications_code",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "tax_code_id",
            "subject_kind",
            "subject_ref",
            "version",
            name="uq_tax_subject_classifications_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "source_version",
            name="uq_tax_subject_classifications_source",
        ),
        sa.CheckConstraint(
            "subject_kind IN ('party','supply','place')",
            name="ck_tax_subject_classifications_kind",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to >= effective_from",
            name="ck_tax_subject_classifications_dates",
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_tax_subject_classifications_version"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_tax_subject_classifications_selection",
        "tax_subject_classifications",
        [
            "tenant_id",
            "tax_code_id",
            "subject_kind",
            "subject_ref",
            "effective_from",
        ],
        schema=_SCHEMA,
    )

    op.create_table(
        "tax_determination_sets",
        *_identity("tax_determination_sets"),
        sa.Column("jurisdiction_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("fact_kind", sa.String(100), nullable=False),
        sa.Column("recognition_basis_code", sa.String(100), nullable=False),
        sa.Column("transaction_side", sa.String(20), nullable=False),
        sa.Column("source_amount", _MONEY, nullable=False),
        sa.Column("net_amount", _MONEY, nullable=False),
        sa.Column("tax_amount", _MONEY, nullable=False),
        sa.Column("gross_amount", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("source_version", sa.String(100), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("counterparty_ref", sa.String(240), nullable=True),
        sa.Column("supply_ref", sa.String(240), nullable=True),
        sa.Column("place_ref", sa.String(240), nullable=True),
        sa.Column("determined_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("tax_determination_sets"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "jurisdiction_id"],
            ["mod_tax.tax_jurisdictions.tenant_id", "mod_tax.tax_jurisdictions.id"],
            ondelete="RESTRICT",
            name="fk_tax_determination_sets_jurisdiction",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_ref",
            "source_version",
            name="uq_tax_determination_sets_source",
        ),
        sa.CheckConstraint(
            "source_amount >= 0 AND net_amount >= 0 AND tax_amount >= 0 "
            "AND gross_amount >= 0",
            name="ck_tax_determination_sets_amounts",
        ),
        sa.CheckConstraint(
            "gross_amount = net_amount + tax_amount",
            name="ck_tax_determination_sets_total",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6",
            name="ck_tax_determination_sets_minor_units",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_tax_determination_sets_period",
        "tax_determination_sets",
        ["tenant_id", "jurisdiction_id", "occurred_on"],
        schema=_SCHEMA,
    )

    op.drop_constraint(
        "uq_tax_determinations_source",
        "tax_determinations",
        schema=_SCHEMA,
        type_="unique",
    )
    for column in (
        sa.Column("determination_set_id", sa.Uuid(), nullable=True),
        sa.Column("component_sequence", sa.Integer(), nullable=True),
        sa.Column("treatment_code", sa.String(24), nullable=True),
        sa.Column("calculation_base_code", sa.String(32), nullable=True),
        sa.Column("inclusive", sa.Boolean(), nullable=True),
        sa.Column("party_category", sa.String(100), nullable=True),
        sa.Column("supply_category", sa.String(100), nullable=True),
        sa.Column("place_code", sa.String(100), nullable=True),
        sa.Column("party_classification_id", sa.Uuid(), nullable=True),
        sa.Column("supply_classification_id", sa.Uuid(), nullable=True),
        sa.Column("place_classification_id", sa.Uuid(), nullable=True),
    ):
        op.add_column("tax_determinations", column, schema=_SCHEMA)
    op.create_foreign_key(
        "fk_tax_determinations_set",
        "tax_determinations",
        "tax_determination_sets",
        ["tenant_id", "determination_set_id"],
        ["tenant_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="CASCADE",
    )
    for kind in ("party", "supply", "place"):
        op.create_foreign_key(
            f"fk_tax_determinations_{kind}_classification",
            "tax_determinations",
            "tax_subject_classifications",
            ["tenant_id", f"{kind}_classification_id"],
            ["tenant_id", "id"],
            source_schema=_SCHEMA,
            referent_schema=_SCHEMA,
            ondelete="RESTRICT",
        )
    op.create_unique_constraint(
        "uq_tax_determinations_set_sequence",
        "tax_determinations",
        ["tenant_id", "determination_set_id", "component_sequence"],
        schema=_SCHEMA,
    )
    op.create_unique_constraint(
        "uq_tax_determinations_set_code",
        "tax_determinations",
        ["tenant_id", "determination_set_id", "tax_code_id"],
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_tax_determinations_treatment",
        "tax_determinations",
        "treatment_code IN " "('standard_rated','zero_rated','exempt','out_of_scope')",
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_tax_determinations_calculation_base",
        "tax_determinations",
        "calculation_base_code IN ('source_amount','source_plus_prior_tax')",
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_subject_classifications FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();
        CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE ON mod_tax.tax_determination_sets FOR EACH ROW EXECUTE FUNCTION mod_tax.protect_tax_evidence();

        ALTER TABLE mod_tax.tax_subject_classifications ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_subject_classifications FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_subject_classifications_tenant_isolation ON mod_tax.tax_subject_classifications USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_subject_classifications TO app_user;

        ALTER TABLE mod_tax.tax_determination_sets ENABLE ROW LEVEL SECURITY;
        ALTER TABLE mod_tax.tax_determination_sets FORCE ROW LEVEL SECURITY;
        CREATE POLICY tax_determination_sets_tenant_isolation ON mod_tax.tax_determination_sets USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON mod_tax.tax_determination_sets TO app_user;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS protect_tax_evidence " "ON mod_tax.tax_determinations"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS protect_tax_evidence "
        "ON mod_tax.tax_determination_lines"
    )
    op.execute(
        "DELETE FROM mod_tax.tax_determinations "
        "WHERE determination_set_id IS NOT NULL"
    )
    for kind in ("party", "supply", "place"):
        op.drop_constraint(
            f"fk_tax_determinations_{kind}_classification",
            "tax_determinations",
            schema=_SCHEMA,
            type_="foreignkey",
        )
    op.drop_constraint(
        "fk_tax_determinations_set",
        "tax_determinations",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_tax_determinations_set_code",
        "tax_determinations",
        schema=_SCHEMA,
        type_="unique",
    )
    op.drop_constraint(
        "uq_tax_determinations_set_sequence",
        "tax_determinations",
        schema=_SCHEMA,
        type_="unique",
    )
    op.drop_constraint(
        "ck_tax_determinations_calculation_base",
        "tax_determinations",
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_tax_determinations_treatment",
        "tax_determinations",
        schema=_SCHEMA,
        type_="check",
    )
    for column_name in (
        "place_classification_id",
        "supply_classification_id",
        "party_classification_id",
        "place_code",
        "supply_category",
        "party_category",
        "inclusive",
        "calculation_base_code",
        "treatment_code",
        "component_sequence",
        "determination_set_id",
    ):
        op.drop_column("tax_determinations", column_name, schema=_SCHEMA)
    op.create_unique_constraint(
        "uq_tax_determinations_source",
        "tax_determinations",
        ["tenant_id", "source_ref", "source_version"],
        schema=_SCHEMA,
    )

    op.drop_table("tax_determination_sets", schema=_SCHEMA)
    op.drop_table("tax_subject_classifications", schema=_SCHEMA)

    op.drop_constraint(
        "ck_tax_rules_calculation_base",
        "tax_rules",
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_tax_rules_calculation_sequence",
        "tax_rules",
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_tax_rules_treatment",
        "tax_rules",
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_column("tax_rules", "calculation_base_code", schema=_SCHEMA)
    op.drop_column("tax_rules", "calculation_sequence", schema=_SCHEMA)
    op.drop_column("tax_rules", "treatment_code", schema=_SCHEMA)
    op.execute(
        "CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE "
        "ON mod_tax.tax_determinations FOR EACH ROW EXECUTE FUNCTION "
        "mod_tax.protect_tax_evidence()"
    )
    op.execute(
        "CREATE TRIGGER protect_tax_evidence BEFORE UPDATE OR DELETE "
        "ON mod_tax.tax_determination_lines FOR EACH ROW EXECUTE FUNCTION "
        "mod_tax.protect_tax_evidence()"
    )
