"""Add the durable PROVISION runtime tables and fleet-target revisions.

SPI 1.5's provisioning contract shipped persistence-free on purpose: the
contract could be proved without a table. This revision is the stateful half
that contract named. `provision_operations` holds one product-authored plan
per submission identity (`installation_id`, `command_id`); its steps are the
plan's only stored form in `provision_step_attempts`, which is delivery-shaped
on purpose — lease, `next_attempt_at` floor, per-step idempotency key — with
one addition a delivery does not need: `next_action`, the engine-set word
that guarantees an apply whose outcome is unknown is followed by `observe`
and never by a blind re-apply.

`connector_config_revisions` gains nullable `network_targets_json` (SPI 1.7,
ADR-0074): installation-scoped fleet CIDRs narrowing the manifest's
protocol/port ceiling. NULL predates the declaration; an empty list is an
explicit deny-all; the two must not be conflated, which is why the column is
nullable rather than defaulted.

No table here can hold a secret. Step inputs are validated secret-material-
free before submission writes them (identifiers and versions only, ADR-0076
§7), evidence is typed and bounded, and there is deliberately no free-text
error column — `error_code` is a closed machine vocabulary.

Platform plane: no `tenant_id`, no RLS, SELECT/INSERT/UPDATE/DELETE for the
platform roles (these are work queues, not append-only evidence), REVOKE ALL
from `app_user` last.

Revision ID: ig_0017_provisioning
Revises: ig_0016_receipt_origin
Create Date: 2026-10-27
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ig_0017_provisioning"
down_revision = "ig_0016_receipt_origin"
branch_labels = None
depends_on = None

_SCHEMA = "mod_intg"
_REVISIONS = "connector_config_revisions"
_OPERATIONS = "provision_operations"
_STEPS = "provision_step_attempts"


def upgrade() -> None:
    op.add_column(
        _REVISIONS,
        sa.Column(
            "network_targets_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        _OPERATIONS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("installation_id", sa.Uuid(), nullable=False),
        sa.Column("capability_binding_id", sa.Uuid(), nullable=False),
        sa.Column("capability_id", sa.String(160), nullable=False),
        sa.Column("command_id", sa.String(240), nullable=False),
        sa.Column("plan_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), server_default="planned", nullable=False),
        sa.Column("plan_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "plan_attempt_count", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.String(240), nullable=True),
        sa.Column("correlation_id", sa.String(120), nullable=True),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "installation_id", "command_id", name="uq_provision_operations_command"
        ),
        sa.CheckConstraint(
            "state IN ('planned', 'applying', 'succeeded', 'failed', "
            "'cancelled', 'reconciliation_required')",
            name="ck_provision_operations_state",
        ),
        sa.CheckConstraint(
            "length(plan_hash) = 64", name="ck_provision_operations_plan_hash_len"
        ),
        sa.CheckConstraint(
            "plan_hash ~ '^[0-9a-f]{64}$'",
            name="ck_provision_operations_plan_hash",
        ),
        sa.CheckConstraint(
            "plan_attempt_count >= 0", name="ck_provision_operations_attempts"
        ),
        sa.ForeignKeyConstraint(
            ["installation_id"],
            ["mod_intg.connector_installations.id"],
            ondelete="CASCADE",
            name="fk_provision_operations_installation",
        ),
        sa.ForeignKeyConstraint(
            ["capability_binding_id"],
            ["mod_intg.capability_bindings.id"],
            ondelete="CASCADE",
            name="fk_provision_operations_binding",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_provision_operations_state_next",
        _OPERATIONS,
        ["state", "next_attempt_at", "leased_until"],
        schema=_SCHEMA,
    )

    op.create_table(
        _STEPS,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("operation_id", sa.Uuid(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_key", sa.String(160), nullable=False),
        sa.Column("endpoint_code", sa.String(160), nullable=False),
        sa.Column(
            "depends_on_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "input_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(500), nullable=False),
        sa.Column("state", sa.String(32), server_default="pending", nullable=False),
        sa.Column("next_action", sa.String(16), server_default="apply", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_operation_ref", sa.String(500), nullable=True),
        sa.Column(
            "evidence_json",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "operation_id", "step_key", name="uq_provision_steps_operation_key"
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'in_flight', 'succeeded', 'observing', "
            "'retryable', 'terminal', 'ambiguous', 'cancelled', "
            "'reconciliation_required')",
            name="ck_provision_steps_state",
        ),
        sa.CheckConstraint(
            "next_action IN ('apply', 'observe', 'cancel')",
            name="ck_provision_steps_next_action",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_provision_steps_attempts"),
        sa.CheckConstraint("step_index >= 0", name="ck_provision_steps_index"),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["mod_intg.provision_operations.id"],
            ondelete="CASCADE",
            name="fk_provision_steps_operation",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_provision_steps_due",
        _STEPS,
        ["operation_id", "state", "next_attempt_at", "leased_until"],
        schema=_SCHEMA,
    )

    # Literal, never looped: the composed gate reads this file statically.
    # REVOKE last so a later grant cannot silently outrank the isolation.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_intg.provision_operations TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_intg.provision_operations TO app_admin;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_intg.provision_step_attempts TO platform_api;"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "mod_intg.provision_step_attempts TO app_admin;"
    )
    op.execute("REVOKE ALL ON mod_intg.provision_operations FROM app_user;")
    op.execute("REVOKE ALL ON mod_intg.provision_step_attempts FROM app_user;")


def downgrade() -> None:
    # Durable submitted work is not downgraded away silently: an operation a
    # product issued and the engine has not finished is evidence, and a
    # downgrade that dropped it would make "did the plan land?" unanswerable.
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM mod_intg.provision_operations) THEN "
        "RAISE EXCEPTION 'ig_0017 downgrade refuses while provision "
        "operations exist'; "
        "END IF; "
        "IF EXISTS (SELECT 1 FROM mod_intg.connector_config_revisions "
        "WHERE network_targets_json IS NOT NULL) THEN "
        "RAISE EXCEPTION 'ig_0017 downgrade refuses while network targets "
        "are declared'; "
        "END IF; END $$;"
    )
    op.execute("DROP TABLE IF EXISTS mod_intg.provision_step_attempts CASCADE;")
    op.execute("DROP TABLE IF EXISTS mod_intg.provision_operations CASCADE;")
    op.drop_column(_REVISIONS, "network_targets_json", schema=_SCHEMA)
