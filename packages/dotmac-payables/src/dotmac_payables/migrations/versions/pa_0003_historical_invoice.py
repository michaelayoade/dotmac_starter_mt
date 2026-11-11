"""Permanent historical invoice recognition evidence; no duplicate GL posting."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "pa_0003_historical_invoice"
down_revision = "pa_0002_optional_supplier_doc"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "historical_invoice_receipts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("obligation_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_invoice_id", sa.String(255), nullable=False),
        sa.Column("source_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(68), nullable=False),
        sa.Column("facts_fingerprint", sa.String(68), nullable=False),
        sa.Column("accounting_evidence_fingerprint", sa.String(68), nullable=False),
        sa.Column("source_journal_ref", sa.String(255), nullable=False),
        sa.Column("destination_journal_ref", sa.String(255), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("original_posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_historical_invoice_receipts_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "invoice_id", name="uq_historical_invoice_receipts_invoice"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_invoice_id",
            name="uq_historical_invoice_receipts_source",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["public.tenants.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "invoice_id"],
            [
                "mod_payables.supplier_invoices.tenant_id",
                "mod_payables.supplier_invoices.id",
            ],
            ondelete="RESTRICT",
            name="fk_historical_invoice_receipts_invoice",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "obligation_id"],
            [
                "mod_payables.payment_obligations.tenant_id",
                "mod_payables.payment_obligations.id",
            ],
            ondelete="RESTRICT",
            name="fk_historical_invoice_receipts_obligation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "event_id"],
            [
                "mod_payables.liability_events.tenant_id",
                "mod_payables.liability_events.id",
            ],
            ondelete="RESTRICT",
            name="fk_historical_invoice_receipts_event",
        ),
        schema="mod_payables",
    )
    op.execute(
        "ALTER TABLE mod_payables.historical_invoice_receipts ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE mod_payables.historical_invoice_receipts FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY historical_invoice_receipts_tenant_isolation ON mod_payables.historical_invoice_receipts USING (tenant_id=public.app_current_tenant_id()) WITH CHECK (tenant_id=public.app_current_tenant_id())"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_payables.historical_invoice_receipts TO app_user"
    )
    op.execute(
        "CREATE TRIGGER historical_invoice_receipts_immutable BEFORE UPDATE OR DELETE ON mod_payables.historical_invoice_receipts FOR EACH ROW EXECUTE FUNCTION mod_payables.protect_immutable_payables_evidence()"
    )


def downgrade() -> None:
    # Permanent recognition evidence cannot be discarded by a schema rollback.
    # Once populated, recovery requires a forward correction.
    op.execute(
        "LOCK TABLE mod_payables.historical_invoice_receipts "
        "IN ACCESS EXCLUSIVE MODE"
    )
    if op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM mod_payables.historical_invoice_receipts)"
        )
    ):
        raise RuntimeError("historical invoice receipts require forward correction")
    op.drop_table("historical_invoice_receipts", schema="mod_payables")
