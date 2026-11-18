"""Deduction settlement mode and payroll-run cancellation evidence (additive).

Revision ID: py_0002_mode_and_run_cancel
Revises: py_0001_payroll

Adds ``settlement_mode`` ('external' default | 'internal_recovery') to rules,
calculation lines and liabilities, and cancel evidence columns to payroll runs.
Existing rows backfill to 'external' through the column default, so released
behaviour is unchanged. Adding a defaulted column does not fire the
append-only evidence triggers.
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "py_0002_mode_and_run_cancel"
down_revision = "py_0001_payroll"
branch_labels = None
depends_on = None

_SCHEMA = "mod_payroll"
_MODE_CHECK = "settlement_mode IN ('external','internal_recovery')"
_MODE_TABLES = (
    ("pay_structure_rules", "ck_pay_structure_rules_settlement_mode"),
    ("payroll_calculation_lines", "ck_payroll_calculation_lines_settlement_mode"),
    ("payroll_liabilities", "ck_payroll_liabilities_settlement_mode"),
)
CANCEL_PRESENT_CHECK = (
    "status <> 'cancelled' OR (cancelled_by_id IS NOT NULL "
    "AND cancelled_at IS NOT NULL AND cancel_reason IS NOT NULL)"
)
CANCEL_ABSENT_CHECK = (
    "status = 'cancelled' OR (cancelled_by_id IS NULL "
    "AND cancelled_at IS NULL AND cancel_reason IS NULL)"
)


def upgrade() -> None:
    for table, check in _MODE_TABLES:
        op.add_column(
            table,
            sa.Column(
                "settlement_mode",
                sa.String(24),
                nullable=False,
                server_default="external",
            ),
            schema=_SCHEMA,
        )
        op.create_check_constraint(check, table, _MODE_CHECK, schema=_SCHEMA)
    op.add_column(
        "payroll_runs",
        sa.Column("cancelled_by_id", sa.Uuid(), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        "payroll_runs",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        "payroll_runs",
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_payroll_runs_cancel_present",
        "payroll_runs",
        CANCEL_PRESENT_CHECK,
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_payroll_runs_cancel_absent",
        "payroll_runs",
        CANCEL_ABSENT_CHECK,
        schema=_SCHEMA,
    )


def downgrade() -> None:
    bind = op.get_bind()
    refused = bind.execute(
        sa.text(
            "SELECT (SELECT count(*) FROM mod_payroll.payroll_runs "
            "WHERE status = 'cancelled') + "
            "(SELECT count(*) FROM mod_payroll.pay_structure_rules "
            "WHERE settlement_mode <> 'external') + "
            "(SELECT count(*) FROM mod_payroll.payroll_calculation_lines "
            "WHERE settlement_mode <> 'external') + "
            "(SELECT count(*) FROM mod_payroll.payroll_liabilities "
            "WHERE settlement_mode <> 'external')"
        )
    ).scalar_one()
    if refused:
        raise RuntimeError(
            "payroll a4 downgrade refused: cancelled runs or internal-recovery "
            "rows exist"
        )
    op.drop_constraint("ck_payroll_runs_cancel_absent", "payroll_runs", schema=_SCHEMA)
    op.drop_constraint("ck_payroll_runs_cancel_present", "payroll_runs", schema=_SCHEMA)
    for column in ("cancel_reason", "cancelled_at", "cancelled_by_id"):
        op.drop_column("payroll_runs", column, schema=_SCHEMA)
    for table, check in reversed(_MODE_TABLES):
        op.drop_constraint(check, table, schema=_SCHEMA)
        op.drop_column(table, "settlement_mode", schema=_SCHEMA)
