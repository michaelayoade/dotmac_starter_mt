"""Governed payment reversal, settled historical admission, invoice recognition.

Revision ID: pa_0005_reversal_settled_history
Revises: pa_0004_supplier_payments

Additive: new nullable reversal columns and two new tables. The supplier-payment
status/evidence/separation-of-duties CHECKs and the lifecycle trigger function are
replaced by supersets that still accept every row pa_0004 could create.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "pa_0005_reversal_settled_history"
down_revision = "pa_0004_supplier_payments"
branch_labels = None
depends_on = None

_SCHEMA = "mod_payables"

# Frozen copies (a released migration must not change if the model later does).
_NO_REVERSAL = (
    "reversed_by IS NULL AND reversed_at IS NULL AND reversal_reason IS NULL"
    " AND reversal_date IS NULL"
)
_APPROVED_EVIDENCE = (
    "approved_by IS NOT NULL AND approved_at IS NOT NULL"
    " AND approval_reference IS NOT NULL"
)
_RECORDED_EVIDENCE = (
    "recorded_by IS NOT NULL AND recorded_at IS NOT NULL"
    " AND bank_reference IS NOT NULL"
)
_NO_RECORDING = "recorded_by IS NULL AND recorded_at IS NULL AND bank_reference IS NULL"
_NO_CANCEL = "cancelled_by IS NULL AND cancelled_at IS NULL AND cancel_reason IS NULL"
SUPPLIER_PAYMENT_EVIDENCE_CHECK = (
    "(status = 'DRAFT' AND approved_by IS NULL AND approved_at IS NULL"
    " AND approval_reference IS NULL AND "
    + _NO_RECORDING
    + " AND "
    + _NO_CANCEL
    + " AND "
    + _NO_REVERSAL
    + ")"
    " OR (status = 'APPROVED' AND "
    + _APPROVED_EVIDENCE
    + " AND "
    + _NO_RECORDING
    + " AND "
    + _NO_CANCEL
    + " AND "
    + _NO_REVERSAL
    + ")"
    " OR (status = 'RECORDED' AND "
    + _APPROVED_EVIDENCE
    + " AND "
    + _RECORDED_EVIDENCE
    + " AND "
    + _NO_CANCEL
    + " AND "
    + _NO_REVERSAL
    + ")"
    " OR (status = 'CANCELLED' AND "
    + _NO_RECORDING
    + " AND cancelled_by IS NOT NULL AND cancelled_at IS NOT NULL"
    " AND cancel_reason IS NOT NULL AND " + _NO_REVERSAL + ")"
    " OR (status = 'REVERSED' AND "
    + _APPROVED_EVIDENCE
    + " AND "
    + _RECORDED_EVIDENCE
    + " AND "
    + _NO_CANCEL
    + " AND reversed_by IS NOT NULL AND reversed_at IS NOT NULL"
    " AND reversal_reason IS NOT NULL AND reversal_date IS NOT NULL)"
)
SUPPLIER_PAYMENT_SOD_CHECK = (
    "(approved_by IS NULL OR lower(approved_by) <> lower(prepared_by))"
    " AND (recorded_by IS NULL OR (lower(recorded_by) <> lower(prepared_by)"
    " AND lower(recorded_by) <> lower(approved_by)))"
    " AND (reversed_by IS NULL OR (lower(reversed_by) <> lower(recorded_by)"
    " AND lower(reversed_by) <> lower(approved_by)))"
)

_V1_EVIDENCE_CHECK = (
    "(status = 'DRAFT' AND approved_by IS NULL AND approved_at IS NULL"
    " AND approval_reference IS NULL AND recorded_by IS NULL AND recorded_at IS NULL"
    " AND bank_reference IS NULL AND cancelled_by IS NULL AND cancelled_at IS NULL"
    " AND cancel_reason IS NULL)"
    " OR (status = 'APPROVED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL"
    " AND approval_reference IS NOT NULL AND recorded_by IS NULL"
    " AND recorded_at IS NULL"
    " AND bank_reference IS NULL AND cancelled_by IS NULL AND cancelled_at IS NULL"
    " AND cancel_reason IS NULL)"
    " OR (status = 'RECORDED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL"
    " AND approval_reference IS NOT NULL AND recorded_by IS NOT NULL"
    " AND recorded_at IS NOT NULL AND bank_reference IS NOT NULL"
    " AND cancelled_by IS NULL AND cancelled_at IS NULL AND cancel_reason IS NULL)"
    " OR (status = 'CANCELLED' AND recorded_by IS NULL AND recorded_at IS NULL"
    " AND bank_reference IS NULL AND cancelled_by IS NOT NULL"
    " AND cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL)"
)
_V1_SOD_CHECK = (
    "(approved_by IS NULL OR lower(approved_by) <> lower(prepared_by))"
    " AND (recorded_by IS NULL OR (lower(recorded_by) <> lower(prepared_by)"
    " AND lower(recorded_by) <> lower(approved_by)))"
)

_V1_FUNCTION = """
        CREATE FUNCTION mod_payables.protect_supplier_payment()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE lifecycle text[] := ARRAY['status','approval_reference','approved_by',
          'approved_at','bank_reference','recorded_by','recorded_at','cancelled_by',
          'cancelled_at','cancel_reason','updated_at'];
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='supplier payments are never deleted';
          END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'DRAFT' THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='a supplier payment is created as a draft';
            END IF;
            RETURN NEW;
          END IF;
          IF (to_jsonb(NEW)-lifecycle) <> (to_jsonb(OLD)-lifecycle) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='supplier payment facts are immutable';
          END IF;
          IF OLD.approved_by IS NOT NULL AND (
               NEW.approved_by IS DISTINCT FROM OLD.approved_by
            OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
            OR NEW.approval_reference IS DISTINCT FROM OLD.approval_reference) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='supplier payment approval evidence is immutable';
          END IF;
          IF NOT ((OLD.status='DRAFT' AND NEW.status IN ('APPROVED','CANCELLED'))
               OR (OLD.status='APPROVED' AND NEW.status IN ('RECORDED','CANCELLED'))) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid supplier payment transition';
          END IF;
          RETURN NEW;
        END; $$;
"""

_V2_FUNCTION = """
        CREATE OR REPLACE FUNCTION mod_payables.protect_supplier_payment()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE lifecycle text[] := ARRAY['status','approval_reference','approved_by',
          'approved_at','bank_reference','recorded_by','recorded_at','cancelled_by',
          'cancelled_at','cancel_reason','updated_at','reversed_by','reversed_at',
          'reversal_reason','reversal_date'];
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='supplier payments are never deleted';
          END IF;
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'DRAFT' THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='a supplier payment is created as a draft';
            END IF;
            RETURN NEW;
          END IF;
          IF (to_jsonb(NEW)-lifecycle) <> (to_jsonb(OLD)-lifecycle) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='supplier payment facts are immutable';
          END IF;
          IF OLD.approved_by IS NOT NULL AND (
               NEW.approved_by IS DISTINCT FROM OLD.approved_by
            OR NEW.approved_at IS DISTINCT FROM OLD.approved_at
            OR NEW.approval_reference IS DISTINCT FROM OLD.approval_reference) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='supplier payment approval evidence is immutable';
          END IF;
          IF OLD.recorded_by IS NOT NULL AND (
               NEW.recorded_by IS DISTINCT FROM OLD.recorded_by
            OR NEW.recorded_at IS DISTINCT FROM OLD.recorded_at
            OR NEW.bank_reference IS DISTINCT FROM OLD.bank_reference) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='supplier payment recording evidence is immutable';
          END IF;
          IF NOT ((OLD.status='DRAFT' AND NEW.status IN ('APPROVED','CANCELLED'))
               OR (OLD.status='APPROVED' AND NEW.status IN ('RECORDED','CANCELLED'))
               OR (OLD.status='RECORDED' AND NEW.status='REVERSED')) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid supplier payment transition';
          END IF;
          RETURN NEW;
        END; $$;
"""


def _swap_payment_checks(*, status: str, evidence: str, sod: str) -> None:
    op.drop_constraint(
        "ck_supplier_payments_status", "supplier_payments", schema=_SCHEMA
    )
    op.drop_constraint(
        "ck_supplier_payments_evidence", "supplier_payments", schema=_SCHEMA
    )
    op.drop_constraint("ck_supplier_payments_sod", "supplier_payments", schema=_SCHEMA)
    op.create_check_constraint(
        "ck_supplier_payments_status",
        "supplier_payments",
        status,
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_supplier_payments_evidence",
        "supplier_payments",
        evidence,
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_supplier_payments_sod", "supplier_payments", sod, schema=_SCHEMA
    )


def upgrade() -> None:
    op.add_column(
        "supplier_payments", sa.Column("reversed_by", sa.String(255)), schema=_SCHEMA
    )
    op.add_column(
        "supplier_payments",
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        schema=_SCHEMA,
    )
    op.add_column(
        "supplier_payments", sa.Column("reversal_reason", sa.Text()), schema=_SCHEMA
    )
    op.add_column(
        "supplier_payments", sa.Column("reversal_date", sa.Date()), schema=_SCHEMA
    )
    _swap_payment_checks(
        status="status IN ('DRAFT','APPROVED','RECORDED','CANCELLED','REVERSED')",
        evidence=SUPPLIER_PAYMENT_EVIDENCE_CHECK,
        sod=SUPPLIER_PAYMENT_SOD_CHECK,
    )
    op.execute(_V2_FUNCTION)

    op.create_table(
        "invoice_recognitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_reference", sa.String(255), nullable=False),
        sa.Column("recognized_by", sa.String(255), nullable=False),
        sa.Column("approval_reference", sa.String(255), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_invoice_id", sa.String(255), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(68), nullable=False),
        sa.Column("facts_fingerprint", sa.String(68), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("recognized_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_invoice_recognitions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [
                "mod_payables.supplier_invoices.tenant_id",
                "mod_payables.supplier_invoices.id",
            ],
            ondelete="RESTRICT",
            name="fk_invoice_recognitions_invoice",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_invoice_recognitions_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "invoice_id", name="uq_invoice_recognitions_invoice"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_invoice_id",
            name="uq_invoice_recognitions_source",
        ),
        schema=_SCHEMA,
    )
    op.create_table(
        "historical_settlements",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_id", sa.Uuid(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_historical_settlements_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "receipt_id"],
            [
                "mod_payables.historical_invoice_receipts.tenant_id",
                "mod_payables.historical_invoice_receipts.id",
            ],
            ondelete="RESTRICT",
            name="fk_historical_settlements_receipt",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                "mod_payables.payment_obligations.tenant_id",
                "mod_payables.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_historical_settlements_obligation",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_historical_settlements_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "receipt_id",
            "line_number",
            name="uq_historical_settlements_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_settlement_id",
            name="uq_historical_settlements_source",
        ),
        sa.CheckConstraint(
            "applied_amount > 0 AND withholding_amount >= 0"
            " AND withholding_amount <= applied_amount",
            name="ck_historical_settlements_amounts",
        ),
        sa.CheckConstraint("exchange_rate > 0", name="ck_historical_settlements_rate"),
        schema=_SCHEMA,
    )

    # A recognition may only be written for an APPROVED invoice that has no
    # recognition event, obligation, accounting receipt or historical receipt.
    op.execute(
        """
        CREATE FUNCTION mod_payables.protect_invoice_recognition()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE invoice_status text;
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invoice recognitions are append-only';
          END IF;
          SELECT status INTO invoice_status FROM mod_payables.supplier_invoices
          WHERE id=NEW.invoice_id AND tenant_id=NEW.tenant_id FOR SHARE;
          IF invoice_status IS DISTINCT FROM 'APPROVED' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='only an approved invoice can be recognized';
          END IF;
          IF EXISTS (SELECT 1 FROM mod_payables.liability_events
                     WHERE tenant_id=NEW.tenant_id AND document_kind='supplier_invoice'
                       AND document_id=NEW.invoice_id)
             OR EXISTS (SELECT 1 FROM mod_payables.payment_obligations
                        WHERE tenant_id=NEW.tenant_id AND invoice_id=NEW.invoice_id)
             OR EXISTS (SELECT 1 FROM mod_payables.accounting_receipts
                        WHERE tenant_id=NEW.tenant_id AND document_kind='supplier_invoice'
                          AND document_id=NEW.invoice_id)
             OR EXISTS (SELECT 1 FROM mod_payables.historical_invoice_receipts
                        WHERE tenant_id=NEW.tenant_id AND invoice_id=NEW.invoice_id) THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invoice already has recognition, obligation or accounting evidence';
          END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_payables.protect_invoice_recognition() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER invoice_recognitions_guard BEFORE INSERT OR UPDATE OR DELETE ON mod_payables.invoice_recognitions FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_invoice_recognition();"
    )
    op.execute(
        "CREATE TRIGGER historical_settlements_immutable BEFORE UPDATE OR DELETE ON mod_payables.historical_settlements FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_immutable_payables_evidence();"
    )

    op.execute(
        "ALTER TABLE mod_payables.invoice_recognitions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_payables.invoice_recognitions FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY invoice_recognitions_tenant_isolation ON mod_payables.invoice_recognitions USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_payables.invoice_recognitions TO app_user;")
    op.execute(
        "ALTER TABLE mod_payables.historical_settlements ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_payables.historical_settlements FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY historical_settlements_tenant_isolation ON mod_payables.historical_settlements USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_payables.historical_settlements TO app_user;"
    )


def downgrade() -> None:
    # Reversals, recognitions and legacy settlements are permanent financial
    # evidence: a schema rollback cannot discard them. Once any exist, recovery
    # requires a forward correction (as pa_0003 and pa_0004 do).
    op.execute("LOCK TABLE mod_payables.supplier_payments IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE mod_payables.invoice_recognitions IN ACCESS EXCLUSIVE MODE")
    op.execute(
        "LOCK TABLE mod_payables.historical_settlements IN ACCESS EXCLUSIVE MODE"
    )
    bind = op.get_bind()
    if bind.scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM mod_payables.supplier_payments"
            " WHERE status='REVERSED')"
        )
    ):
        raise RuntimeError("reversed supplier payments require forward correction")
    for statement in (
        "SELECT EXISTS (SELECT 1 FROM mod_payables.invoice_recognitions)",
        "SELECT EXISTS (SELECT 1 FROM mod_payables.historical_settlements)",
    ):
        if bind.scalar(sa.text(statement)):
            raise RuntimeError(
                "invoice recognitions and historical settlements "
                "require forward correction"
            )
    op.drop_table("historical_settlements", schema=_SCHEMA)
    op.drop_table("invoice_recognitions", schema=_SCHEMA)
    op.execute("DROP FUNCTION IF EXISTS mod_payables.protect_invoice_recognition();")
    op.execute(_V1_FUNCTION.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION"))
    _swap_payment_checks(
        status="status IN ('DRAFT','APPROVED','RECORDED','CANCELLED')",
        evidence=_V1_EVIDENCE_CHECK,
        sod=_V1_SOD_CHECK,
    )
    for column in ("reversal_date", "reversal_reason", "reversed_at", "reversed_by"):
        op.drop_column("supplier_payments", column, schema=_SCHEMA)
