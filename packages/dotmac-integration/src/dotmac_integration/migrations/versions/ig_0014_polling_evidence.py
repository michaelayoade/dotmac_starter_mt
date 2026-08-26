"""Add durable polling attempt, failure and backoff evidence.

The three-phase POLL engine recorded a success (receipts plus an advanced
cursor) and recorded nothing at all about a failure. The durable state could
not say how many times in a row a job had failed, when it might safely be tried
again, or what kind of failure it was — so every assembly running a poll worker
had to keep its own attempt counter and its own backoff, which is a parallel
retry ledger and a second writer of a decision the module owns half of.

Two halves, one revision, because they are one fact. The checkpoint gains the
CURRENT retry state, indexed as the `(next_attempt_at, id)` keyset ordering key
a bounded selection walks; `polling_attempt_failures` gains the per-attempt
history.

The history table holds no message, payload, header or provider column. That
absence is the enforcement: exception text cannot reach a column that does not
exist. `connector_exception` is the single bounded exception, and it stores a
sanitized Python identifier — a type name — never a message.

`next_attempt_at` is NOT NULL so the keyset ordering key is total. Existing
rows are backfilled from the checkpoint's own history (last advance, else
creation) before the constraint is applied, which leaves an already-polled job
ordered behind a newly created one rather than all of them tied at the moment
of the migration.

Platform plane: no ``tenant_id``, no RLS, SELECT/INSERT/DELETE for the online
platform role, and REVOKE ALL from ``app_user``. UPDATE is deliberately not
granted: evidence is never rewritten. DELETE exists only for the typed bounded
retention sweep, whose cutoff the product must supply explicitly.

Revision ID: ig_0014_polling_evidence
Revises: ig_0013_delivery_result
Create Date: 2026-08-25
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "ig_0014_polling_evidence"
down_revision = "ig_0013_delivery_result"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_CHECKPOINTS = "polling_checkpoints"
_FAILURES = "polling_attempt_failures"


def upgrade() -> None:
    op.add_column(
        _CHECKPOINTS,
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        schema=_SCHEMA,
    )
    # Added nullable, backfilled, then constrained. A NOT NULL column with a
    # `now()` default would have been one statement and would have stamped every
    # existing checkpoint with the same instant, destroying the ordering that
    # says which job has waited longest — on the deployments that already have a
    # polling backlog, which are exactly the ones this change is for.
    op.add_column(
        _CHECKPOINTS,
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.execute(
        "UPDATE mod_intg.polling_checkpoints "
        "SET next_attempt_at = COALESCE(advanced_at, created_at) "
        "WHERE next_attempt_at IS NULL;"
    )
    op.alter_column(
        _CHECKPOINTS,
        "next_attempt_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
        schema=_SCHEMA,
    )
    op.add_column(
        _CHECKPOINTS,
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _CHECKPOINTS,
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _CHECKPOINTS,
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.add_column(
        _CHECKPOINTS,
        sa.Column("last_failure_code", sa.String(40), nullable=True),
        schema=_SCHEMA,
    )
    op.create_check_constraint(
        "ck_polling_checkpoints_attempt_count",
        _CHECKPOINTS,
        "attempt_count >= 0",
        schema=_SCHEMA,
    )
    # A code with no time, or a time with no code, is a half-written record that
    # still reads as evidence.
    op.create_check_constraint(
        "ck_polling_checkpoints_failure_pairing",
        _CHECKPOINTS,
        "(last_failure_code IS NULL) = (last_failure_at IS NULL)",
        schema=_SCHEMA,
    )
    # The keyset ordering key, as one composite index: the selection compares
    # `(next_attempt_at, id)` as a pair, and a single-column index on the
    # timestamp would leave the tiebreak to a sort.
    op.create_index(
        "ix_polling_checkpoints_due",
        _CHECKPOINTS,
        ["next_attempt_at", "id"],
        schema=_SCHEMA,
    )

    op.create_table(
        _FAILURES,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("checkpoint_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("checkpoint_version", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.String(40), nullable=False),
        sa.Column("connector_exception", sa.String(120), nullable=True),
        sa.Column("retry_in_seconds", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        # The engine's CLOSED vocabulary, spelled out rather than generated from
        # the Python tuple: a check constraint that a code change could widen
        # without a migration is not a constraint on the data.
        sa.CheckConstraint(
            "failure_code IN ('checkpoint_unavailable', 'cursor_invalid', "
            "'handler_unavailable', 'secrets_unavailable', 'contract_violated', "
            "'connector_raised', 'checkpoint_conflict', 'settlement_failed')",
            name="ck_polling_attempt_failures_code",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1", name="ck_polling_attempt_failures_attempt"
        ),
        sa.CheckConstraint(
            "retry_in_seconds >= 0", name="ck_polling_attempt_failures_retry_in"
        ),
        sa.CheckConstraint(
            "checkpoint_version >= 1", name="ck_polling_attempt_failures_version"
        ),
        sa.ForeignKeyConstraint(
            ["checkpoint_id"],
            ["mod_intg.polling_checkpoints.id"],
            ondelete="CASCADE",
            name="fk_polling_attempt_failures_checkpoint",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_polling_attempt_failures_checkpoint_recent",
        _FAILURES,
        ["checkpoint_id", "observed_at", "id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_polling_attempt_failures_code_observed",
        _FAILURES,
        ["failure_code", "observed_at"],
        schema=_SCHEMA,
    )

    # Literal, never looped: the composed gate reads this file statically.
    # REVOKE last so a later grant cannot silently outrank the isolation.
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON "
        "mod_intg.polling_attempt_failures TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON "
        "mod_intg.polling_attempt_failures TO app_admin;"
    )
    # BIGSERIAL's sequence is a separate PostgreSQL privilege object. Table
    # INSERT without sequence USAGE is declared-reachable but fails on the first
    # real append, so both halves are explicit and independently canaried.
    op.execute(
        "GRANT USAGE, SELECT ON SEQUENCE "
        "mod_intg.polling_attempt_failures_id_seq TO platform_api;"
    )
    op.execute("REVOKE ALL ON mod_intg.polling_attempt_failures FROM app_user;")
    op.execute(
        "REVOKE ALL ON SEQUENCE mod_intg.polling_attempt_failures_id_seq "
        "FROM app_user;"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_intg.polling_attempt_failures CASCADE;")
    op.drop_index("ix_polling_checkpoints_due", _CHECKPOINTS, schema=_SCHEMA)
    op.drop_constraint(
        "ck_polling_checkpoints_failure_pairing",
        _CHECKPOINTS,
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_constraint(
        "ck_polling_checkpoints_attempt_count",
        _CHECKPOINTS,
        schema=_SCHEMA,
        type_="check",
    )
    op.drop_column(_CHECKPOINTS, "last_failure_code", schema=_SCHEMA)
    op.drop_column(_CHECKPOINTS, "last_failure_at", schema=_SCHEMA)
    op.drop_column(_CHECKPOINTS, "last_success_at", schema=_SCHEMA)
    op.drop_column(_CHECKPOINTS, "last_attempt_at", schema=_SCHEMA)
    op.drop_column(_CHECKPOINTS, "next_attempt_at", schema=_SCHEMA)
    op.drop_column(_CHECKPOINTS, "attempt_count", schema=_SCHEMA)
