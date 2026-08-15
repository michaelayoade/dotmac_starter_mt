"""Payload retention — the legal-hold ledger, and nothing else.

Retention redacts CONTENT in place: `payload_json`, `headers_json` and the
values inside `consequence_json` on `inbox_receipts`. That needs no schema
change at all, and deliberately gets none — every identity, ordering and
outcome column stays exactly as `ig_0002` created it, because a redacted
receipt must still deduplicate a provider's redelivery months later.

What DOES need a table is the one fact the ledger cannot derive: that a human
has instructed us to keep a particular receipt's content. `receipt_legal_holds`
is that instruction, and it is inserted and released rather than deleted —
"was this ever held, and by whom?" is a question asked after the hold is lifted.

## Same PLATFORM plane as everything else in `mod_intg`

No `tenant_id`, no RLS, GRANT to the platform roles and **REVOKE ALL from
`app_user`** (ADR-0023): on this plane the privilege boundary IS the isolation,
and `tests/test_integration_isolation.py` audits it across all seven privileges
and their column-level forms.

## The index is PARTIAL, and that is the constraint that matters

Many released holds may accumulate on one receipt over the years; two ACTIVE
holds must never exist, because then "is this held?" has two rows and two
owners, and releasing one reads as releasing the hold. `WHERE released_at IS
NULL` is what makes "at most one active hold" a database fact rather than a
service convention.

## Lineage note for the reviewer

`down_revision` is `ig_0002_execution` because that is this branch's head at the
commit this work started from. Team 2's ingress slice introduces `ig_0003`
concurrently; whichever lands second REBASES onto the other so the `ig` branch
keeps a single head. Do not merge this with two heads outstanding.

Revision ID: ig_0004_retention
Revises: ig_0002_execution
Create Date: 2026-08-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0004_retention"
down_revision = "ig_0002_execution"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_HOLDS = "receipt_legal_holds"


def upgrade() -> None:
    op.create_table(
        _HOLDS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("receipt_id", sa.Uuid(), nullable=False),
        # Required. An unexplained hold cannot be reviewed, and the person who
        # could explain it has left by the time anyone asks.
        sa.Column("reason", sa.Text(), nullable=False),
        # The accountable owner AT PLACEMENT, copied rather than looked up: the
        # point of the field is who owned this decision when it was made, which
        # today's configuration value cannot answer.
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
            ["receipt_id"],
            ["mod_intg.inbox_receipts.id"],
            ondelete="CASCADE",
            name="fk_receipt_legal_holds_receipt",
        ),
        schema=_SCHEMA,
    )
    # PARTIAL unique — see the module docstring. A plain UNIQUE on receipt_id
    # would forbid the history this table exists to keep.
    op.create_index(
        "uq_receipt_legal_holds_active",
        _HOLDS,
        ["receipt_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "ix_receipt_legal_holds_released",
        _HOLDS,
        ["released_at"],
        schema=_SCHEMA,
    )

    # Literal, never looped: the composed gate reads this file statically.
    # REVOKE last, so a later added grant cannot outrank it.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.receipt_legal_holds "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.receipt_legal_holds "
        "TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.receipt_legal_holds FROM app_user;")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_intg.receipt_legal_holds CASCADE;")
