"""Create tenant fixed-asset books and immutable accounting consequences.

Revision ID: fn_0001_asset_accounting
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "fn_0001_asset_accounting"
down_revision = None
branch_labels = ("finance",)
REQUIRES = ("tenant_scope_catalog.v1", "module_database_roles.v1")
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_finance"
_MONEY = sa.Numeric(20, 6)


def _timestamps() -> tuple[sa.Column[Any], sa.Column[Any]]:
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


def _tenant_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["public.tenants.id"],
        name=name,
        ondelete="CASCADE",
    )


def _tenant_policy(table: str, *, mutable: bool) -> None:
    qualified = f"{_SCHEMA}.{table}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY;")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY;")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {qualified}
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id());
        """
    )
    privileges = "SELECT, INSERT, UPDATE, DELETE" if mutable else "SELECT, INSERT"
    op.execute(f"GRANT {privileges} ON {qualified} TO app_user;")
    op.execute(f"GRANT {privileges} ON {qualified} TO platform_api;")


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_finance;")
    op.execute("GRANT USAGE ON SCHEMA mod_finance TO app_user, platform_api;")

    op.create_table(
        "asset_books",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("book_code", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("accounting_model", sa.String(24), nullable=False),
        sa.Column("depreciation_method", sa.String(32), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("acquisition_cost", _MONEY, nullable=False),
        sa.Column("gross_carrying_amount", _MONEY, nullable=False),
        sa.Column("accumulated_depreciation", _MONEY, nullable=False),
        sa.Column("accumulated_impairment", _MONEY, nullable=False),
        sa.Column("carrying_amount", _MONEY, nullable=False),
        sa.Column("unimpaired_carrying_amount", _MONEY, nullable=False),
        sa.Column("residual_value", _MONEY, nullable=False),
        sa.Column("useful_life_months", sa.Integer(), nullable=False),
        sa.Column("depreciation_periods_taken", sa.Integer(), nullable=False),
        sa.Column("revaluation_reserve_balance", _MONEY, nullable=False),
        sa.Column("prior_revaluation_loss_balance", _MONEY, nullable=False),
        sa.Column("impairment_loss_balance", _MONEY, nullable=False),
        sa.Column("impairment_reserve_reduction_balance", _MONEY, nullable=False),
        sa.Column("available_for_use_on", sa.Date(), nullable=False),
        sa.Column("derecognized_on", sa.Date(), nullable=True),
        sa.Column("asset_account_ref", sa.String(200), nullable=False),
        sa.Column(
            "accumulated_depreciation_account_ref", sa.String(200), nullable=False
        ),
        sa.Column("accumulated_impairment_account_ref", sa.String(200), nullable=False),
        sa.Column("depreciation_expense_account_ref", sa.String(200), nullable=False),
        sa.Column("impairment_loss_account_ref", sa.String(200), nullable=False),
        sa.Column("revaluation_reserve_account_ref", sa.String(200), nullable=True),
        sa.Column("disposal_gain_loss_account_ref", sa.String(200), nullable=False),
        sa.Column("cost_center_ref", sa.String(200), nullable=True),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("source_version", sa.String(80), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        *_timestamps(),
        _tenant_fk("fk_asset_books_tenant"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_asset_books_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "asset_id", "book_code", name="uq_asset_books_asset_book"
        ),
        sa.CheckConstraint(
            "status IN ('active','derecognized')", name="ck_asset_books_status"
        ),
        sa.CheckConstraint(
            "accounting_model IN ('cost','revaluation')",
            name="ck_asset_books_accounting_model",
        ),
        sa.CheckConstraint(
            "depreciation_method IN "
            "('straight_line','declining_balance','double_declining')",
            name="ck_asset_books_depreciation_method",
        ),
        sa.CheckConstraint(
            "currency_code ~ '^[A-Z]{3}$'", name="ck_asset_books_currency"
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_asset_books_minor_units"
        ),
        sa.CheckConstraint(
            "acquisition_cost >= 0 AND gross_carrying_amount >= 0 AND "
            "accumulated_depreciation >= 0 AND accumulated_impairment >= 0 AND "
            "carrying_amount >= 0 AND unimpaired_carrying_amount >= 0 AND "
            "residual_value >= 0",
            name="ck_asset_books_nonnegative_values",
        ),
        sa.CheckConstraint(
            "carrying_amount = gross_carrying_amount - "
            "accumulated_depreciation - accumulated_impairment",
            name="ck_asset_books_carrying_reconciles",
        ),
        sa.CheckConstraint(
            "useful_life_months > 0 AND depreciation_periods_taken >= 0 AND "
            "depreciation_periods_taken <= useful_life_months",
            name="ck_asset_books_life",
        ),
        sa.CheckConstraint("version > 0", name="ck_asset_books_version"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_asset_books_tenant_status",
        "asset_books",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "depreciation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_ref", sa.String(160), nullable=False),
        sa.Column("period_ref", sa.String(160), nullable=False),
        sa.Column("through_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("assets_processed", sa.Integer(), nullable=False),
        sa.Column("total_depreciation", _MONEY, nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_by_id", sa.Uuid(), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        _tenant_fk("fk_depreciation_runs_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_depreciation_runs_tenant_id_id"
        ),
        sa.UniqueConstraint("tenant_id", "run_ref", name="uq_depreciation_runs_ref"),
        sa.CheckConstraint(
            "status IN ('calculated','posted')", name="ck_depreciation_runs_status"
        ),
        sa.CheckConstraint("assets_processed >= 0", name="ck_depreciation_runs_count"),
        sa.CheckConstraint(
            "total_depreciation >= 0", name="ck_depreciation_runs_total"
        ),
        sa.CheckConstraint(
            "currency_code ~ '^[A-Z]{3}$'", name="ck_depreciation_runs_currency"
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_depreciation_runs_minor_units"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_depreciation_runs_period",
        "depreciation_runs",
        ["tenant_id", "period_ref"],
        schema=_SCHEMA,
    )

    op.create_table(
        "depreciation_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("book_version", sa.Integer(), nullable=False),
        sa.Column("periods", sa.Integer(), nullable=False),
        sa.Column("carrying_amount_opening", _MONEY, nullable=False),
        sa.Column("depreciation_amount", _MONEY, nullable=False),
        sa.Column("carrying_amount_closing", _MONEY, nullable=False),
        sa.Column("unimpaired_carrying_opening", _MONEY, nullable=False),
        sa.Column("unimpaired_depreciation_amount", _MONEY, nullable=False),
        sa.Column("unimpaired_carrying_closing", _MONEY, nullable=False),
        sa.Column("remaining_life_opening", sa.Integer(), nullable=False),
        sa.Column("remaining_life_closing", sa.Integer(), nullable=False),
        sa.Column("expense_account_ref", sa.String(200), nullable=False),
        sa.Column(
            "accumulated_depreciation_account_ref", sa.String(200), nullable=False
        ),
        sa.Column("cost_center_ref", sa.String(200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _tenant_fk("fk_depreciation_lines_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            [
                "mod_finance.depreciation_runs.tenant_id",
                "mod_finance.depreciation_runs.id",
            ],
            name="fk_depreciation_lines_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "book_id"],
            ["mod_finance.asset_books.tenant_id", "mod_finance.asset_books.id"],
            name="fk_depreciation_lines_book",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_depreciation_lines_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "run_id", "book_id", name="uq_depreciation_lines_run_book"
        ),
        sa.CheckConstraint(
            "periods > 0 AND book_version > 0", name="ck_depreciation_lines_periods"
        ),
        sa.CheckConstraint(
            "depreciation_amount > 0 AND unimpaired_depreciation_amount >= 0",
            name="ck_depreciation_lines_amounts",
        ),
        sa.CheckConstraint(
            "carrying_amount_opening >= carrying_amount_closing AND "
            "unimpaired_carrying_opening >= unimpaired_carrying_closing AND "
            "remaining_life_opening > remaining_life_closing AND "
            "remaining_life_closing >= 0",
            name="ck_depreciation_lines_closing",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "accounting_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("book_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("source_ref", sa.String(240), nullable=False),
        sa.Column("source_version", sa.String(80), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("approval_ref", sa.String(240), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("carrying_amount_before", _MONEY, nullable=False),
        sa.Column("carrying_amount_after", _MONEY, nullable=False),
        sa.Column("event_data", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("fk_accounting_events_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "book_id"],
            ["mod_finance.asset_books.tenant_id", "mod_finance.asset_books.id"],
            name="fk_accounting_events_book",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_accounting_events_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "book_id", "sequence", name="uq_accounting_events_sequence"
        ),
        sa.CheckConstraint(
            "event_type IN ('capitalized','depreciated','impaired',"
            "'impairment_reversed','revalued','derecognized')",
            name="ck_accounting_events_type",
        ),
        sa.CheckConstraint("sequence > 0", name="ck_accounting_events_sequence"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_accounting_events_book_order",
        "accounting_events",
        ["tenant_id", "book_id", "sequence"],
        schema=_SCHEMA,
    )

    op.create_table(
        "accounting_consequences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("effective_on", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("fk_accounting_consequences_tenant"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_accounting_consequences_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_type",
            "source_id",
            name="uq_accounting_consequences_source",
        ),
        sa.CheckConstraint(
            "source_type IN ('depreciation_run','impairment','revaluation','disposal')",
            name="ck_accounting_consequences_source_type",
        ),
        sa.CheckConstraint(
            "currency_code ~ '^[A-Z]{3}$'",
            name="ck_accounting_consequences_currency",
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6",
            name="ck_accounting_consequences_minor_units",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "accounting_consequence_lines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("consequence_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("account_ref", sa.String(200), nullable=False),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("purpose", sa.String(80), nullable=False),
        sa.Column("cost_center_ref", sa.String(200), nullable=True),
        _tenant_fk("fk_accounting_consequence_lines_tenant"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "consequence_id"],
            [
                "mod_finance.accounting_consequences.tenant_id",
                "mod_finance.accounting_consequences.id",
            ],
            name="fk_accounting_consequence_lines_group",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_accounting_consequence_lines_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "consequence_id",
            "line_number",
            name="uq_accounting_consequence_lines_number",
        ),
        sa.CheckConstraint(
            "line_number > 0", name="ck_accounting_consequence_lines_number"
        ),
        sa.CheckConstraint(
            "side IN ('debit','credit')", name="ck_accounting_consequence_lines_side"
        ),
        sa.CheckConstraint("amount > 0", name="ck_accounting_consequence_lines_amount"),
        schema=_SCHEMA,
    )

    for table in (
        "asset_books",
        "depreciation_runs",
        "depreciation_lines",
        "accounting_events",
        "accounting_consequences",
        "accounting_consequence_lines",
    ):
        _tenant_policy(
            table,
            mutable=table in {"asset_books", "depreciation_runs"},
        )

    op.execute(
        """
        CREATE FUNCTION mod_finance.protect_finance_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'finance accounting evidence is append-only';
        END;
        $$;
        """
    )
    for table in (
        "depreciation_lines",
        "accounting_events",
        "accounting_consequences",
        "accounting_consequence_lines",
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table}_protect_finance_evidence
            BEFORE UPDATE OR DELETE ON mod_finance.{table}
            FOR EACH ROW
            EXECUTE FUNCTION mod_finance.protect_finance_evidence();
            """
        )

    op.execute(
        """
        CREATE FUNCTION mod_finance.assert_balanced_consequence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            target_tenant uuid;
            target_consequence uuid;
            debit_total numeric(20, 6);
            credit_total numeric(20, 6);
        BEGIN
            IF TG_TABLE_NAME = 'accounting_consequences' THEN
                target_tenant := NEW.tenant_id;
                target_consequence := NEW.id;
            ELSE
                target_tenant := NEW.tenant_id;
                target_consequence := NEW.consequence_id;
            END IF;

            SELECT
                COALESCE(sum(amount) FILTER (WHERE side = 'debit'), 0),
                COALESCE(sum(amount) FILTER (WHERE side = 'credit'), 0)
            INTO debit_total, credit_total
            FROM mod_finance.accounting_consequence_lines
            WHERE tenant_id = target_tenant
              AND consequence_id = target_consequence;

            IF debit_total = 0 OR debit_total <> credit_total THEN
                RAISE EXCEPTION
                    'accounting consequence % is unbalanced (debit %, credit %)',
                    target_consequence, debit_total, credit_total;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER accounting_consequences_assert_balanced
        AFTER INSERT ON mod_finance.accounting_consequences
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION mod_finance.assert_balanced_consequence();
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER accounting_consequence_lines_assert_balanced
        AFTER INSERT ON mod_finance.accounting_consequence_lines
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION mod_finance.assert_balanced_consequence();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS accounting_consequence_lines_assert_balanced "
        "ON mod_finance.accounting_consequence_lines;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS accounting_consequences_assert_balanced "
        "ON mod_finance.accounting_consequences;"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_finance.assert_balanced_consequence();")
    for table in (
        "accounting_consequence_lines",
        "accounting_consequences",
        "accounting_events",
        "depreciation_lines",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS {table}_protect_finance_evidence "
            f"ON mod_finance.{table};"
        )
    op.execute("DROP FUNCTION IF EXISTS mod_finance.protect_finance_evidence();")
    op.drop_table("accounting_consequence_lines", schema=_SCHEMA)
    op.drop_table("accounting_consequences", schema=_SCHEMA)
    op.drop_index(
        "ix_accounting_events_book_order",
        table_name="accounting_events",
        schema=_SCHEMA,
    )
    op.drop_table("accounting_events", schema=_SCHEMA)
    op.drop_table("depreciation_lines", schema=_SCHEMA)
    op.drop_index(
        "ix_depreciation_runs_period",
        table_name="depreciation_runs",
        schema=_SCHEMA,
    )
    op.drop_table("depreciation_runs", schema=_SCHEMA)
    op.drop_index(
        "ix_asset_books_tenant_status",
        table_name="asset_books",
        schema=_SCHEMA,
    )
    op.drop_table("asset_books", schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_finance RESTRICT;")
