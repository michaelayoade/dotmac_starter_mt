"""Preserve authoritative currency scale; legacy rows remain unresolved.

Revision ID: pm_0002_money_precision
Revises: pm_0001_payment_intents
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "pm_0002_money_precision"
down_revision = "pm_0001_payment_intents"
branch_labels = None
depends_on = None
_SCHEMA = "mod_payments"
_TABLES = (
    ("payment_intents", "ck_payment_intents_minor_units"),
    ("payment_transfer_proofs", "ck_transfer_proofs_minor_units"),
    ("payment_confirmations", "ck_payment_confirmations_minor_units"),
)


def upgrade() -> None:
    # Neither a currency-code lookup nor Numeric's exponent is authoritative
    # evidence of the original Money scale. No default and no inferred backfill.
    for table, constraint in _TABLES:
        op.add_column(
            table, sa.Column("minor_units", sa.Integer(), nullable=True), schema=_SCHEMA
        )
        op.create_check_constraint(
            constraint, table, "minor_units BETWEEN 0 AND 6", schema=_SCHEMA
        )


def downgrade() -> None:
    for table, constraint in reversed(_TABLES):
        op.drop_constraint(constraint, table, type_="check", schema=_SCHEMA)
        op.drop_column(table, "minor_units", schema=_SCHEMA)
