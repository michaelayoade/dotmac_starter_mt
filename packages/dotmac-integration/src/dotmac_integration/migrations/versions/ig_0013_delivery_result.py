"""Persist the domain-normalized result of a delivered command.

``Outcome.result`` was validated before settlement but discarded immediately.
That made request/response capabilities appear successful while losing the
checkout URL, resolved account, payee reference or other domain result the
caller needed. The column stores only the owning capability contract's
validated result, never a raw provider response, and retention clears it with
the command payload under the same legal hold.

Revision ID: ig_0013_delivery_result
Revises: ig_0012_delivery_evidence
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ig_0013_delivery_result"
down_revision = "ig_0012_delivery_evidence"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_DELIVERIES = "delivery_attempts"


def upgrade() -> None:
    op.add_column(
        _DELIVERIES,
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column(_DELIVERIES, "result_json", schema=_SCHEMA)
