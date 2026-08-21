"""Create the tenant supplier-liability owner.

Revision ID: pa_0001_payables
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

revision = "pa_0001_payables"
down_revision = None
branch_labels = ("payables",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_payables"


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
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_payables;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_payables TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        "supplier_invoices",
        *_identity("supplier_invoices"),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("supplier_ref", sa.String(255), nullable=False),
        sa.Column("supplier_name_snapshot", sa.String(255), nullable=False),
        sa.Column("supplier_document_number", sa.String(120), nullable=False),
        sa.Column("invoice_date", sa.Date(), nullable=False),
        sa.Column("received_date", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("liability_account_ref", sa.String(255), nullable=False),
        sa.Column("subtotal", sa.Numeric(20, 6), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("total_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("payment_schedule", postgresql.JSONB(), nullable=False),
        sa.Column("procurement_ref", sa.String(255), nullable=True),
        sa.Column("receipt_evidence_fingerprint", sa.String(64), nullable=True),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("submitted_by", sa.String(255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_reference", sa.String(255), nullable=True),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        *_timestamps(),
        *_tenant_constraints("supplier_invoices"),
        sa.UniqueConstraint(
            "tenant_id", "number", name="uq_supplier_invoices_tenant_number"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "supplier_ref",
            "supplier_document_number",
            name="uq_supplier_invoices_supplier_document",
        ),
        sa.CheckConstraint(
            "length(currency_code)=3", name="ck_supplier_invoices_currency"
        ),
        sa.CheckConstraint(
            "exchange_rate > 0", name="ck_supplier_invoices_rate_positive"
        ),
        sa.CheckConstraint(
            "subtotal >= 0 AND tax_amount >= 0 AND total_amount > 0",
            name="ck_supplier_invoices_amounts",
        ),
        sa.CheckConstraint(
            "total_amount = subtotal + tax_amount",
            name="ck_supplier_invoices_total",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','APPROVED','VOID')",
            name="ck_supplier_invoices_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_supplier_invoices_tenant_supplier",
        "supplier_invoices",
        ["tenant_id", "supplier_ref"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_supplier_invoices_tenant_status",
        "supplier_invoices",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "supplier_invoice_lines",
        *_identity("supplier_invoice_lines"),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("line_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("posting_account_ref", sa.String(255), nullable=False),
        sa.Column("tax_account_ref", sa.String(255), nullable=True),
        sa.Column("dimension_refs", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("supplier_invoice_lines"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [
                "mod_payables.supplier_invoices.tenant_id",
                "mod_payables.supplier_invoices.id",
            ],
            ondelete="CASCADE",
            name="fk_supplier_invoice_lines_tenant_invoice",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "invoice_id",
            "line_number",
            name="uq_supplier_invoice_lines_number",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_supplier_invoice_lines_quantity"),
        sa.CheckConstraint(
            "unit_price >= 0 AND line_amount >= 0 AND tax_amount >= 0",
            name="ck_supplier_invoice_lines_amounts",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_supplier_invoice_lines_tenant_invoice",
        "supplier_invoice_lines",
        ["tenant_id", "invoice_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "credit_notes",
        *_identity("credit_notes"),
        sa.Column("original_invoice_id", sa.Uuid(), nullable=True),
        sa.Column("number", sa.String(50), nullable=False),
        sa.Column("supplier_ref", sa.String(255), nullable=False),
        sa.Column("supplier_name_snapshot", sa.String(255), nullable=False),
        sa.Column("supplier_document_number", sa.String(120), nullable=False),
        sa.Column("credit_date", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("exchange_rate", sa.Numeric(20, 10), nullable=False),
        sa.Column("liability_account_ref", sa.String(255), nullable=False),
        sa.Column("subtotal", sa.Numeric(20, 6), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("total_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column(
            "available_amount", sa.Numeric(20, 6), nullable=False, server_default="0"
        ),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="DRAFT"),
        sa.Column("submitted_by", sa.String(255), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_reference", sa.String(255), nullable=True),
        sa.Column("approved_by", sa.String(255), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        *_timestamps(),
        *_tenant_constraints("credit_notes"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "original_invoice_id"],
            [
                "mod_payables.supplier_invoices.tenant_id",
                "mod_payables.supplier_invoices.id",
            ],
            ondelete="RESTRICT",
            name="fk_credit_notes_tenant_original_invoice",
        ),
        sa.UniqueConstraint(
            "tenant_id", "number", name="uq_credit_notes_tenant_number"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "supplier_ref",
            "supplier_document_number",
            name="uq_credit_notes_supplier_document",
        ),
        sa.CheckConstraint("length(currency_code)=3", name="ck_credit_notes_currency"),
        sa.CheckConstraint("exchange_rate > 0", name="ck_credit_notes_rate_positive"),
        sa.CheckConstraint(
            "subtotal >= 0 AND tax_amount >= 0 AND total_amount > 0",
            name="ck_credit_notes_amounts",
        ),
        sa.CheckConstraint(
            "total_amount = subtotal + tax_amount",
            name="ck_credit_notes_total",
        ),
        sa.CheckConstraint(
            "available_amount >= 0 AND available_amount <= total_amount",
            name="ck_credit_notes_available",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','APPROVED','VOID')",
            name="ck_credit_notes_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_credit_notes_tenant_supplier",
        "credit_notes",
        ["tenant_id", "supplier_ref"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_credit_notes_tenant_status",
        "credit_notes",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "credit_note_lines",
        *_identity("credit_note_lines"),
        sa.Column("credit_note_id", sa.Uuid(), nullable=False),
        sa.Column("line_number", sa.Integer(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Numeric(20, 6), nullable=False),
        sa.Column("unit_price", sa.Numeric(20, 6), nullable=False),
        sa.Column("line_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("tax_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("posting_account_ref", sa.String(255), nullable=False),
        sa.Column("tax_account_ref", sa.String(255), nullable=True),
        sa.Column("dimension_refs", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("credit_note_lines"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credit_note_id"],
            ["mod_payables.credit_notes.tenant_id", "mod_payables.credit_notes.id"],
            ondelete="CASCADE",
            name="fk_credit_note_lines_tenant_credit_note",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "credit_note_id",
            "line_number",
            name="uq_credit_note_lines_number",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_credit_note_lines_quantity"),
        sa.CheckConstraint(
            "unit_price >= 0 AND line_amount >= 0 AND tax_amount >= 0",
            name="ck_credit_note_lines_amounts",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_credit_note_lines_tenant_credit",
        "credit_note_lines",
        ["tenant_id", "credit_note_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "payment_obligations",
        *_identity("payment_obligations"),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("original_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("outstanding_amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="OPEN"),
        *_timestamps(),
        *_tenant_constraints("payment_obligations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [
                "mod_payables.supplier_invoices.tenant_id",
                "mod_payables.supplier_invoices.id",
            ],
            ondelete="RESTRICT",
            name="fk_payment_obligations_tenant_invoice",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "invoice_id",
            "sequence",
            name="uq_payment_obligations_invoice_sequence",
        ),
        sa.CheckConstraint(
            "original_amount > 0", name="ck_payment_obligations_original_positive"
        ),
        sa.CheckConstraint(
            "outstanding_amount >= 0 AND outstanding_amount <= original_amount",
            name="ck_payment_obligations_outstanding",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','PARTIALLY_SETTLED','SETTLED')",
            name="ck_payment_obligations_status",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_payment_obligations_tenant_due",
        "payment_obligations",
        ["tenant_id", "due_date", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "liability_events",
        *_identity("liability_events"),
        sa.Column("obligation_id", sa.Uuid(), nullable=True),
        sa.Column("event_kind", sa.String(40), nullable=False),
        sa.Column("document_kind", sa.String(40), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("supplier_ref", sa.String(255), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("liability_events"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                "mod_payables.payment_obligations.tenant_id",
                "mod_payables.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_liability_events_tenant_obligation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "event_kind",
            "document_kind",
            "document_id",
            "source_reference",
            name="uq_liability_events_source",
        ),
        sa.CheckConstraint("amount <> 0", name="ck_liability_events_amount_nonzero"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_liability_events_tenant_supplier",
        "liability_events",
        ["tenant_id", "supplier_ref", "currency_code"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_liability_events_tenant_document",
        "liability_events",
        ["tenant_id", "document_kind", "document_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "credit_applications",
        *_identity("credit_applications"),
        sa.Column("credit_note_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("applied_by", sa.String(255), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("credit_applications"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credit_note_id"],
            ["mod_payables.credit_notes.tenant_id", "mod_payables.credit_notes.id"],
            ondelete="RESTRICT",
            name="fk_credit_applications_tenant_credit",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                "mod_payables.payment_obligations.tenant_id",
                "mod_payables.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_credit_applications_tenant_obligation",
        ),
        sa.CheckConstraint("amount > 0", name="ck_credit_applications_amount_positive"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_credit_applications_tenant_credit",
        "credit_applications",
        ["tenant_id", "credit_note_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_credit_applications_tenant_obligation",
        "credit_applications",
        ["tenant_id", "obligation_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "settlement_observations",
        *_identity("settlement_observations"),
        sa.Column("obligation_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_reference", sa.String(255), nullable=False),
        sa.Column("source_version", sa.String(120), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("settlement_observations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                "mod_payables.payment_obligations.tenant_id",
                "mod_payables.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_settlement_observations_tenant_obligation",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_reference",
            "source_version",
            name="uq_settlement_observations_source",
        ),
        sa.CheckConstraint(
            "amount > 0", name="ck_settlement_observations_amount_positive"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_settlement_observations_tenant_obligation",
        "settlement_observations",
        ["tenant_id", "obligation_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "accounting_receipts",
        *_identity("accounting_receipts"),
        sa.Column("document_kind", sa.String(40), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("consequence_fingerprint", sa.String(64), nullable=False),
        sa.Column("accounting_reference", sa.String(255), nullable=False),
        sa.Column("accounting_evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        *_tenant_constraints("accounting_receipts"),
        sa.UniqueConstraint(
            "tenant_id",
            "document_kind",
            "document_id",
            name="uq_accounting_receipts_document",
        ),
        schema=_SCHEMA,
    )

    # Once submitted, source documents and lines cannot be rewritten. Projection
    # fields move only through the service transitions above.
    op.execute(
        """
        CREATE FUNCTION mod_payables.protect_payables_document()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.status<>'DRAFT' THEN
              RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='a payables document must begin as draft';
            END IF;
            RETURN NEW;
          END IF;
          IF TG_OP='DELETE' AND OLD.status<>'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='submitted payables documents cannot be deleted';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='DRAFT' THEN
            IF NEW.status='DRAFT' THEN RETURN NEW; END IF;
            IF NEW.status='SUBMITTED'
               AND (to_jsonb(NEW)-ARRAY['status','submitted_by','submitted_at','updated_at'])
                 = (to_jsonb(OLD)-ARRAY['status','submitted_by','submitted_at','updated_at']) THEN
              RETURN NEW;
            END IF;
            IF NEW.status='VOID'
               AND (to_jsonb(NEW)-ARRAY['status','void_reason','updated_at'])
                 = (to_jsonb(OLD)-ARRAY['status','void_reason','updated_at']) THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid draft payables document transition';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='SUBMITTED' THEN
            IF NEW.status='APPROVED'
               AND (to_jsonb(NEW)-ARRAY['status','available_amount','approval_reference','approved_by','approved_at','updated_at'])
                 = (to_jsonb(OLD)-ARRAY['status','available_amount','approval_reference','approved_by','approved_at','updated_at']) THEN
              RETURN NEW;
            END IF;
            IF NEW.status='VOID'
               AND (to_jsonb(NEW)-ARRAY['status','void_reason','updated_at'])
                 = (to_jsonb(OLD)-ARRAY['status','void_reason','updated_at']) THEN
              RETURN NEW;
            END IF;
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='invalid submitted payables document transition';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='APPROVED' THEN
            IF TG_TABLE_NAME='credit_notes' THEN
              IF NEW.status='APPROVED'
                 AND NEW.available_amount<=OLD.available_amount
                 AND (to_jsonb(NEW)-ARRAY['available_amount','updated_at'])
                   = (to_jsonb(OLD)-ARRAY['available_amount','updated_at']) THEN
                RETURN NEW;
              END IF;
            END IF;
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='approved payables document is immutable';
          END IF;
          IF TG_OP='UPDATE' AND OLD.status='VOID' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='void payables document is immutable';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_payables.protect_payables_document() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER supplier_invoices_submitted_immutable BEFORE INSERT OR UPDATE OR DELETE ON mod_payables.supplier_invoices FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_payables_document();"
    )
    op.execute(
        "CREATE TRIGGER credit_notes_submitted_immutable BEFORE INSERT OR UPDATE OR DELETE ON mod_payables.credit_notes FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_payables_document();"
    )

    op.execute(
        """
        CREATE FUNCTION mod_payables.protect_payables_line()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        DECLARE document_status text;
        BEGIN
          IF TG_TABLE_NAME='supplier_invoice_lines' THEN
            SELECT status INTO document_status FROM mod_payables.supplier_invoices
            WHERE id=COALESCE(NEW.invoice_id,OLD.invoice_id) AND tenant_id=COALESCE(NEW.tenant_id,OLD.tenant_id);
          ELSE
            SELECT status INTO document_status FROM mod_payables.credit_notes
            WHERE id=COALESCE(NEW.credit_note_id,OLD.credit_note_id) AND tenant_id=COALESCE(NEW.tenant_id,OLD.tenant_id);
          END IF;
          IF document_status<>'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='submitted payables lines are immutable';
          END IF;
          IF TG_OP='DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_payables.protect_payables_line() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER supplier_invoice_lines_submitted_immutable BEFORE INSERT OR UPDATE OR DELETE ON mod_payables.supplier_invoice_lines FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_payables_line();"
    )
    op.execute(
        "CREATE TRIGGER credit_note_lines_submitted_immutable BEFORE INSERT OR UPDATE OR DELETE ON mod_payables.credit_note_lines FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_payables_line();"
    )

    op.execute(
        """
        CREATE FUNCTION mod_payables.protect_immutable_payables_evidence()
        RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
          RAISE EXCEPTION USING ERRCODE='integrity_constraint_violation', MESSAGE='payables evidence is append-only';
        END; $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION mod_payables.protect_immutable_payables_evidence() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER liability_events_immutable BEFORE UPDATE OR DELETE ON mod_payables.liability_events FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_immutable_payables_evidence();"
    )
    op.execute(
        "CREATE TRIGGER credit_applications_immutable BEFORE UPDATE OR DELETE ON mod_payables.credit_applications FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_immutable_payables_evidence();"
    )
    op.execute(
        "CREATE TRIGGER settlement_observations_immutable BEFORE UPDATE OR DELETE ON mod_payables.settlement_observations FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_immutable_payables_evidence();"
    )
    op.execute(
        "CREATE TRIGGER accounting_receipts_immutable BEFORE UPDATE OR DELETE ON mod_payables.accounting_receipts FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_immutable_payables_evidence();"
    )

    op.execute("ALTER TABLE mod_payables.supplier_invoices ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_payables.supplier_invoices FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY supplier_invoices_tenant_isolation ON mod_payables.supplier_invoices USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payables.supplier_invoices TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_payables.supplier_invoice_lines ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_payables.supplier_invoice_lines FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY supplier_invoice_lines_tenant_isolation ON mod_payables.supplier_invoice_lines USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payables.supplier_invoice_lines TO app_user;"
    )
    op.execute("ALTER TABLE mod_payables.credit_notes ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_payables.credit_notes FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY credit_notes_tenant_isolation ON mod_payables.credit_notes USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payables.credit_notes TO app_user;"
    )
    op.execute("ALTER TABLE mod_payables.credit_note_lines ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_payables.credit_note_lines FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY credit_note_lines_tenant_isolation ON mod_payables.credit_note_lines USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payables.credit_note_lines TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_payables.payment_obligations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_payables.payment_obligations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY payment_obligations_tenant_isolation ON mod_payables.payment_obligations USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_payables.payment_obligations TO app_user;"
    )
    op.execute("ALTER TABLE mod_payables.liability_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_payables.liability_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY liability_events_tenant_isolation ON mod_payables.liability_events USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_payables.liability_events TO app_user;")
    op.execute(
        "ALTER TABLE mod_payables.credit_applications ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_payables.credit_applications FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY credit_applications_tenant_isolation ON mod_payables.credit_applications USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_payables.credit_applications TO app_user;")
    op.execute(
        "ALTER TABLE mod_payables.settlement_observations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_payables.settlement_observations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY settlement_observations_tenant_isolation ON mod_payables.settlement_observations USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_payables.settlement_observations TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_payables.accounting_receipts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_payables.accounting_receipts FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY accounting_receipts_tenant_isolation ON mod_payables.accounting_receipts USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id());"
    )
    op.execute("GRANT SELECT, INSERT ON mod_payables.accounting_receipts TO app_user;")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS accounting_receipts_immutable ON mod_payables.accounting_receipts;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS settlement_observations_immutable ON mod_payables.settlement_observations;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS credit_applications_immutable ON mod_payables.credit_applications;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS liability_events_immutable ON mod_payables.liability_events;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS credit_note_lines_submitted_immutable ON mod_payables.credit_note_lines;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS supplier_invoice_lines_submitted_immutable ON mod_payables.supplier_invoice_lines;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS credit_notes_submitted_immutable ON mod_payables.credit_notes;"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS supplier_invoices_submitted_immutable ON mod_payables.supplier_invoices;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS mod_payables.protect_immutable_payables_evidence();"
    )
    op.execute("DROP FUNCTION IF EXISTS mod_payables.protect_payables_line();")
    op.execute("DROP FUNCTION IF EXISTS mod_payables.protect_payables_document();")
    for table in (
        "accounting_receipts",
        "settlement_observations",
        "credit_applications",
        "liability_events",
        "payment_obligations",
        "credit_note_lines",
        "credit_notes",
        "supplier_invoice_lines",
        "supplier_invoices",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_payables;")
