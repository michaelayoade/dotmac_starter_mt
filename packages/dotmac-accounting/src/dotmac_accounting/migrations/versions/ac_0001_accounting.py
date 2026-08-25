"""Create the tenant accounting owner.

Revision ID: ac_0001_accounting
Revises: (lineage root)
Create Date: 2026-08-19
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ac_0001_accounting"
down_revision = None
branch_labels = ("accounting",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_accounting"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_accounting;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_accounting TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        "account_categories",
        *_identity("account_categories"),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("account_class", sa.String(40), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        *_tenant_constraints("account_categories"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [
                "mod_accounting.account_categories.tenant_id",
                "mod_accounting.account_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_account_categories_tenant_parent",
        ),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_account_categories_tenant_code"
        ),
        sa.CheckConstraint(
            "account_class IN ('ASSET','LIABILITY','EQUITY','REVENUE','EXPENSE','OTHER_COMPREHENSIVE_INCOME')",
            name="ck_account_categories_class",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_account_categories_tenant_parent",
        "account_categories",
        ["tenant_id", "parent_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "accounts",
        *_identity("accounts"),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("normal_balance", sa.String(12), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "posting_allowed", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        *_timestamps(),
        *_tenant_constraints("accounts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "category_id"],
            [
                "mod_accounting.account_categories.tenant_id",
                "mod_accounting.account_categories.id",
            ],
            ondelete="RESTRICT",
            name="fk_accounts_tenant_category",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["mod_accounting.accounts.tenant_id", "mod_accounting.accounts.id"],
            ondelete="RESTRICT",
            name="fk_accounts_tenant_parent",
        ),
        sa.UniqueConstraint("tenant_id", "code", name="uq_accounts_tenant_code"),
        sa.CheckConstraint(
            "kind IN ('CONTROL','POSTING','STATISTICAL')", name="ck_accounts_kind"
        ),
        sa.CheckConstraint(
            "normal_balance IN ('DEBIT','CREDIT')", name="ck_accounts_normal_balance"
        ),
        sa.CheckConstraint(
            "currency_code IS NULL OR length(currency_code)=3",
            name="ck_accounts_currency_length",
        ),
        sa.CheckConstraint(
            "kind = 'POSTING' OR posting_allowed = false",
            name="ck_accounts_posting_allowed",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_accounts_tenant_category",
        "accounts",
        ["tenant_id", "category_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_accounts_tenant_parent",
        "accounts",
        ["tenant_id", "parent_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "fiscal_years",
        *_identity("fiscal_years"),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        *_timestamps(),
        *_tenant_constraints("fiscal_years"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_fiscal_years_tenant_code"),
        sa.CheckConstraint("start_date <= end_date", name="ck_fiscal_years_date_order"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fiscal_years_tenant_dates",
        "fiscal_years",
        ["tenant_id", "start_date", "end_date"],
        schema=_SCHEMA,
    )

    op.create_table(
        "fiscal_periods",
        *_identity("fiscal_periods"),
        sa.Column("fiscal_year_id", sa.Uuid(), nullable=False),
        sa.Column("period_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "is_adjustment", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("status", sa.String(24), nullable=False, server_default="FUTURE"),
        sa.Column("reopen_token", sa.Uuid(), nullable=True),
        *_timestamps(),
        *_tenant_constraints("fiscal_periods"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "fiscal_year_id"],
            ["mod_accounting.fiscal_years.tenant_id", "mod_accounting.fiscal_years.id"],
            ondelete="RESTRICT",
            name="fk_fiscal_periods_tenant_year",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "fiscal_year_id",
            "period_number",
            name="uq_fiscal_periods_year_number",
        ),
        sa.CheckConstraint(
            "start_date <= end_date", name="ck_fiscal_periods_date_order"
        ),
        sa.CheckConstraint(
            "period_number > 0", name="ck_fiscal_periods_number_positive"
        ),
        sa.CheckConstraint(
            "status IN ('FUTURE','OPEN','SOFT_CLOSED','REOPENED','LOCKED')",
            name="ck_fiscal_periods_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fiscal_periods_tenant_dates",
        "fiscal_periods",
        ["tenant_id", "start_date", "end_date"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_fiscal_periods_tenant_status",
        "fiscal_periods",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "accounting_dimensions",
        *_identity("accounting_dimensions"),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        *_tenant_constraints("accounting_dimensions"),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_accounting_dimensions_tenant_code"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "accounting_dimension_values",
        *_identity("accounting_dimension_values"),
        sa.Column("dimension_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        *_tenant_constraints("accounting_dimension_values"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dimension_id"],
            [
                "mod_accounting.accounting_dimensions.tenant_id",
                "mod_accounting.accounting_dimensions.id",
            ],
            ondelete="RESTRICT",
            name="fk_accounting_dimension_values_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [
                "mod_accounting.accounting_dimension_values.tenant_id",
                "mod_accounting.accounting_dimension_values.id",
            ],
            ondelete="RESTRICT",
            name="fk_accounting_dimension_values_parent",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dimension_id",
            "code",
            name="uq_accounting_dimension_values_code",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_accounting_dimension_values_dimension",
        "accounting_dimension_values",
        ["tenant_id", "dimension_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "journal_entries",
        *_identity("journal_entries"),
        sa.Column("fiscal_period_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("total_debit", sa.Numeric(20, 6), nullable=False),
        sa.Column("total_credit", sa.Numeric(20, 6), nullable=False),
        sa.Column("total_debit_functional", sa.Numeric(20, 6), nullable=False),
        sa.Column("total_credit_functional", sa.Numeric(20, 6), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_document_kind", sa.String(80), nullable=False),
        sa.Column("source_document_id", sa.String(255), nullable=False),
        sa.Column("source_version", sa.String(120), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("approval_reference", sa.String(255), nullable=True),
        sa.Column("posted_by", sa.String(255), nullable=True),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverses_journal_id", sa.Uuid(), nullable=True),
        sa.Column("reversal_journal_id", sa.Uuid(), nullable=True),
        *_timestamps(),
        *_tenant_constraints("journal_entries"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "fiscal_period_id"],
            [
                "mod_accounting.fiscal_periods.tenant_id",
                "mod_accounting.fiscal_periods.id",
            ],
            ondelete="RESTRICT",
            name="fk_journal_entries_tenant_period",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reverses_journal_id"],
            [
                "mod_accounting.journal_entries.tenant_id",
                "mod_accounting.journal_entries.id",
            ],
            ondelete="RESTRICT",
            name="fk_journal_entries_tenant_reverses",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reversal_journal_id"],
            [
                "mod_accounting.journal_entries.tenant_id",
                "mod_accounting.journal_entries.id",
            ],
            ondelete="RESTRICT",
            name="fk_journal_entries_tenant_reversal",
        ),
        sa.UniqueConstraint(
            "tenant_id", "number", name="uq_journal_entries_tenant_number"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_document_kind",
            "source_document_id",
            "source_version",
            name="uq_journal_entries_source_identity",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "reverses_journal_id",
            name="uq_journal_entries_single_reversal",
        ),
        sa.CheckConstraint(
            "kind IN ('STANDARD','ADJUSTMENT','CLOSING','OPENING','REVERSAL')",
            name="ck_journal_entries_kind",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','POSTED','REVERSED','VOID')",
            name="ck_journal_entries_status",
        ),
        sa.CheckConstraint(
            "length(currency_code)=3", name="ck_journal_entries_currency"
        ),
        sa.CheckConstraint(
            "exchange_rate > 0", name="ck_journal_entries_rate_positive"
        ),
        sa.CheckConstraint(
            "total_debit >= 0 AND total_credit >= 0",
            name="ck_journal_entries_totals_nonnegative",
        ),
        sa.CheckConstraint(
            "total_debit_functional >= 0 AND total_credit_functional >= 0",
            name="ck_journal_entries_functional_nonnegative",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_journal_entries_tenant_status",
        "journal_entries",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_journal_entries_tenant_period",
        "journal_entries",
        ["tenant_id", "fiscal_period_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "journal_lines",
        *_identity("journal_lines"),
        sa.Column("journal_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("debit", sa.Numeric(20, 6), nullable=False),
        sa.Column("credit", sa.Numeric(20, 6), nullable=False),
        sa.Column("debit_functional", sa.Numeric(20, 6), nullable=False),
        sa.Column("credit_functional", sa.Numeric(20, 6), nullable=False),
        *_timestamps(),
        *_tenant_constraints("journal_lines"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "journal_id"],
            [
                "mod_accounting.journal_entries.tenant_id",
                "mod_accounting.journal_entries.id",
            ],
            ondelete="CASCADE",
            name="fk_journal_lines_tenant_journal",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["mod_accounting.accounts.tenant_id", "mod_accounting.accounts.id"],
            ondelete="RESTRICT",
            name="fk_journal_lines_tenant_account",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "journal_id",
            "line_number",
            name="uq_journal_lines_journal_number",
        ),
        sa.CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="ck_journal_lines_one_side",
        ),
        sa.CheckConstraint(
            "(debit_functional > 0 AND credit_functional = 0) OR (credit_functional > 0 AND debit_functional = 0)",
            name="ck_journal_lines_functional_one_side",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_journal_lines_tenant_journal",
        "journal_lines",
        ["tenant_id", "journal_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_journal_lines_tenant_account",
        "journal_lines",
        ["tenant_id", "account_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "journal_line_dimensions",
        *_identity("journal_line_dimensions"),
        sa.Column("journal_line_id", sa.Uuid(), nullable=False),
        sa.Column("dimension_id", sa.Uuid(), nullable=False),
        sa.Column("dimension_value_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("journal_line_dimensions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "journal_line_id"],
            [
                "mod_accounting.journal_lines.tenant_id",
                "mod_accounting.journal_lines.id",
            ],
            ondelete="CASCADE",
            name="fk_journal_line_dimensions_tenant_line",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dimension_id"],
            [
                "mod_accounting.accounting_dimensions.tenant_id",
                "mod_accounting.accounting_dimensions.id",
            ],
            ondelete="RESTRICT",
            name="fk_journal_line_dimensions_tenant_dimension",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dimension_value_id"],
            [
                "mod_accounting.accounting_dimension_values.tenant_id",
                "mod_accounting.accounting_dimension_values.id",
            ],
            ondelete="RESTRICT",
            name="fk_journal_line_dimensions_tenant_value",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "journal_line_id",
            "dimension_id",
            name="uq_journal_line_dimensions_one_value",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_journal_line_dimensions_tenant_line",
        "journal_line_dimensions",
        ["tenant_id", "journal_line_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "posted_ledger_lines",
        *_identity("posted_ledger_lines"),
        sa.Column("journal_id", sa.Uuid(), nullable=False),
        sa.Column("journal_line_id", sa.Uuid(), nullable=False),
        sa.Column("fiscal_period_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("account_code", sa.String(40), nullable=False),
        sa.Column("journal_number", sa.String(50), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("posting_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("debit", sa.Numeric(20, 6), nullable=False),
        sa.Column("credit", sa.Numeric(20, 6), nullable=False),
        sa.Column("original_debit", sa.Numeric(20, 6), nullable=False),
        sa.Column("original_credit", sa.Numeric(20, 6), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_document_kind", sa.String(80), nullable=False),
        sa.Column("source_document_id", sa.String(255), nullable=False),
        sa.Column("source_version", sa.String(120), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("posted_by", sa.String(255), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("posted_ledger_lines"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "journal_id"],
            [
                "mod_accounting.journal_entries.tenant_id",
                "mod_accounting.journal_entries.id",
            ],
            ondelete="RESTRICT",
            name="fk_posted_ledger_lines_tenant_journal",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "journal_line_id"],
            [
                "mod_accounting.journal_lines.tenant_id",
                "mod_accounting.journal_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_posted_ledger_lines_tenant_line",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "fiscal_period_id"],
            [
                "mod_accounting.fiscal_periods.tenant_id",
                "mod_accounting.fiscal_periods.id",
            ],
            ondelete="RESTRICT",
            name="fk_posted_ledger_lines_tenant_period",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "account_id"],
            ["mod_accounting.accounts.tenant_id", "mod_accounting.accounts.id"],
            ondelete="RESTRICT",
            name="fk_posted_ledger_lines_tenant_account",
        ),
        sa.UniqueConstraint(
            "tenant_id", "journal_line_id", name="uq_posted_ledger_lines_journal_line"
        ),
        sa.CheckConstraint(
            "(debit > 0 AND credit = 0) OR (credit > 0 AND debit = 0)",
            name="ck_posted_ledger_lines_one_side",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_posted_ledger_lines_tenant_account_period",
        "posted_ledger_lines",
        ["tenant_id", "account_id", "fiscal_period_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_posted_ledger_lines_tenant_posting_date",
        "posted_ledger_lines",
        ["tenant_id", "posting_date"],
        schema=_SCHEMA,
    )

    op.create_table(
        "posted_ledger_dimensions",
        *_identity("posted_ledger_dimensions"),
        sa.Column("ledger_line_id", sa.Uuid(), nullable=False),
        sa.Column("dimension_id", sa.Uuid(), nullable=False),
        sa.Column("dimension_code", sa.String(40), nullable=False),
        sa.Column("dimension_value_id", sa.Uuid(), nullable=False),
        sa.Column("value_code", sa.String(80), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("posted_ledger_dimensions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ledger_line_id"],
            [
                "mod_accounting.posted_ledger_lines.tenant_id",
                "mod_accounting.posted_ledger_lines.id",
            ],
            ondelete="RESTRICT",
            name="fk_posted_ledger_dimensions_tenant_line",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "ledger_line_id",
            "dimension_code",
            name="uq_posted_ledger_dimensions_code",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "period_events",
        *_identity("period_events"),
        sa.Column("period_id", sa.Uuid(), nullable=False),
        sa.Column("event_kind", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(24), nullable=False),
        sa.Column("to_status", sa.String(24), nullable=False),
        sa.Column("actor_ref", sa.String(255), nullable=False),
        sa.Column("approval_reference", sa.String(255), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("period_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "period_id"],
            [
                "mod_accounting.fiscal_periods.tenant_id",
                "mod_accounting.fiscal_periods.id",
            ],
            ondelete="RESTRICT",
            name="fk_period_events_tenant_period",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_period_events_tenant_period",
        "period_events",
        ["tenant_id", "period_id", "occurred_at"],
        schema=_SCHEMA,
    )

    # Serialize fiscal interval creation even for callers bypassing the service.
    op.execute(
        """
        CREATE FUNCTION mod_accounting.protect_fiscal_year_overlap()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || ':fiscal-years', 0));
          IF EXISTS (
            SELECT 1 FROM mod_accounting.fiscal_years existing
            WHERE existing.tenant_id=NEW.tenant_id
              AND existing.id<>NEW.id
              AND daterange(existing.start_date, existing.end_date, '[]') && daterange(NEW.start_date, NEW.end_date, '[]')
          ) THEN
            RAISE EXCEPTION USING ERRCODE='exclusion_violation', MESSAGE='fiscal years cannot overlap';
          END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_accounting.protect_fiscal_year_overlap() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER fiscal_years_no_overlap BEFORE INSERT OR UPDATE OF tenant_id,start_date,end_date ON mod_accounting.fiscal_years FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_fiscal_year_overlap();"
    )

    op.execute(
        """
        CREATE FUNCTION mod_accounting.protect_fiscal_period_overlap()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || ':periods:' || NEW.fiscal_year_id::text, 0));
          IF EXISTS (
            SELECT 1 FROM mod_accounting.fiscal_periods existing
            WHERE existing.tenant_id=NEW.tenant_id
              AND existing.fiscal_year_id=NEW.fiscal_year_id
              AND existing.id<>NEW.id
              AND daterange(existing.start_date, existing.end_date, '[]') && daterange(NEW.start_date, NEW.end_date, '[]')
          ) THEN
            RAISE EXCEPTION USING ERRCODE='exclusion_violation', MESSAGE='fiscal periods cannot overlap';
          END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_accounting.protect_fiscal_period_overlap() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER fiscal_periods_no_overlap BEFORE INSERT OR UPDATE OF tenant_id,fiscal_year_id,start_date,end_date ON mod_accounting.fiscal_periods FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_fiscal_period_overlap();"
    )

    op.execute(
        """
        CREATE FUNCTION mod_accounting.protect_fiscal_period_transition()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'FUTURE' OR NEW.reopen_token IS NOT NULL THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='a fiscal period must begin as future';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.status='LOCKED' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='locked fiscal period is immutable';
          END IF;
          IF OLD.status='FUTURE' AND NEW.status NOT IN ('FUTURE','OPEN') THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid future fiscal period transition';
          END IF;
          IF OLD.status IN ('OPEN','REOPENED') AND NEW.status NOT IN (OLD.status,'SOFT_CLOSED') THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid open fiscal period transition';
          END IF;
          IF OLD.status='SOFT_CLOSED' AND NEW.status NOT IN ('SOFT_CLOSED','REOPENED','LOCKED') THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid soft-closed fiscal period transition';
          END IF;
          IF OLD.status<>'FUTURE'
             AND (to_jsonb(NEW)-ARRAY['status','reopen_token','updated_at'])
               <> (to_jsonb(OLD)-ARRAY['status','reopen_token','updated_at']) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='opened fiscal period definition is immutable';
          END IF;
          IF NEW.status='REOPENED' AND NEW.reopen_token IS NULL THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='reopened fiscal period requires a token';
          END IF;
          IF NEW.status<>'REOPENED' AND NEW.reopen_token IS NOT NULL THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='only a reopened fiscal period carries a token';
          END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_accounting.protect_fiscal_period_transition() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER fiscal_periods_valid_transition BEFORE INSERT OR UPDATE ON mod_accounting.fiscal_periods FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_fiscal_period_transition();"
    )

    op.execute(
        """
        CREATE FUNCTION mod_accounting.protect_posted_journal()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE journal_line_count bigint;
        DECLARE ledger_line_count bigint;
        DECLARE ledger_debit numeric;
        DECLARE ledger_credit numeric;
        DECLARE ledger_original_debit numeric;
        DECLARE ledger_original_credit numeric;
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'DRAFT' THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='a journal must be assembled as draft';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' AND OLD.status<>'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journals cannot be deleted';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='DRAFT' AND NEW.status='POSTED' THEN
            SELECT count(*) INTO journal_line_count
            FROM mod_accounting.journal_lines line
            WHERE line.tenant_id=NEW.tenant_id AND line.journal_id=NEW.id;
            SELECT count(*), COALESCE(sum(debit),0), COALESCE(sum(credit),0),
                   COALESCE(sum(original_debit),0), COALESCE(sum(original_credit),0)
            INTO ledger_line_count, ledger_debit, ledger_credit,
                 ledger_original_debit, ledger_original_credit
            FROM mod_accounting.posted_ledger_lines ledger
            WHERE ledger.tenant_id=NEW.tenant_id AND ledger.journal_id=NEW.id;
            IF journal_line_count<2 OR ledger_line_count<>journal_line_count
               OR ledger_debit<=0 OR ledger_debit<>ledger_credit
               OR ledger_debit<>NEW.total_debit_functional
               OR ledger_credit<>NEW.total_credit_functional
               OR ledger_original_debit<=0
               OR ledger_original_debit<>ledger_original_credit
               OR ledger_original_debit<>NEW.total_debit
               OR ledger_original_credit<>NEW.total_credit
               OR NEW.approval_reference IS NULL OR NEW.posted_by IS NULL OR NEW.posted_at IS NULL THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journal requires complete balanced ledger evidence';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='DRAFT' AND NEW.status NOT IN ('DRAFT','VOID') THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid draft journal transition';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status<>'DRAFT' THEN
            IF OLD.status='POSTED' AND NEW.status='REVERSED'
               AND OLD.reversal_journal_id IS NULL AND NEW.reversal_journal_id IS NOT NULL
               AND EXISTS (
                 SELECT 1 FROM mod_accounting.journal_entries reversal
                 WHERE reversal.tenant_id=OLD.tenant_id
                   AND reversal.id=NEW.reversal_journal_id
                   AND reversal.reverses_journal_id=OLD.id
                   AND reversal.status='POSTED'
               )
               AND (to_jsonb(NEW)-ARRAY['status','reversal_journal_id','updated_at']) = (to_jsonb(OLD)-ARRAY['status','reversal_journal_id','updated_at']) THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journal content is immutable';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_accounting.protect_posted_journal() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER journal_entries_posted_immutable BEFORE INSERT OR UPDATE OR DELETE ON mod_accounting.journal_entries FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_posted_journal();"
    )

    op.execute(
        """
        CREATE FUNCTION mod_accounting.protect_journal_content()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE target_journal uuid;
        DECLARE target_tenant uuid;
        DECLARE current_status text;
        BEGIN
          IF TG_TABLE_NAME='journal_lines' THEN
            IF TG_OP='DELETE' THEN
              target_journal=OLD.journal_id;
              target_tenant=OLD.tenant_id;
            ELSE
              target_journal=NEW.journal_id;
              target_tenant=NEW.tenant_id;
            END IF;
          ELSE
            IF TG_OP='DELETE' THEN
              SELECT line.journal_id, line.tenant_id
              INTO target_journal, target_tenant
              FROM mod_accounting.journal_lines line
              WHERE line.id=OLD.journal_line_id AND line.tenant_id=OLD.tenant_id;
            ELSE
              SELECT line.journal_id, line.tenant_id
              INTO target_journal, target_tenant
              FROM mod_accounting.journal_lines line
              WHERE line.id=NEW.journal_line_id AND line.tenant_id=NEW.tenant_id;
            END IF;
          END IF;
          SELECT status INTO current_status FROM mod_accounting.journal_entries
          WHERE id=target_journal AND tenant_id=target_tenant;
          IF current_status<>'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='posted journal lines are immutable';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_accounting.protect_journal_content() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER journal_lines_posted_immutable BEFORE INSERT OR UPDATE OR DELETE ON mod_accounting.journal_lines FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_journal_content();"
    )
    op.execute(
        "CREATE TRIGGER journal_line_dimensions_posted_immutable BEFORE INSERT OR UPDATE OR DELETE ON mod_accounting.journal_line_dimensions FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_journal_content();"
    )

    op.execute(
        """
        CREATE FUNCTION mod_accounting.protect_immutable_accounting_evidence()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='accounting evidence is append-only';
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_accounting.protect_immutable_accounting_evidence() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER posted_ledger_lines_immutable BEFORE UPDATE OR DELETE ON mod_accounting.posted_ledger_lines FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_immutable_accounting_evidence();"
    )
    op.execute(
        "CREATE TRIGGER posted_ledger_dimensions_immutable BEFORE UPDATE OR DELETE ON mod_accounting.posted_ledger_dimensions FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_immutable_accounting_evidence();"
    )
    op.execute(
        "CREATE TRIGGER period_events_immutable BEFORE UPDATE OR DELETE ON mod_accounting.period_events FOR EACH ROW EXECUTE FUNCTION mod_accounting.protect_immutable_accounting_evidence();"
    )

    op.execute(
        "ALTER TABLE mod_accounting.account_categories ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.account_categories FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY account_categories_tenant_isolation ON mod_accounting.account_categories USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_accounting.account_categories TO app_user;"
    )
    op.execute("ALTER TABLE mod_accounting.accounts ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_accounting.accounts FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY accounts_tenant_isolation ON mod_accounting.accounts USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_accounting.accounts TO app_user;"
    )
    op.execute("ALTER TABLE mod_accounting.fiscal_years ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_accounting.fiscal_years FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY fiscal_years_tenant_isolation ON mod_accounting.fiscal_years USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_accounting.fiscal_years TO app_user;"
    )
    op.execute("ALTER TABLE mod_accounting.fiscal_periods ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_accounting.fiscal_periods FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY fiscal_periods_tenant_isolation ON mod_accounting.fiscal_periods USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_accounting.fiscal_periods TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.accounting_dimensions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.accounting_dimensions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY accounting_dimensions_tenant_isolation ON mod_accounting.accounting_dimensions USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_accounting.accounting_dimensions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.accounting_dimension_values ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.accounting_dimension_values FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY accounting_dimension_values_tenant_isolation ON mod_accounting.accounting_dimension_values USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_accounting.accounting_dimension_values TO app_user;"
    )
    op.execute("ALTER TABLE mod_accounting.journal_entries ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_accounting.journal_entries FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY journal_entries_tenant_isolation ON mod_accounting.journal_entries USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_accounting.journal_entries TO app_user;"
    )
    op.execute("ALTER TABLE mod_accounting.journal_lines ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_accounting.journal_lines FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY journal_lines_tenant_isolation ON mod_accounting.journal_lines USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_accounting.journal_lines TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.journal_line_dimensions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.journal_line_dimensions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY journal_line_dimensions_tenant_isolation ON mod_accounting.journal_line_dimensions USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_accounting.journal_line_dimensions TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.posted_ledger_lines ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.posted_ledger_lines FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY posted_ledger_lines_tenant_isolation ON mod_accounting.posted_ledger_lines USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_accounting.posted_ledger_lines TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.posted_ledger_dimensions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_accounting.posted_ledger_dimensions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY posted_ledger_dimensions_tenant_isolation ON mod_accounting.posted_ledger_dimensions USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_accounting.posted_ledger_dimensions TO app_user;"
    )
    op.execute("ALTER TABLE mod_accounting.period_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_accounting.period_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY period_events_tenant_isolation ON mod_accounting.period_events USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_accounting.period_events TO app_user;")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS period_events_immutable ON mod_accounting.period_events;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS posted_ledger_dimensions_immutable ON mod_accounting.posted_ledger_dimensions;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS posted_ledger_lines_immutable ON mod_accounting.posted_ledger_lines;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS journal_line_dimensions_posted_immutable ON mod_accounting.journal_line_dimensions;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS journal_lines_posted_immutable ON mod_accounting.journal_lines;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS journal_entries_posted_immutable ON mod_accounting.journal_entries;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS fiscal_periods_valid_transition ON mod_accounting.fiscal_periods;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS fiscal_periods_no_overlap ON mod_accounting.fiscal_periods;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS fiscal_years_no_overlap ON mod_accounting.fiscal_years;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mod_accounting.protect_immutable_accounting_evidence();"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_accounting.protect_journal_content();")
    op.execute("DROP FUNCTION IF EXISTS mod_accounting.protect_posted_journal();")
    op.execute(
        "DROP FUNCTION IF EXISTS mod_accounting.protect_fiscal_period_transition();"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mod_accounting.protect_fiscal_period_overlap();"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_accounting.protect_fiscal_year_overlap();")
    for table in (
        "period_events",
        "posted_ledger_dimensions",
        "posted_ledger_lines",
        "journal_line_dimensions",
        "journal_lines",
        "journal_entries",
        "accounting_dimension_values",
        "accounting_dimensions",
        "fiscal_periods",
        "fiscal_years",
        "accounts",
        "account_categories",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_accounting;")
