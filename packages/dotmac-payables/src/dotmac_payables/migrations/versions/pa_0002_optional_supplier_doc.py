"""Allow uncaptured supplier-issued document numbers to persist as NULL.

Revision ID: pa_0002_optional_supplier_doc
Revises: pa_0001_payables
Create Date: 2026-11-10
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "pa_0002_optional_supplier_doc"
down_revision = "pa_0001_payables"
branch_labels = None
depends_on = None

_SCHEMA = "mod_payables"
_TABLES = ("supplier_invoices", "credit_notes")


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(
            table,
            "supplier_document_number",
            existing_type=sa.String(120),
            nullable=True,
            schema=_SCHEMA,
        )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.alter_column(
            table,
            "supplier_document_number",
            existing_type=sa.String(120),
            nullable=False,
            schema=_SCHEMA,
        )
