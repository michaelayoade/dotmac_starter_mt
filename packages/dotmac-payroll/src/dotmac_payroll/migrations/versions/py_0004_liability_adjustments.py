"""Audited liability adjustments (additive).

Revision ID: py_0004_liability_adjustments
Revises: py_0003_settlement_reversal

Adds ``payroll_liabilities.amount_adjusted`` (default 0, so every existing row
and released a5 behaviour is unchanged), widens the amounts CHECK to the
effective amount (``amount + amount_adjusted``) and adds the append-only
``payroll_liability_adjustments`` table. ``payroll_liabilities.amount`` is the
calculation-bound original and is never changed. Downgrade refuses once an
adjustment exists, because dropping the rows would silently change what is owed.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "py_0004_liability_adjustments"
down_revision = "py_0003_settlement_reversal"
branch_labels = None
depends_on = None

_SCHEMA = "mod_payroll"
_TABLE = "payroll_liability_adjustments"
_CHECK = "ck_payroll_liabilities_amounts"
_NEW_CHECK = (
    "amount >= 0 AND amount_settled >= 0 "
    "AND amount + amount_adjusted >= 0 "
    "AND amount_settled <= amount + amount_adjusted"
)
_OLD_CHECK = "amount >= 0 AND amount_settled >= 0 AND amount_settled <= amount"


def upgrade() -> None:
    op.add_column(
        "payroll_liabilities",
        sa.Column(
            "amount_adjusted",
            sa.Numeric(20, 6),
            nullable=False,
            server_default="0",
        ),
        schema=_SCHEMA,
    )
    op.drop_constraint(_CHECK, "payroll_liabilities", schema=_SCHEMA, type_="check")
    op.create_check_constraint(
        _CHECK, "payroll_liabilities", _NEW_CHECK, schema=_SCHEMA
    )
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("liability_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(20, 6), nullable=False),
        sa.Column("adjustment_ref", sa.String(240), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("evidence_ref", sa.String(240), nullable=False),
        sa.Column("offset_account_ref", sa.String(240), nullable=False),
        sa.Column("adjusted_by_id", sa.Uuid(), nullable=False),
        sa.Column("adjusted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_payroll_liability_adjustments_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_payroll_liability_adjustments_tenant_id_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "liability_id",
            "adjustment_ref",
            name="uq_payroll_liability_adjustments_ref",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "liability_id"],
            [
                "mod_payroll.payroll_liabilities.tenant_id",
                "mod_payroll.payroll_liabilities.id",
            ],
            ondelete="RESTRICT",
            name="fk_payroll_liability_adjustments_liability",
        ),
        sa.CheckConstraint(
            "amount <> 0", name="ck_payroll_liability_adjustments_amount"
        ),
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
        sa.text("SELECT count(*) FROM mod_payroll.payroll_liability_adjustments")
    ).scalar_one()
    if refused:
        raise RuntimeError(
            "payroll a6 downgrade refused: liability adjustments exist "
            "(dropping them would silently change what is owed)"
        )
    op.drop_table(_TABLE, schema=_SCHEMA)
    op.drop_constraint(_CHECK, "payroll_liabilities", schema=_SCHEMA, type_="check")
    op.create_check_constraint(
        _CHECK, "payroll_liabilities", _OLD_CHECK, schema=_SCHEMA
    )
    op.drop_column("payroll_liabilities", "amount_adjusted", schema=_SCHEMA)
