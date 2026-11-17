"""Governed settlement reversal for bank-returned salary credits (additive).

Revision ID: py_0003_settlement_reversal
Revises: py_0002_mode_and_run_cancel

Adds the append-only ``payroll_settlement_reversals`` table. Existing tables and
rows are untouched, so released a3/a4 behaviour is unchanged. Downgrade refuses
once any reversal exists, because dropping it would silently re-close liabilities
that were reopened for re-payment.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "py_0003_settlement_reversal"
down_revision = "py_0002_mode_and_run_cancel"
branch_labels = None
depends_on = None

_SCHEMA = "mod_payroll"
_TABLE = "payroll_settlement_reversals"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("settlement_id", sa.Uuid(), nullable=False),
        sa.Column("liability_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("return_ref", sa.String(240), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reversed_by_id", sa.Uuid(), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_payroll_settlement_reversals_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_payroll_settlement_reversals_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "settlement_id",
            name="uq_payroll_settlement_reversals_settlement",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "settlement_id"],
            [
                "mod_payroll.payroll_liability_settlements.tenant_id",
                "mod_payroll.payroll_liability_settlements.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_settlement_reversals_settlement",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "liability_id"],
            [
                "mod_payroll.payroll_liabilities.tenant_id",
                "mod_payroll.payroll_liabilities.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_settlement_reversals_liability",
        ),
        sa.CheckConstraint("amount > 0", name="ck_payroll_settlement_reversals_amount"),
        schema=_SCHEMA,
    )
    op.execute(
        f"""
        CREATE TRIGGER protect_payroll_evidence BEFORE UPDATE OR DELETE ON {_SCHEMA}.{_TABLE} FOR EACH ROW EXECUTE FUNCTION mod_payroll.protect_payroll_evidence();
        ALTER TABLE {_SCHEMA}.{_TABLE} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {_SCHEMA}.{_TABLE} FORCE ROW LEVEL SECURITY;
        CREATE POLICY {_TABLE}_tenant_isolation ON {_SCHEMA}.{_TABLE} USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());
        GRANT SELECT, INSERT ON {_SCHEMA}.{_TABLE} TO app_user;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    refused = bind.execute(
        sa.text("SELECT count(*) FROM mod_payroll.payroll_settlement_reversals")
    ).scalar_one()
    if refused:
        raise RuntimeError(
            "payroll a5 downgrade refused: settlement reversals exist "
            "(dropping them would re-close reopened liabilities)"
        )
    op.drop_table(_TABLE, schema=_SCHEMA)
