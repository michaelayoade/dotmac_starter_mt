"""Create the tenant-only outbound campaign progression owner.

Revision ID: ca_0001_campaigns
Revises: (lineage root)
Create Date: 2026-08-18

Every table carries ``UNIQUE (tenant_id, id)`` and every module foreign key
carries the tenant with the target id. RLS is enabled and forced in this same
revision. Snapshot triggers make revisions, steps, audiences and recipients
immutable after sending begins; the recipient trigger admits only the explicit
privacy-scrub transition.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "ca_0001_campaigns"
down_revision = None
branch_labels = ("campaigns",)

REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "idempotency_ledger.v1",
    "outbox_relay.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_campaigns"


def _identity(name: str) -> tuple[sa.Column[Any], ...]:
    return (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
    )


def _tenant_constraints(name: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name=f"fk_{name}_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name=f"uq_{name}_tenant_id_id"),
    )


def _timestamps(*, updated: bool = False) -> tuple[sa.Column[Any], ...]:
    columns: list[sa.Column[Any]] = [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        )
    ]
    if updated:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        )
    return tuple(columns)


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_campaigns;")
    op.execute(
        "GRANT USAGE ON SCHEMA mod_campaigns TO app_user, platform_api, app_admin;"
    )

    op.create_table(
        "campaigns",
        *_identity("campaigns"),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("active_revision_id", sa.Uuid(), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancellation_reason", sa.String(255), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pii_expires_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(updated=True),
        *_tenant_constraints("campaigns"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_campaigns_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaigns_tenant_id", "campaigns", ["tenant_id"], schema=_SCHEMA
    )
    op.create_index(
        "ix_campaigns_tenant_status",
        "campaigns",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_revisions",
        *_identity("campaign_revisions"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("send_window_start", sa.Time(), nullable=False),
        sa.Column("send_window_end", sa.Time(), nullable=False),
        sa.Column("sender_key", sa.String(120), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_tenant_constraints("campaign_revisions"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_revisions_campaign",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "campaign_id",
            "revision_number",
            name="uq_campaign_revisions_tenant_campaign_number",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_revisions_tenant_id",
        "campaign_revisions",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_revisions_tenant_campaign",
        "campaign_revisions",
        ["tenant_id", "campaign_id"],
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "fk_campaigns_active_revision",
        "campaigns",
        "campaign_revisions",
        ["tenant_id", "active_revision_id"],
        ["tenant_id", "id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "campaign_steps",
        *_identity("campaign_steps"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("delay_seconds", sa.Integer(), nullable=False),
        sa.Column("template_slug", sa.String(120), nullable=False),
        sa.Column("template_channel", sa.String(40), nullable=False),
        sa.Column("advance_on", postgresql.JSONB(), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_tenant_constraints("campaign_steps"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_steps_campaign",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                "mod_campaigns.campaign_revisions.tenant_id",
                "mod_campaigns.campaign_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_steps_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "revision_id",
            "position",
            name="uq_campaign_steps_tenant_revision_position",
        ),
        sa.CheckConstraint("position >= 0", name="ck_campaign_steps_position"),
        sa.CheckConstraint(
            "delay_seconds >= 0", name="ck_campaign_steps_delay_seconds"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_steps_tenant_id",
        "campaign_steps",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_steps_tenant_revision",
        "campaign_steps",
        ["tenant_id", "revision_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_audiences",
        *_identity("campaign_audiences"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_version", sa.String(120), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("eligibility_reason", sa.String(120), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_tenant_constraints("campaign_audiences"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_audiences_campaign",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "campaign_id",
            "source_owner",
            "source_version",
            "source_fingerprint",
            name="uq_campaign_audiences_source_snapshot",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_audiences_tenant_id",
        "campaign_audiences",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_audiences_campaign",
        "campaign_audiences",
        ["tenant_id", "campaign_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_recipients",
        *_identity("campaign_recipients"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("audience_id", sa.Uuid(), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_subject_id", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("address_hash", sa.String(64), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("eligibility_reason", sa.String(120), nullable=False),
        sa.Column("snapshot_fingerprint", sa.String(64), nullable=False),
        sa.Column("frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pii_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scrubbed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_tenant_constraints("campaign_recipients"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_recipients_campaign",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "audience_id"],
            [
                "mod_campaigns.campaign_audiences.tenant_id",
                "mod_campaigns.campaign_audiences.id",
            ],
            ondelete="RESTRICT",
            name="fk_campaign_recipients_audience",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "campaign_id",
            "source_owner",
            "source_subject_id",
            name="uq_campaign_recipients_source_subject",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_recipients_tenant_id",
        "campaign_recipients",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_recipients_campaign",
        "campaign_recipients",
        ["tenant_id", "campaign_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_recipients_address",
        "campaign_recipients",
        ["tenant_id", "address_hash"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_recipient_steps",
        *_identity("campaign_recipient_steps"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("step_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("delivery_state", sa.String(32), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timer_generation", sa.Integer(), nullable=True),
        sa.Column("first_opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("open_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_clicked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("click_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_replied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reply_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(updated=True),
        *_tenant_constraints("campaign_recipient_steps"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_recipient_steps_campaign",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipient_id"],
            [
                "mod_campaigns.campaign_recipients.tenant_id",
                "mod_campaigns.campaign_recipients.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_recipient_steps_recipient",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "step_id"],
            [
                "mod_campaigns.campaign_steps.tenant_id",
                "mod_campaigns.campaign_steps.id",
            ],
            ondelete="RESTRICT",
            name="fk_campaign_recipient_steps_step",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "recipient_id",
            "step_id",
            name="uq_campaign_recipient_steps_recipient_step",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_recipient_steps_tenant_id",
        "campaign_recipient_steps",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_recipient_steps_campaign_status",
        "campaign_recipient_steps",
        ["tenant_id", "campaign_id", "status"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_consent_receipts",
        *_identity("campaign_consent_receipts"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_step_id", sa.Uuid(), nullable=True),
        sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(120), nullable=True),
        sa.Column("policy_owner", sa.String(120), nullable=False),
        sa.Column("destination_hash", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        *_tenant_constraints("campaign_consent_receipts"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_consent_receipts_campaign",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipient_id"],
            [
                "mod_campaigns.campaign_recipients.tenant_id",
                "mod_campaigns.campaign_recipients.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_consent_receipts_recipient",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipient_step_id"],
            [
                "mod_campaigns.campaign_recipient_steps.tenant_id",
                "mod_campaigns.campaign_recipient_steps.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_consent_receipts_step",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "recipient_id",
            "recipient_step_id",
            "phase",
            "fingerprint",
            name="uq_campaign_consent_receipts_evaluation",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_consent_receipts_tenant_id",
        "campaign_consent_receipts",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_consent_receipts_recipient",
        "campaign_consent_receipts",
        ["tenant_id", "recipient_id", "evaluated_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_delivery_intents",
        *_identity("campaign_delivery_intents"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_step_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("address", sa.String(500), nullable=True),
        sa.Column("address_hash", sa.String(64), nullable=False),
        sa.Column("sender_key", sa.String(120), nullable=False),
        sa.Column("sender_address", sa.String(500), nullable=True),
        sa.Column("sender_display_name", sa.String(200), nullable=True),
        sa.Column("sender_fingerprint", sa.String(64), nullable=False),
        sa.Column("template_revision", sa.String(255), nullable=False),
        sa.Column("rendered_subject", sa.Text(), nullable=True),
        sa.Column("rendered_body", sa.Text(), nullable=True),
        sa.Column("rendered_fingerprint", sa.String(64), nullable=False),
        sa.Column("outbox_event_id", sa.Uuid(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pii_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scrubbed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        *_tenant_constraints("campaign_delivery_intents"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_delivery_intents_campaign",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipient_step_id"],
            [
                "mod_campaigns.campaign_recipient_steps.tenant_id",
                "mod_campaigns.campaign_recipient_steps.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_delivery_intents_step",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "recipient_step_id",
            name="uq_campaign_delivery_intents_recipient_step",
        ),
        sa.UniqueConstraint(
            "tenant_id", "dispatch_id", name="uq_campaign_delivery_intents_dispatch"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_delivery_intents_tenant_id",
        "campaign_delivery_intents",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_delivery_intents_campaign",
        "campaign_delivery_intents",
        ["tenant_id", "campaign_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_observations",
        *_identity("campaign_observations"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_step_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_intent_id", sa.Uuid(), nullable=False),
        sa.Column("dispatch_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("delivery_state", sa.String(32), nullable=True),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(255), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("correlation_ref", sa.String(255), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        *_tenant_constraints("campaign_observations"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_observations_campaign",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipient_step_id"],
            [
                "mod_campaigns.campaign_recipient_steps.tenant_id",
                "mod_campaigns.campaign_recipient_steps.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_observations_step",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "delivery_intent_id"],
            [
                "mod_campaigns.campaign_delivery_intents.tenant_id",
                "mod_campaigns.campaign_delivery_intents.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_observations_intent",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_campaign_observations_source_event",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_observations_tenant_id",
        "campaign_observations",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_observations_step",
        "campaign_observations",
        ["tenant_id", "recipient_step_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_unsubscribe_requests",
        *_identity("campaign_unsubscribe_requests"),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("recipient_id", sa.Uuid(), nullable=True),
        sa.Column("channel", sa.String(40), nullable=False),
        sa.Column("destination_hash", sa.String(64), nullable=False),
        sa.Column("source_owner", sa.String(120), nullable=False),
        sa.Column("source_event_id", sa.String(255), nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("reason", sa.String(120), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        *_tenant_constraints("campaign_unsubscribe_requests"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="RESTRICT",
            name="fk_campaign_unsubscribe_requests_campaign",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipient_id"],
            [
                "mod_campaigns.campaign_recipients.tenant_id",
                "mod_campaigns.campaign_recipients.id",
            ],
            ondelete="RESTRICT",
            name="fk_campaign_unsubscribe_requests_recipient",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_campaign_unsubscribe_requests_source_event",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_unsubscribe_requests_tenant_id",
        "campaign_unsubscribe_requests",
        ["tenant_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_unsubscribe_requests_destination",
        "campaign_unsubscribe_requests",
        ["tenant_id", "destination_hash"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_responses",
        *_identity("campaign_responses"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_step_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("correlation_ref", sa.String(255), nullable=True),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("emitted_outbox_event_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        *_timestamps(),
        *_tenant_constraints("campaign_responses"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_responses_campaign",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipient_id"],
            [
                "mod_campaigns.campaign_recipients.tenant_id",
                "mod_campaigns.campaign_recipients.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_responses_recipient",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "recipient_step_id"],
            [
                "mod_campaigns.campaign_recipient_steps.tenant_id",
                "mod_campaigns.campaign_recipient_steps.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_responses_step",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                "mod_campaigns.campaign_observations.tenant_id",
                "mod_campaigns.campaign_observations.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_responses_observation",
        ),
        sa.UniqueConstraint(
            "tenant_id", "observation_id", name="uq_campaign_responses_observation"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_responses_tenant_id",
        "campaign_responses",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "campaign_counters",
        *_identity("campaign_counters"),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("total_recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("suppressed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "intents_published", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("opened", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("replied", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rebuilt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        *_tenant_constraints("campaign_counters"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            ["mod_campaigns.campaigns.tenant_id", "mod_campaigns.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_counters_campaign",
        ),
        sa.UniqueConstraint(
            "tenant_id", "campaign_id", name="uq_campaign_counters_campaign"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_campaign_counters_tenant_id",
        "campaign_counters",
        ["tenant_id"],
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION mod_campaigns.protect_campaign_snapshot()
        RETURNS trigger AS $$
        DECLARE
            owning_campaign uuid;
            lifecycle text;
        BEGIN
            owning_campaign := CASE WHEN TG_OP = 'DELETE'
                                    THEN OLD.campaign_id ELSE NEW.campaign_id END;
            SELECT status INTO lifecycle
              FROM mod_campaigns.campaigns
             WHERE tenant_id = CASE WHEN TG_OP = 'DELETE'
                                    THEN OLD.tenant_id ELSE NEW.tenant_id END
               AND id = owning_campaign;
            IF lifecycle = 'draft' THEN
                RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END IF;
            IF TG_TABLE_NAME = 'campaign_recipients' AND TG_OP = 'UPDATE'
               AND (to_jsonb(NEW) - ARRAY['address','context','scrubbed_at']::text[])
                   = (to_jsonb(OLD) - ARRAY['address','context','scrubbed_at']::text[])
               AND NEW.address IS NULL
               AND NEW.context = '{}'::jsonb
               AND NEW.scrubbed_at IS NOT NULL THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'campaign snapshot is immutable once sending begins'
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        "CREATE TRIGGER campaign_revisions_snapshot_immutable "
        "BEFORE UPDATE OR DELETE ON mod_campaigns.campaign_revisions "
        "FOR EACH ROW EXECUTE FUNCTION mod_campaigns.protect_campaign_snapshot();"
    )
    op.execute(
        "CREATE TRIGGER campaign_steps_snapshot_immutable "
        "BEFORE UPDATE OR DELETE ON mod_campaigns.campaign_steps "
        "FOR EACH ROW EXECUTE FUNCTION mod_campaigns.protect_campaign_snapshot();"
    )
    op.execute(
        "CREATE TRIGGER campaign_audiences_snapshot_immutable "
        "BEFORE UPDATE OR DELETE ON mod_campaigns.campaign_audiences "
        "FOR EACH ROW EXECUTE FUNCTION mod_campaigns.protect_campaign_snapshot();"
    )
    op.execute(
        "CREATE TRIGGER campaign_recipients_snapshot_immutable "
        "BEFORE UPDATE OR DELETE ON mod_campaigns.campaign_recipients "
        "FOR EACH ROW EXECUTE FUNCTION mod_campaigns.protect_campaign_snapshot();"
    )

    # Explicit per table so both source review and the static migration gate can
    # see the whole RLS/grant contract without evaluating a generated string.
    op.execute("ALTER TABLE mod_campaigns.campaigns ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_campaigns.campaigns FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY campaigns_tenant_isolation ON mod_campaigns.campaigns USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaigns TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_revisions ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_campaigns.campaign_revisions FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY campaign_revisions_tenant_isolation ON mod_campaigns.campaign_revisions USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_revisions TO app_user;"
    )
    op.execute("ALTER TABLE mod_campaigns.campaign_steps ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_campaigns.campaign_steps FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY campaign_steps_tenant_isolation ON mod_campaigns.campaign_steps USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_steps TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_audiences ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_campaigns.campaign_audiences FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY campaign_audiences_tenant_isolation ON mod_campaigns.campaign_audiences USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_audiences TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_recipients ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_recipients FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY campaign_recipients_tenant_isolation ON mod_campaigns.campaign_recipients USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_recipients TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_recipient_steps ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_recipient_steps FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY campaign_recipient_steps_tenant_isolation ON mod_campaigns.campaign_recipient_steps USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_recipient_steps TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_consent_receipts ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_consent_receipts FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY campaign_consent_receipts_tenant_isolation ON mod_campaigns.campaign_consent_receipts USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_consent_receipts TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_delivery_intents ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_delivery_intents FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY campaign_delivery_intents_tenant_isolation ON mod_campaigns.campaign_delivery_intents USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_delivery_intents TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_observations ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_observations FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY campaign_observations_tenant_isolation ON mod_campaigns.campaign_observations USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_observations TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_unsubscribe_requests ENABLE ROW LEVEL SECURITY;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_unsubscribe_requests FORCE ROW LEVEL SECURITY;"
    )
    op.execute(
        "CREATE POLICY campaign_unsubscribe_requests_tenant_isolation ON mod_campaigns.campaign_unsubscribe_requests USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_unsubscribe_requests TO app_user;"
    )
    op.execute(
        "ALTER TABLE mod_campaigns.campaign_responses ENABLE ROW LEVEL SECURITY;"
    )
    op.execute("ALTER TABLE mod_campaigns.campaign_responses FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY campaign_responses_tenant_isolation ON mod_campaigns.campaign_responses USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_responses TO app_user;"
    )
    op.execute("ALTER TABLE mod_campaigns.campaign_counters ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_campaigns.campaign_counters FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY campaign_counters_tenant_isolation ON mod_campaigns.campaign_counters USING (tenant_id = public.app_current_tenant_id()) WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_campaigns.campaign_counters TO app_user;"
    )


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS mod_campaigns CASCADE;")
