"""Add bounded outbound provider evidence and delivery legal holds.

A successful WhatsApp send returns a provider message reference used by later
status callbacks. The generic dispatch engine previously discarded it, making
outbound reconciliation impossible. The two nullable evidence columns are
typed and bounded; no arbitrary provider response body is stored.

Outbound payloads carry customer message content. ``delivery_legal_holds`` is
the durable instruction that prevents a terminal delivery's payload from
ageing out under the explicit integration retention policy.

Revision ID: ig_0012_delivery_evidence
Revises: ig_0011_replay_retention
Create Date: 2026-08-23
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0012_delivery_evidence"
down_revision = "ig_0011_replay_retention"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_DELIVERIES = "delivery_attempts"
_HOLDS = "delivery_legal_holds"


def upgrade() -> None:
    op.add_column(
        _DELIVERIES,
        sa.Column("provider_reference", sa.String(500), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _DELIVERIES,
        sa.Column("provider_status_code", sa.Integer(), nullable=True),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_delivery_attempts_provider_status",
        _DELIVERIES,
        "provider_status_code IS NULL OR provider_status_code BETWEEN 100 AND 599",
        schema=_SCHEMA,
    )

    op.create_table(
        _HOLDS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("policy_owner", sa.String(160), nullable=False),
        sa.Column("placed_by", sa.String(160), nullable=False),
        sa.Column(
            "placed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.String(160), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["delivery_id"],
            ["mod_intg.delivery_attempts.id"],
            ondelete="CASCADE",
            name="fk_delivery_legal_holds_delivery",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_delivery_legal_holds_active",
        _HOLDS,
        ["delivery_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "ix_delivery_legal_holds_released",
        _HOLDS,
        ["released_at"],
        schema=_SCHEMA,
    )

    # Literal grants keep the composed live-catalog audit non-vacuous.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.delivery_legal_holds "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.delivery_legal_holds "
        "TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.delivery_legal_holds FROM app_user;")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_intg.delivery_legal_holds CASCADE;")
    op.drop_constraint(
        "ck_delivery_attempts_provider_status",
        _DELIVERIES,
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_column(_DELIVERIES, "provider_status_code", schema=_SCHEMA)
    op.drop_column(_DELIVERIES, "provider_reference", schema=_SCHEMA)
