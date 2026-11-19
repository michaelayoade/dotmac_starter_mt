"""Claim liability lifecycle: liabilities, settlements, adjustments, history.

Revision ID: ex_0002_claim_liabilities
Revises: ex_0001_expenses

Purely additive: eight new tenant tables, each with forced row-level security.
No released table, column or byte of ``ex_0001_expenses`` changes.

* ``claim_liabilities`` grants UPDATE on the three projection columns
  (``amount_settled``, ``amount_adjusted``, ``status``) only. A trigger refuses
  any change to the acceptance-bound facts (amount, claim, claimant, account,
  origin, ...), refuses DELETE, makes REVERSED terminal and allows REVERSED
  only from OPEN after the reversal row exists. CHECKs hold the amounts and the
  derived status.
* Every other new table is append-only (the existing a1 evidence trigger
  function) with SELECT/INSERT grants only.
* The kernel idempotency ledger (``idempotency_ledger.v1``) is verified before
  any DDL: every command runs in ``execute_once``.
* The downgrade takes ACCESS EXCLUSIVE locks and refuses once any row exists:
  the rows are permanent financial evidence and recovery is a forward
  correction.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "ex_0002_claim_liabilities"
down_revision = "ex_0001_expenses"
branch_labels = None

# Every a2 command runs in `execute_once`, which writes the kernel idempotency
# ledger at request time. a1 never needed it, so ex_0001 (released bytes, never
# edited) does not name it; this revision proves it before any DDL. The assembly
# binds the effect to whichever revision supplies it; this file names no foreign
# revision.
REQUIRES = ("idempotency_ledger.v1",)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_expenses"
_TENANT_FK = ["public.tenants.id"]
_MONEY = sa.Numeric(20, 6)

# Order matters: parents first (the downgrade drops them in reverse).
_TABLES = (
    "claim_liabilities",
    "claim_liability_lines",
    "claim_liability_settlements",
    "claim_settlement_reversals",
    "claim_liability_adjustments",
    "claim_liability_reversals",
    "historical_claim_receipts",
    "claim_accounting_receipts",
)
_APPEND_ONLY = _TABLES[1:]

_AMOUNTS = (
    "amount > 0 AND amount_settled >= 0 "
    "AND amount + amount_adjusted >= 0 "
    "AND amount_settled <= amount + amount_adjusted"
)
_DERIVED = (
    "(status = 'reversed' AND amount_settled = 0 AND amount_adjusted = 0) "
    "OR (status = 'settled' AND amount_settled = amount + amount_adjusted) "
    "OR (status = 'open' AND amount_settled = 0 "
    "AND amount_settled < amount + amount_adjusted) "
    "OR (status = 'partially_settled' AND amount_settled > 0 "
    "AND amount_settled < amount + amount_adjusted)"
)


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"], _TENANT_FK, ondelete="CASCADE", name=f"fk_{table}_tenant"
    )


def _fk(
    name: str, local: list[str], remote_table: str, remote: list[str]
) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        local,
        [f"{_SCHEMA}.{remote_table}.{column}" for column in remote],
        ondelete="RESTRICT",
        name=name,
    )


def _liability_fk(table: str) -> sa.ForeignKeyConstraint:
    return _fk(
        f"fk_{table}_liability",
        ["tenant_id", "liability_id"],
        "claim_liabilities",
        ["tenant_id", "id"],
    )


def _identity() -> tuple[sa.Column[Any], sa.Column[Any]]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
    )


def _tenant_unique(table: str) -> sa.UniqueConstraint:
    return sa.UniqueConstraint("tenant_id", "id", name=f"uq_{table}_tenant_id_id")


def _create_tables() -> None:
    op.create_table(
        "claim_liabilities",
        *_identity(),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("claimant_party_id", sa.Uuid(), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("liability_account_ref", sa.String(240), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("input_vat_mode", sa.String(16), nullable=True),
        sa.Column("acceptance_reference", sa.String(240), nullable=True),
        sa.Column("accepted_by", sa.String(255), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("amount_settled", _MONEY, nullable=False, server_default="0"),
        sa.Column("amount_adjusted", _MONEY, nullable=False, server_default="0"),
        sa.Column("status", sa.String(24), nullable=False, server_default="open"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        _tenant_fk("claim_liabilities"),
        _tenant_unique("claim_liabilities"),
        sa.UniqueConstraint("tenant_id", "claim_id", name="uq_claim_liabilities_claim"),
        _fk(
            "fk_claim_liabilities_claim",
            ["tenant_id", "claim_id"],
            "expense_claims",
            ["tenant_id", "id"],
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "claimant_party_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="RESTRICT",
            name="fk_claim_liabilities_claimant_party",
        ),
        sa.CheckConstraint(_AMOUNTS, name="ck_claim_liabilities_amounts"),
        sa.CheckConstraint(
            "status IN ('open', 'partially_settled', 'settled', 'reversed')",
            name="ck_claim_liabilities_status",
        ),
        sa.CheckConstraint(_DERIVED, name="ck_claim_liabilities_derived"),
        sa.CheckConstraint(
            "origin IN ('accepted', 'historical')", name="ck_claim_liabilities_origin"
        ),
        sa.CheckConstraint(
            "minor_units BETWEEN 0 AND 6", name="ck_claim_liabilities_minor_units"
        ),
        sa.CheckConstraint(
            "(origin = 'accepted' AND input_vat_mode IN ('none', 'immediate') "
            "AND acceptance_reference IS NOT NULL) "
            "OR (origin = 'historical' AND input_vat_mode IS NULL "
            "AND acceptance_reference IS NULL)",
            name="ck_claim_liabilities_origin_facts",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_claim_liabilities_tenant_claimant",
        "claim_liabilities",
        ["tenant_id", "claimant_party_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "claim_liability_lines",
        *_identity(),
        sa.Column("liability_id", sa.Uuid(), nullable=False),
        sa.Column("claim_line_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("expense_account_ref", sa.String(240), nullable=False),
        sa.Column("dimension_refs", sa.JSON(), nullable=False),
        sa.Column("tax_account_ref", sa.String(240), nullable=True),
        sa.Column("net_amount", _MONEY, nullable=False),
        sa.Column("tax_amount", _MONEY, nullable=False),
        _tenant_fk("claim_liability_lines"),
        _tenant_unique("claim_liability_lines"),
        sa.UniqueConstraint(
            "tenant_id",
            "liability_id",
            "claim_line_id",
            name="uq_claim_liability_lines_claim_line",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "liability_id",
            "line_number",
            name="uq_claim_liability_lines_number",
        ),
        _liability_fk("claim_liability_lines"),
        _fk(
            "fk_claim_liability_lines_claim_line",
            ["tenant_id", "claim_line_id"],
            "expense_claim_lines",
            ["tenant_id", "id"],
        ),
        sa.CheckConstraint("net_amount > 0", name="ck_claim_liability_lines_net"),
        sa.CheckConstraint("tax_amount >= 0", name="ck_claim_liability_lines_tax"),
        sa.CheckConstraint(
            "tax_amount = 0 OR (tax_account_ref IS NOT NULL "
            "AND tax_account_ref <> expense_account_ref)",
            name="ck_claim_liability_lines_tax_account",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "claim_liability_settlements",
        *_identity(),
        sa.Column("liability_id", sa.Uuid(), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("settlement_ref", sa.String(240), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("recorded_by", sa.String(255), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("claim_liability_settlements"),
        _tenant_unique("claim_liability_settlements"),
        sa.UniqueConstraint(
            "tenant_id",
            "liability_id",
            "settlement_ref",
            name="uq_claim_liability_settlements_ref",
        ),
        _liability_fk("claim_liability_settlements"),
        sa.CheckConstraint("amount > 0", name="ck_claim_liability_settlements_amount"),
        schema=_SCHEMA,
    )

    op.create_table(
        "claim_settlement_reversals",
        *_identity(),
        sa.Column("settlement_id", sa.Uuid(), nullable=False),
        sa.Column("liability_id", sa.Uuid(), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("return_ref", sa.String(240), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reversed_by", sa.String(255), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("claim_settlement_reversals"),
        _tenant_unique("claim_settlement_reversals"),
        sa.UniqueConstraint(
            "tenant_id",
            "settlement_id",
            name="uq_claim_settlement_reversals_settlement",
        ),
        _liability_fk("claim_settlement_reversals"),
        _fk(
            "fk_claim_settlement_reversals_settlement",
            ["tenant_id", "settlement_id"],
            "claim_liability_settlements",
            ["tenant_id", "id"],
        ),
        sa.CheckConstraint("amount > 0", name="ck_claim_settlement_reversals_amount"),
        schema=_SCHEMA,
    )

    op.create_table(
        "claim_liability_adjustments",
        *_identity(),
        sa.Column("liability_id", sa.Uuid(), nullable=False),
        sa.Column("amount", _MONEY, nullable=False),
        sa.Column("adjustment_ref", sa.String(240), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("offset_account_ref", sa.String(240), nullable=False),
        sa.Column("adjusted_by", sa.String(255), nullable=False),
        sa.Column("adjusted_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("claim_liability_adjustments"),
        _tenant_unique("claim_liability_adjustments"),
        sa.UniqueConstraint(
            "tenant_id",
            "liability_id",
            "adjustment_ref",
            name="uq_claim_liability_adjustments_ref",
        ),
        _liability_fk("claim_liability_adjustments"),
        sa.CheckConstraint("amount <> 0", name="ck_claim_liability_adjustments_amount"),
        schema=_SCHEMA,
    )

    op.create_table(
        "claim_liability_reversals",
        *_identity(),
        sa.Column("liability_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("reversed_by", sa.String(255), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("claim_liability_reversals"),
        _tenant_unique("claim_liability_reversals"),
        sa.UniqueConstraint(
            "tenant_id", "liability_id", name="uq_claim_liability_reversals_liability"
        ),
        _liability_fk("claim_liability_reversals"),
        schema=_SCHEMA,
    )

    op.create_table(
        "historical_claim_receipts",
        *_identity(),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("liability_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_claim_id", sa.String(255), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(68), nullable=False),
        sa.Column("facts_fingerprint", sa.String(68), nullable=False),
        sa.Column("accounting_evidence_fingerprint", sa.String(68), nullable=False),
        sa.Column("source_journal_ref", sa.String(255), nullable=False),
        sa.Column("carried_evidence_ref", sa.String(255), nullable=False),
        sa.Column("carried_account_ref", sa.String(240), nullable=False),
        sa.Column("evidence_reference", sa.String(255), nullable=False),
        sa.Column("admitted_by", sa.String(255), nullable=False),
        sa.Column("net_payable_amount", _MONEY, nullable=False),
        sa.Column("settled_before_admission", _MONEY, nullable=False),
        sa.Column("residual", _MONEY, nullable=False),
        sa.Column("carried_control_amount", _MONEY, nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("original_posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("historical_claim_receipts"),
        _tenant_unique("historical_claim_receipts"),
        sa.UniqueConstraint(
            "tenant_id", "claim_id", name="uq_historical_claim_receipts_claim"
        ),
        sa.UniqueConstraint(
            "tenant_id", "liability_id", name="uq_historical_claim_receipts_liability"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_claim_id",
            name="uq_historical_claim_receipts_source",
        ),
        _liability_fk("historical_claim_receipts"),
        _fk(
            "fk_historical_claim_receipts_claim",
            ["tenant_id", "claim_id"],
            "expense_claims",
            ["tenant_id", "id"],
        ),
        sa.CheckConstraint(
            "residual > 0", name="ck_historical_claim_receipts_residual"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "claim_accounting_receipts",
        *_identity(),
        sa.Column("document_kind", sa.String(80), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("consequence_fingerprint", sa.String(64), nullable=False),
        sa.Column("accounting_reference", sa.String(255), nullable=False),
        sa.Column("accounting_evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("claim_accounting_receipts"),
        _tenant_unique("claim_accounting_receipts"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_kind",
            "document_id",
            name="uq_claim_accounting_receipts_document",
        ),
        schema=_SCHEMA,
    )


def _install_guard() -> None:
    op.execute(
        """
        CREATE FUNCTION mod_expenses.guard_claim_liability()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation',
              MESSAGE='claim liabilities are permanent financial evidence';
          END IF;
          IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id
             OR NEW.claim_id <> OLD.claim_id
             OR NEW.claimant_party_id <> OLD.claimant_party_id
             OR NEW.currency_code <> OLD.currency_code
             OR NEW.minor_units <> OLD.minor_units
             OR NEW.liability_account_ref <> OLD.liability_account_ref
             OR NEW.origin <> OLD.origin
             OR NEW.input_vat_mode IS DISTINCT FROM OLD.input_vat_mode
             OR NEW.acceptance_reference IS DISTINCT FROM OLD.acceptance_reference
             OR NEW.accepted_by <> OLD.accepted_by
             OR NEW.accepted_at <> OLD.accepted_at
             OR NEW.amount <> OLD.amount
             OR NEW.created_at <> OLD.created_at THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation',
              MESSAGE='claim liability acceptance facts are immutable';
          END IF;
          IF OLD.status = 'reversed' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation',
              MESSAGE='a reversed claim liability is terminal';
          END IF;
          IF NEW.status = 'reversed' AND (
               OLD.status <> 'open' OR OLD.origin <> 'accepted'
               OR NOT EXISTS (
                 SELECT 1 FROM mod_expenses.claim_liability_reversals r
                 WHERE r.tenant_id = NEW.tenant_id AND r.liability_id = NEW.id)) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation',
              MESSAGE='a claim liability is reversed only from open, with its reversal row';
          END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_expenses.guard_claim_liability() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER claim_liabilities_are_guarded "
        "BEFORE UPDATE OR DELETE ON mod_expenses.claim_liabilities "
        "FOR EACH ROW EXECUTE FUNCTION mod_expenses.guard_claim_liability();"
    )


def _install_security() -> None:
    for table in _TABLES:
        op.execute(f"ALTER TABLE mod_expenses.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_expenses.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_expenses.{table} "
            "USING (tenant_id = current_setting('app.current_tenant', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.current_tenant', true)::uuid);"
        )
        op.execute(f"GRANT SELECT, INSERT ON mod_expenses.{table} TO app_user;")
    op.execute(
        "GRANT UPDATE (amount_settled, amount_adjusted, status) "
        "ON mod_expenses.claim_liabilities TO app_user;"
    )
    for table in _APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_are_append_only "
            f"BEFORE UPDATE OR DELETE ON mod_expenses.{table} "
            "FOR EACH ROW EXECUTE FUNCTION "
            "mod_expenses.refuse_expense_evidence_mutation();"
        )


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    _create_tables()
    _install_guard()
    _install_security()


def downgrade() -> None:
    # Liabilities, settlements, adjustments and historical receipts are
    # permanent financial evidence: a schema rollback cannot discard them.
    bind = op.get_bind()
    for table in _TABLES:
        op.execute(f"LOCK TABLE mod_expenses.{table} IN ACCESS EXCLUSIVE MODE")
    for statement in (
        "SELECT EXISTS (SELECT 1 FROM mod_expenses.claim_liabilities)",
        "SELECT EXISTS (SELECT 1 FROM mod_expenses.claim_liability_lines)",
        "SELECT EXISTS (SELECT 1 FROM mod_expenses.claim_liability_settlements)",
        "SELECT EXISTS (SELECT 1 FROM mod_expenses.claim_settlement_reversals)",
        "SELECT EXISTS (SELECT 1 FROM mod_expenses.claim_liability_adjustments)",
        "SELECT EXISTS (SELECT 1 FROM mod_expenses.claim_liability_reversals)",
        "SELECT EXISTS (SELECT 1 FROM mod_expenses.historical_claim_receipts)",
        "SELECT EXISTS (SELECT 1 FROM mod_expenses.claim_accounting_receipts)",
    ):
        if bind.scalar(sa.text(statement)):
            raise RuntimeError(
                "claim liabilities, settlements, adjustments and historical "
                "receipts require forward correction"
            )
    for table in reversed(_TABLES):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP FUNCTION IF EXISTS mod_expenses.guard_claim_liability();")
