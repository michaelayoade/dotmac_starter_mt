"""Supplier-payment lifecycle: additive tables, no change to released tables.

Revision ID: pa_0004_supplier_payments
Revises: pa_0003_historical_invoice
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "pa_0004_supplier_payments"
down_revision = "pa_0003_historical_invoice"
branch_labels = None
depends_on = None

_SCHEMA = "mod_payables"
# Frozen copies: a released migration must not change if the model later does.
# tests/architecture/test_payables_module.py asserts these equal the model today.
SUPPLIER_PAYMENT_EVIDENCE_CHECK = (
    "(status = 'DRAFT' AND approved_by IS NULL AND approved_at IS NULL"
    " AND approval_reference IS NULL AND recorded_by IS NULL AND recorded_at IS NULL"
    " AND bank_reference IS NULL AND cancelled_by IS NULL AND cancelled_at IS NULL"
    " AND cancel_reason IS NULL)"
    " OR (status = 'APPROVED' AND approved_by IS NOT NULL AND approved_at IS NOT NULL"
    " AND approval_reference IS NOT NULL AND recorded_by IS NULL AND recorded_at IS NULL"
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
SUPPLIER_PAYMENT_SOD_CHECK = (
    "(approved_by IS NULL OR lower(approved_by) <> lower(prepared_by))"
    " AND (recorded_by IS NULL OR (lower(recorded_by) <> lower(prepared_by)"
    " AND lower(recorded_by) <> lower(approved_by)))"
)

_CHILDREN = ("supplier_payment_allocations", "supplier_payment_withholdings")


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id"],
        ["public.tenants.id"],
        ondelete="CASCADE",
        name=f"fk_{table}_tenant",
    )


def _payment_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", "payment_id"],
        [
            "mod_payables.supplier_payments.tenant_id",
            "mod_payables.supplier_payments.id",
        ],
        ondelete="RESTRICT",
        name=name,
    )


def upgrade() -> None:
    op.create_table(
        "supplier_payments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("supplier_ref", sa.String(255), nullable=False),
        sa.Column("supplier_name_snapshot", sa.String(255), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("value_date", sa.Date(), nullable=False),
        sa.Column("cash_account_ref", sa.String(255), nullable=False),
        sa.Column("payer_instrument_ref", sa.String(255), nullable=False),
        sa.Column("gross_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("wht_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("net_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("prepared_by", sa.String(255), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approval_reference", sa.String(255)),
        sa.Column("approved_by", sa.String(255)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("bank_reference", sa.String(255)),
        sa.Column("recorded_by", sa.String(255)),
        sa.Column("recorded_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_by", sa.String(255)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_reason", sa.Text()),
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
        _tenant_fk("supplier_payments"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_supplier_payments_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "number", name="uq_supplier_payments_tenant_number"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "payer_instrument_ref",
            "bank_reference",
            name="uq_supplier_payments_bank_reference",
        ),
        sa.CheckConstraint(
            "length(currency_code) = 3", name="ck_supplier_payments_currency"
        ),
        sa.CheckConstraint(
            "exchange_rate > 0", name="ck_supplier_payments_rate_positive"
        ),
        sa.CheckConstraint(
            "gross_amount > 0 AND wht_amount >= 0 AND wht_amount < gross_amount"
            " AND net_amount = gross_amount - wht_amount",
            name="ck_supplier_payments_amounts",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','APPROVED','RECORDED','CANCELLED')",
            name="ck_supplier_payments_status",
        ),
        sa.CheckConstraint(
            SUPPLIER_PAYMENT_EVIDENCE_CHECK, name="ck_supplier_payments_evidence"
        ),
        sa.CheckConstraint(SUPPLIER_PAYMENT_SOD_CHECK, name="ck_supplier_payments_sod"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_supplier_payments_tenant_supplier",
        "supplier_payments",
        ["tenant_id", "supplier_ref"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_supplier_payments_tenant_status",
        "supplier_payments",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "supplier_payment_allocations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("obligation_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("supplier_payment_allocations"),
        _payment_fk("fk_supplier_payment_allocations_payment"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                "mod_payables.payment_obligations.tenant_id",
                "mod_payables.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_supplier_payment_allocations_obligation",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_supplier_payment_allocations_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "payment_id",
            "line_number",
            name="uq_supplier_payment_allocations_number",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "payment_id",
            "obligation_id",
            name="uq_supplier_payment_allocations_obligation",
        ),
        sa.CheckConstraint("amount > 0", name="ck_supplier_payment_allocations_amount"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_supplier_payment_allocations_tenant_obligation",
        "supplier_payment_allocations",
        ["tenant_id", "obligation_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "supplier_payment_withholdings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("account_ref", sa.String(255), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("description", sa.String(255)),
        sa.Column("certificate_ref", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        _tenant_fk("supplier_payment_withholdings"),
        _payment_fk("fk_supplier_payment_withholdings_payment"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_supplier_payment_withholdings_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "payment_id",
            "line_number",
            name="uq_supplier_payment_withholdings_number",
        ),
        sa.CheckConstraint(
            "amount > 0", name="ck_supplier_payment_withholdings_amount"
        ),
        schema=_SCHEMA,
    )

    # Header: only forward lifecycle transitions; commercial facts are frozen from
    # creation; DELETE is never allowed (a cancelled payment stays as history).
    op.execute(
        """
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
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_payables.protect_supplier_payment() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER supplier_payments_lifecycle BEFORE INSERT OR UPDATE OR DELETE ON mod_payables.supplier_payments FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_supplier_payment();"
    )
    # Children: insert only while the parent payment is a draft; never changed.
    op.execute(
        """
        CREATE FUNCTION mod_payables.protect_supplier_payment_child()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE parent_status text;
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='supplier payment lines are append-only';
          END IF;
          SELECT status INTO parent_status FROM mod_payables.supplier_payments
          WHERE id=NEW.payment_id AND tenant_id=NEW.tenant_id FOR SHARE;
          IF parent_status IS DISTINCT FROM 'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='lines can only be added to a draft supplier payment';
          END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_payables.protect_supplier_payment_child() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER supplier_payment_allocations_append_only BEFORE INSERT OR UPDATE OR DELETE ON mod_payables.supplier_payment_allocations FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_supplier_payment_child();"
    )
    op.execute(
        "CREATE TRIGGER supplier_payment_withholdings_append_only BEFORE INSERT OR UPDATE OR DELETE ON mod_payables.supplier_payment_withholdings FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_supplier_payment_child();"
    )

    op.execute("ALTER TABLE mod_payables.supplier_payments ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_payables.supplier_payments FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY supplier_payments_tenant_isolation ON mod_payables.supplier_payments USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON mod_payables.supplier_payments TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_payables.supplier_payment_allocations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_payables.supplier_payment_allocations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY supplier_payment_allocations_tenant_isolation ON mod_payables.supplier_payment_allocations USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_payables.supplier_payment_allocations TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_payables.supplier_payment_withholdings ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_payables.supplier_payment_withholdings FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY supplier_payment_withholdings_tenant_isolation ON mod_payables.supplier_payment_withholdings USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_payables.supplier_payment_withholdings TO app_user;"
    )


def downgrade() -> None:
    # Recorded supplier payments are financial evidence: a schema rollback cannot
    # discard them. Once populated, recovery requires a forward correction.
    op.execute("LOCK TABLE mod_payables.supplier_payments IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM mod_payables.supplier_payments)")
    ):
        raise RuntimeError("supplier payments require forward correction")
    for table in reversed(_CHILDREN):
        op.drop_table(table, schema=_SCHEMA)
    op.drop_table("supplier_payments", schema=_SCHEMA)
    op.execute("DROP FUNCTION IF EXISTS mod_payables.protect_supplier_payment_child();")
    op.execute("DROP FUNCTION IF EXISTS mod_payables.protect_supplier_payment();")
