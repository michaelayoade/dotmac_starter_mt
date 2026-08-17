"""Add indexed, append-only product-port shadow evidence.

The independently deployed Integrator needs to compare its provider-neutral
delivery request with what the destination application recorded before that
capability can cut over.  The destination owns the comparison; this table owns
only safe orchestration evidence keyed by the Integrator's receipt UUID and an
explicit comparison revision.

This is not the kernel platform audit log.  A scheduled comparison is
high-volume module state queried on every worker poll.  Storing it in the
global operator-action ledger would turn a reusable module concern into an
ever-growing cross-domain JSON scan and make the thin assembly its owner.

Platform plane: no ``tenant_id``, no RLS, SELECT/INSERT for the online platform
role, and REVOKE ALL from ``app_user``.  UPDATE and DELETE are deliberately not
granted: evidence is appended, never rewritten.

Revision ID: ig_0010_shadow_evidence
Revises: ig_0009_product_port_desc
Create Date: 2026-08-17
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ig_0010_shadow_evidence"
down_revision = "ig_0009_product_port_desc"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_TABLE = "shadow_comparison_evidence"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        sa.Column("comparison_revision", sa.String(160), nullable=False),
        sa.Column("verdict", sa.String(40), nullable=False),
        sa.Column("blocking_reasons", postgresql.JSONB(), nullable=False),
        sa.Column("disagreeing_fields", postgresql.JSONB(), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "verdict IN ('agrees', 'field_disagreement', "
            "'identity_shape_mismatch', 'collision', 'no_counterpart', "
            "'unreadable', 'unrecognized')",
            name="ck_shadow_comparison_evidence_verdict",
        ),
        sa.ForeignKeyConstraint(
            ["receipt_id"],
            ["mod_intg.inbox_receipts.id"],
            ondelete="CASCADE",
            name="fk_shadow_evidence_receipt",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_shadow_evidence_revision_receipt_latest",
        _TABLE,
        ["comparison_revision", "receipt_id", "observed_at", "id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_shadow_evidence_revision_observed",
        _TABLE,
        ["comparison_revision", "observed_at"],
        schema=_SCHEMA,
    )

    # Literal, never looped: the composed gate reads this file statically.
    # REVOKE last so a later grant cannot silently outrank the isolation.
    op.execute(
        "GRANT SELECT, INSERT ON mod_intg.shadow_comparison_evidence "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT ON mod_intg.shadow_comparison_evidence TO app_admin;"
    )
    # BIGSERIAL's sequence is a separate PostgreSQL privilege object. Table
    # INSERT without sequence USAGE is declared-reachable but fails on the first
    # real append, so both halves are explicit and independently canaried.
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE "
        "mod_intg.shadow_comparison_evidence_id_seq TO platform_api;"
    )
    op.execute("REVOKE ALL ON mod_intg.shadow_comparison_evidence FROM app_user;")
    op.execute(
        "REVOKE ALL ON SEQUENCE mod_intg.shadow_comparison_evidence_id_seq "
        "FROM app_user;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_intg.shadow_comparison_evidence CASCADE;")
