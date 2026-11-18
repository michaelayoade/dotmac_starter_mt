"""Cash-basis deferred-VAT release, historical deferral position, historical credit.

Revision ID: pa_0006_tax_release_hist_credit
Revises: pa_0005_reversal_settled_history

Purely additive: seven new tenant tables, each with forced row-level security,
append-only triggers and SELECT/INSERT grants only. No released table or column
changes. The downgrade refuses once any row exists (permanent financial evidence;
recovery is a forward correction, as for pa_0003 to pa_0005).

The spec names this revision ``pa_0006_tax_release_history_credit``; that is 34
characters and ``alembic_version.version_num`` is VARCHAR(32), so the slug is
shortened.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "pa_0006_tax_release_hist_credit"
down_revision = "pa_0005_reversal_settled_history"
branch_labels = None
depends_on = None

_SCHEMA = "mod_payables"
_TENANT_FK = ["public.tenants.id"]
_TABLES = (
    "tax_deferral_policies",
    "supplier_payment_tax_releases",
    "historical_tax_deferrals",
    "historical_credit_receipts",
    "historical_credit_settlements",
    "credit_note_tax_splits",
    "credit_application_tax_releases",
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


def upgrade() -> None:
    op.create_table(
        "tax_deferral_policies",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("deferral_account_ref", sa.String(255), nullable=False),
        sa.Column("release_account_ref", sa.String(255), nullable=False),
        sa.Column("minor_units", sa.Integer(), nullable=False),
        sa.Column("declared_by", sa.String(255), nullable=False),
        sa.Column("evidence_reference", sa.String(255), nullable=False),
        sa.Column("declared_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("tax_deferral_policies"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_tax_deferral_policies_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "deferral_account_ref",
            name="uq_tax_deferral_policies_deferral",
        ),
        sa.CheckConstraint(
            "deferral_account_ref <> release_account_ref",
            name="ck_tax_deferral_policies_accounts",
        ),
        sa.CheckConstraint(
            "minor_units >= 0 AND minor_units <= 6",
            name="ck_tax_deferral_policies_minor_units",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "supplier_payment_tax_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("deferral_account_ref", sa.String(255), nullable=False),
        sa.Column("release_account_ref", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("supplier_payment_tax_releases"),
        _fk(
            "fk_supplier_payment_tax_releases_payment",
            ["tenant_id", "payment_id"],
            "supplier_payments",
            ["tenant_id", "id"],
        ),
        _fk(
            "fk_supplier_payment_tax_releases_invoice",
            ["tenant_id", "invoice_id"],
            "supplier_invoices",
            ["tenant_id", "id"],
        ),
        _fk(
            "fk_supplier_payment_tax_releases_policy",
            ["tenant_id", "policy_id"],
            "tax_deferral_policies",
            ["tenant_id", "id"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_supplier_payment_tax_releases_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "payment_id",
            "line_number",
            name="uq_supplier_payment_tax_releases_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "payment_id",
            "invoice_id",
            "deferral_account_ref",
            name="uq_supplier_payment_tax_releases_release",
        ),
        sa.CheckConstraint(
            "amount > 0", name="ck_supplier_payment_tax_releases_amount"
        ),
        sa.CheckConstraint(
            "deferral_account_ref <> release_account_ref",
            name="ck_supplier_payment_tax_releases_accounts",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_supplier_payment_tax_releases_tenant_invoice",
        "supplier_payment_tax_releases",
        ["tenant_id", "invoice_id"],
        schema=_SCHEMA,
    )
    op.create_table(
        "historical_tax_deferrals",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("deferral_account_ref", sa.String(255), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_invoice_id", sa.String(255), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(68), nullable=False),
        sa.Column("facts_fingerprint", sa.String(68), nullable=False),
        sa.Column("accounting_evidence_fingerprint", sa.String(68), nullable=False),
        sa.Column("deferred_tax_total", sa.Numeric(20, 6), nullable=False),
        sa.Column("released_before_admission", sa.Numeric(20, 6), nullable=False),
        sa.Column("remaining", sa.Numeric(20, 6), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("historical_tax_deferrals"),
        _fk(
            "fk_historical_tax_deferrals_invoice",
            ["tenant_id", "invoice_id"],
            "supplier_invoices",
            ["tenant_id", "id"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_historical_tax_deferrals_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "invoice_id",
            "deferral_account_ref",
            name="uq_historical_tax_deferrals_invoice",
        ),
        sa.CheckConstraint(
            "deferred_tax_total > 0 AND released_before_admission >= 0"
            " AND remaining >= 0"
            " AND remaining = deferred_tax_total - released_before_admission",
            name="ck_historical_tax_deferrals_amounts",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "historical_credit_receipts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("credit_note_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_invoice_id", sa.String(255), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(68), nullable=False),
        sa.Column("facts_fingerprint", sa.String(68), nullable=False),
        sa.Column("accounting_evidence_fingerprint", sa.String(68), nullable=False),
        sa.Column("source_journal_ref", sa.String(255), nullable=False),
        sa.Column("destination_journal_ref", sa.String(255), nullable=False),
        sa.Column("carried_evidence_ref", sa.String(255), nullable=False),
        sa.Column("evidence_reference", sa.String(255), nullable=False),
        sa.Column("admitted_by", sa.String(255), nullable=False),
        sa.Column("credit_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("carried_control_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("original_posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("historical_credit_receipts"),
        _fk(
            "fk_historical_credit_receipts_credit",
            ["tenant_id", "credit_note_id"],
            "credit_notes",
            ["tenant_id", "id"],
        ),
        _fk(
            "fk_historical_credit_receipts_invoice",
            ["tenant_id", "invoice_id"],
            "supplier_invoices",
            ["tenant_id", "id"],
        ),
        _fk(
            "fk_historical_credit_receipts_event",
            ["tenant_id", "event_id"],
            "liability_events",
            ["tenant_id", "id"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_historical_credit_receipts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "credit_note_id", name="uq_historical_credit_receipts_credit"
        ),
        sa.UniqueConstraint(
            "tenant_id", "invoice_id", name="uq_historical_credit_receipts_invoice"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_invoice_id",
            name="uq_historical_credit_receipts_source",
        ),
        sa.CheckConstraint(
            "credit_amount > 0", name="ck_historical_credit_receipts_credit"
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "historical_credit_settlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("source_settlement_id", sa.String(255), nullable=False),
        sa.Column("payment_ref", sa.String(255), nullable=False),
        sa.Column("settled_on", sa.Date(), nullable=False),
        sa.Column("applied_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("withholding_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("source_row_fingerprint", sa.String(68), nullable=False),
        sa.Column("source_journal_ref", sa.String(255), nullable=False),
        sa.Column("destination_journal_ref", sa.String(255), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("historical_credit_settlements"),
        _fk(
            "fk_historical_credit_settlements_receipt",
            ["tenant_id", "receipt_id"],
            "historical_credit_receipts",
            ["tenant_id", "id"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_historical_credit_settlements_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "receipt_id",
            "line_number",
            name="uq_historical_credit_settlements_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_settlement_id",
            name="uq_historical_credit_settlements_source",
        ),
        sa.CheckConstraint(
            "applied_amount > 0 AND withholding_amount >= 0"
            " AND withholding_amount <= applied_amount",
            name="ck_historical_credit_settlements_amounts",
        ),
        sa.CheckConstraint(
            "exchange_rate > 0", name="ck_historical_credit_settlements_rate"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "credit_note_tax_splits",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("credit_note_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("deferral_account_ref", sa.String(255), nullable=False),
        sa.Column("release_account_ref", sa.String(255), nullable=False),
        sa.Column("deferred_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("released_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("credit_note_tax_splits"),
        _fk(
            "fk_credit_note_tax_splits_credit",
            ["tenant_id", "credit_note_id"],
            "credit_notes",
            ["tenant_id", "id"],
        ),
        _fk(
            "fk_credit_note_tax_splits_invoice",
            ["tenant_id", "invoice_id"],
            "supplier_invoices",
            ["tenant_id", "id"],
        ),
        _fk(
            "fk_credit_note_tax_splits_policy",
            ["tenant_id", "policy_id"],
            "tax_deferral_policies",
            ["tenant_id", "id"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_credit_note_tax_splits_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "credit_note_id",
            "line_number",
            name="uq_credit_note_tax_splits_line",
        ),
        sa.CheckConstraint(
            "deferred_amount >= 0 AND released_amount >= 0"
            " AND deferred_amount + released_amount > 0",
            name="ck_credit_note_tax_splits_amounts",
        ),
        sa.CheckConstraint(
            "deferral_account_ref <> release_account_ref",
            name="ck_credit_note_tax_splits_accounts",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_credit_note_tax_splits_tenant_invoice",
        "credit_note_tax_splits",
        ["tenant_id", "invoice_id"],
        schema=_SCHEMA,
    )
    op.create_table(
        "credit_application_tax_releases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("deferral_account_ref", sa.String(255), nullable=False),
        sa.Column("release_account_ref", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("credit_application_tax_releases"),
        _fk(
            "fk_credit_application_tax_releases_application",
            ["tenant_id", "application_id"],
            "credit_applications",
            ["tenant_id", "id"],
        ),
        _fk(
            "fk_credit_application_tax_releases_invoice",
            ["tenant_id", "invoice_id"],
            "supplier_invoices",
            ["tenant_id", "id"],
        ),
        _fk(
            "fk_credit_application_tax_releases_policy",
            ["tenant_id", "policy_id"],
            "tax_deferral_policies",
            ["tenant_id", "id"],
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_credit_application_tax_releases_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "application_id",
            "deferral_account_ref",
            name="uq_credit_application_tax_releases_release",
        ),
        sa.CheckConstraint(
            "amount > 0", name="ck_credit_application_tax_releases_amount"
        ),
        sa.CheckConstraint(
            "deferral_account_ref <> release_account_ref",
            name="ck_credit_application_tax_releases_accounts",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_credit_application_tax_releases_tenant_invoice",
        "credit_application_tax_releases",
        ["tenant_id", "invoice_id"],
        schema=_SCHEMA,
    )

    # A legacy settlement settles exactly one invoice across all admissions. The
    # service checks both directions; this trigger backs the credit side in the
    # database without touching the released historical_settlements table.
    op.execute(
        """
        CREATE FUNCTION mod_payables.protect_historical_credit_settlement()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='payables evidence is append-only';
          END IF;
          IF EXISTS (SELECT 1 FROM mod_payables.historical_settlements
                     WHERE tenant_id=NEW.tenant_id
                       AND source_settlement_id=NEW.source_settlement_id) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='a legacy settlement was already admitted against an invoice';
          END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_payables.protect_historical_credit_settlement() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER historical_credit_settlements_guard BEFORE INSERT OR UPDATE OR DELETE ON mod_payables.historical_credit_settlements FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_historical_credit_settlement();"
    )
    for table in _TABLES:
        if table != "historical_credit_settlements":
            op.execute(
                f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON mod_payables.{table} FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_immutable_payables_evidence();"
            )
        op.execute(f"ALTER TABLE mod_payables.{table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE mod_payables.{table} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON mod_payables.{table} USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
        )
        op.execute(f"GRANT SELECT, INSERT ON mod_payables.{table} TO app_user;")


def downgrade() -> None:
    # Policies, release rows, deferral positions and historical credits are
    # permanent financial evidence: a schema rollback cannot discard them.
    bind = op.get_bind()
    for table in _TABLES:
        op.execute(f"LOCK TABLE mod_payables.{table} IN ACCESS EXCLUSIVE MODE")
    for statement in (
        "SELECT EXISTS (SELECT 1 FROM mod_payables.tax_deferral_policies)",
        "SELECT EXISTS (SELECT 1 FROM mod_payables.supplier_payment_tax_releases)",
        "SELECT EXISTS (SELECT 1 FROM mod_payables.historical_tax_deferrals)",
        "SELECT EXISTS (SELECT 1 FROM mod_payables.historical_credit_receipts)",
        "SELECT EXISTS (SELECT 1 FROM mod_payables.historical_credit_settlements)",
        "SELECT EXISTS (SELECT 1 FROM mod_payables.credit_note_tax_splits)",
        "SELECT EXISTS (SELECT 1 FROM mod_payables.credit_application_tax_releases)",
    ):
        if bind.scalar(sa.text(statement)):
            raise RuntimeError(
                "tax deferral policies, releases, historical deferrals and "
                "historical credits require forward correction"
            )
    op.drop_table("credit_application_tax_releases", schema=_SCHEMA)
    op.drop_table("credit_note_tax_splits", schema=_SCHEMA)
    op.drop_table("historical_credit_settlements", schema=_SCHEMA)
    op.drop_table("historical_credit_receipts", schema=_SCHEMA)
    op.drop_table("historical_tax_deferrals", schema=_SCHEMA)
    op.drop_table("supplier_payment_tax_releases", schema=_SCHEMA)
    op.drop_table("tax_deferral_policies", schema=_SCHEMA)
    op.execute(
        "DROP FUNCTION IF EXISTS mod_payables.protect_historical_credit_settlement();"
    )
