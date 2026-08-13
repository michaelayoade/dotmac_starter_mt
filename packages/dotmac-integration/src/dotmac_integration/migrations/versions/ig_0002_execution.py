"""Execution machinery — inbox, outbox and polling checkpoints.

Slice 2 of the extraction from `dotmac_sub`. Same PLATFORM plane as `ig_0001`:
no `tenant_id`, no RLS, GRANT to the platform roles and **REVOKE ALL from
`app_user`**, because on this plane the privilege boundary IS the isolation.

## Three mechanisms, three different concurrency defences

`inbox_receipts` — `(installation_id, provider_event_id)` UNIQUE is the
deduplication key, in the database rather than in a service, because two webhook
workers racing one redelivery is the normal case. `payload_digest` is what lets
the service tell a redelivery from a provider identity collision.

`delivery_attempts` — `leased_until` is a claim. Two dispatchers without it both
call the provider, and no downstream idempotency repairs a provider that has
already seen two requests. `(installation_id, idempotency_key)` UNIQUE
deduplicates ENQUEUE only; at-most-once EXECUTION belongs to
`dotmac_kernel.idempotency` (ADR-0014, hard rule 21) and is adapted rather than
rebuilt here.

`polling_checkpoints` — an optimistic `version` instead. The risk there is not a
double call but a silently skipped window: the slower of two writers wins and
the range between the cursors is never polled again.

Revision ID: ig_0002_execution
Revises: ig_0001_connector_cp
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ig_0002_execution"
down_revision = "ig_0001_connector_cp"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_INBOX = "inbox_receipts"
_DELIVERIES = "delivery_attempts"
_CHECKPOINTS = "polling_checkpoints"

_JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        _INBOX,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("capability_binding_id", sa.Uuid(), nullable=True),
        sa.Column("provider_event_id", sa.String(240), nullable=False),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("payload_json", _JSON, nullable=True),
        sa.Column("headers_json", _JSON, nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="verified"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consequence_json", _JSON, nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["mod_intg.connector_installations.id"],
            ondelete="CASCADE",
            name="fk_inbox_receipts_installation",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "provider_event_id",
            name="uq_inbox_receipts_installation_event",
        ),
        sa.CheckConstraint(
            "state IN ('verified', 'processing', 'processed', 'retryable', "
            "'reconciliation_required', 'dead_letter')",
            name="ck_inbox_receipts_state",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_inbox_receipts_state_received",
        _INBOX,
        ["state", "received_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        _DELIVERIES,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("capability_binding_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(160), nullable=False),
        sa.Column("idempotency_key", sa.String(240), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("payload_json", _JSON, nullable=True),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["mod_intg.connector_installations.id"],
            ondelete="CASCADE",
            name="fk_delivery_attempts_installation",
        ),
        sa.UniqueConstraint(
            "installation_id",
            "idempotency_key",
            name="uq_delivery_attempts_installation_key",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'in_flight', 'delivered', 'retryable', "
            "'reconciliation_required', 'dead_letter')",
            name="ck_delivery_attempts_state",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_delivery_attempts_attempts"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_delivery_attempts_state_next",
        _DELIVERIES,
        ["state", "next_attempt_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        _CHECKPOINTS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("capability_binding_id", sa.Uuid(), nullable=False),
        sa.Column("job_key", sa.String(160), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("cursor_json", _JSON, nullable=True),
        sa.Column("advanced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["capability_binding_id"],
            ["mod_intg.capability_bindings.id"],
            ondelete="CASCADE",
            name="fk_polling_checkpoints_binding",
        ),
        sa.UniqueConstraint(
            "capability_binding_id",
            "job_key",
            name="uq_polling_checkpoints_binding_job",
        ),
        sa.CheckConstraint("version >= 1", name="ck_polling_checkpoints_version"),
        schema=_SCHEMA,
    )

    # Literal per table, never looped: the composed gate reads this file
    # statically. REVOKEs last, so a later added grant cannot outrank them.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.inbox_receipts "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.inbox_receipts TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.delivery_attempts "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.delivery_attempts "
        "TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.polling_checkpoints "
        "TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_intg.polling_checkpoints "
        "TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.inbox_receipts FROM app_user;")
    op.execute("REVOKE ALL ON mod_intg.delivery_attempts FROM app_user;")
    op.execute("REVOKE ALL ON mod_intg.polling_checkpoints FROM app_user;")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS mod_intg.polling_checkpoints CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_intg.delivery_attempts CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_intg.inbox_receipts CASCADE;")
