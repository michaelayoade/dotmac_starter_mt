"""Tenant-only campaign persistence in the allocated ``mod_campaigns`` schema."""

from __future__ import annotations

import uuid
from datetime import datetime, time

from dotmac_kernel.models import Base
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("campaigns")


class Campaign(Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_campaigns_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_campaigns_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaigns_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "active_revision_id"],
            [
                f"{SCHEMA}.campaign_revisions.tenant_id",
                f"{SCHEMA}.campaign_revisions.id",
            ],
            name="fk_campaigns_active_revision",
            use_alter=True,
        ),
        Index("ix_campaigns_tenant_status", "tenant_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft")
    active_revision_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    scheduled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancellation_reason: Mapped[str | None] = mapped_column(String(255))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    pii_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignRevision(Base):
    __tablename__ = "campaign_revisions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_campaign_revisions_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "campaign_id",
            "revision_number",
            name="uq_campaign_revisions_tenant_campaign_number",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_revisions_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_revisions_campaign",
        ),
        Index("ix_campaign_revisions_tenant_campaign", "tenant_id", "campaign_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    send_window_start: Mapped[time] = mapped_column(Time(), nullable=False)
    send_window_end: Mapped[time] = mapped_column(Time(), nullable=False)
    sender_key: Mapped[str] = mapped_column(String(120), nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignStep(Base):
    __tablename__ = "campaign_steps"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_campaign_steps_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "revision_id",
            "position",
            name="uq_campaign_steps_tenant_revision_position",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_steps_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_steps_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "revision_id"],
            [
                f"{SCHEMA}.campaign_revisions.tenant_id",
                f"{SCHEMA}.campaign_revisions.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_steps_revision",
        ),
        Index("ix_campaign_steps_tenant_revision", "tenant_id", "revision_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    revision_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    delay_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    template_slug: Mapped[str] = mapped_column(String(120), nullable=False)
    template_channel: Mapped[str] = mapped_column(String(40), nullable=False)
    advance_on: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignAudience(Base):
    __tablename__ = "campaign_audiences"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_campaign_audiences_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "campaign_id",
            "source_owner",
            "source_version",
            "source_fingerprint",
            name="uq_campaign_audiences_source_snapshot",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_audiences_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_audiences_campaign",
        ),
        Index("ix_campaign_audiences_campaign", "tenant_id", "campaign_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_version: Mapped[str] = mapped_column(String(120), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    eligibility_reason: Mapped[str] = mapped_column(String(120), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignRecipient(Base):
    __tablename__ = "campaign_recipients"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_campaign_recipients_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "campaign_id",
            "source_owner",
            "source_subject_id",
            name="uq_campaign_recipients_source_subject",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_recipients_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_recipients_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "audience_id"],
            [
                f"{SCHEMA}.campaign_audiences.tenant_id",
                f"{SCHEMA}.campaign_audiences.id",
            ],
            ondelete="RESTRICT",
            name="fk_campaign_recipients_audience",
        ),
        Index("ix_campaign_recipients_campaign", "tenant_id", "campaign_id"),
        Index("ix_campaign_recipients_address", "tenant_id", "address_hash"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    audience_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_subject_id: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    address_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    context: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    eligibility_reason: Mapped[str] = mapped_column(String(120), nullable=False)
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pii_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    scrubbed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignConsentReceipt(Base):
    __tablename__ = "campaign_consent_receipts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_campaign_consent_receipts_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "recipient_id",
            "recipient_step_id",
            "phase",
            "fingerprint",
            name="uq_campaign_consent_receipts_evaluation",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_consent_receipts_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_consent_receipts_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_id"],
            [
                f"{SCHEMA}.campaign_recipients.tenant_id",
                f"{SCHEMA}.campaign_recipients.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_consent_receipts_recipient",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_step_id"],
            [
                f"{SCHEMA}.campaign_recipient_steps.tenant_id",
                f"{SCHEMA}.campaign_recipient_steps.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_consent_receipts_step",
            use_alter=True,
        ),
        Index(
            "ix_campaign_consent_receipts_recipient",
            "tenant_id",
            "recipient_id",
            "evaluated_at",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recipient_step_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    phase: Mapped[str] = mapped_column(String(24), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(120))
    policy_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    destination_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignRecipientStep(Base):
    __tablename__ = "campaign_recipient_steps"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_campaign_recipient_steps_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "recipient_id",
            "step_id",
            name="uq_campaign_recipient_steps_recipient_step",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_recipient_steps_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_recipient_steps_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_id"],
            [
                f"{SCHEMA}.campaign_recipients.tenant_id",
                f"{SCHEMA}.campaign_recipients.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_recipient_steps_recipient",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "step_id"],
            [f"{SCHEMA}.campaign_steps.tenant_id", f"{SCHEMA}.campaign_steps.id"],
            ondelete="RESTRICT",
            name="fk_campaign_recipient_steps_step",
        ),
        Index(
            "ix_campaign_recipient_steps_campaign_status",
            "tenant_id",
            "campaign_id",
            "status",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    step_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_state: Mapped[str] = mapped_column(String(32), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timer_generation: Mapped[int | None] = mapped_column(Integer)
    first_opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    open_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reply_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignDeliveryIntent(Base):
    __tablename__ = "campaign_delivery_intents"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_campaign_delivery_intents_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "recipient_step_id",
            name="uq_campaign_delivery_intents_recipient_step",
        ),
        UniqueConstraint(
            "tenant_id", "dispatch_id", name="uq_campaign_delivery_intents_dispatch"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_delivery_intents_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_delivery_intents_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_step_id"],
            [
                f"{SCHEMA}.campaign_recipient_steps.tenant_id",
                f"{SCHEMA}.campaign_recipient_steps.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_delivery_intents_step",
        ),
        Index("ix_campaign_delivery_intents_campaign", "tenant_id", "campaign_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recipient_step_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    dispatch_id: Mapped[uuid.UUID] = mapped_column(nullable=False, default=uuid.uuid4)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
    address_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sender_key: Mapped[str] = mapped_column(String(120), nullable=False)
    sender_address: Mapped[str | None] = mapped_column(String(500))
    sender_display_name: Mapped[str | None] = mapped_column(String(200))
    sender_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    template_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    rendered_subject: Mapped[str | None] = mapped_column(Text)
    rendered_body: Mapped[str | None] = mapped_column(Text)
    rendered_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    outbox_event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    pii_expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    scrubbed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignObservation(Base):
    __tablename__ = "campaign_observations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_campaign_observations_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_campaign_observations_source_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_observations_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_observations_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_step_id"],
            [
                f"{SCHEMA}.campaign_recipient_steps.tenant_id",
                f"{SCHEMA}.campaign_recipient_steps.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_observations_step",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "delivery_intent_id"],
            [
                f"{SCHEMA}.campaign_delivery_intents.tenant_id",
                f"{SCHEMA}.campaign_delivery_intents.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_observations_intent",
        ),
        Index("ix_campaign_observations_step", "tenant_id", "recipient_step_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recipient_step_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    delivery_intent_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    dispatch_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    delivery_state: Mapped[str | None] = mapped_column(String(32))
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_ref: Mapped[str | None] = mapped_column(String(255))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignUnsubscribeRequest(Base):
    __tablename__ = "campaign_unsubscribe_requests"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_campaign_unsubscribe_requests_tenant_id_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "source_owner",
            "source_event_id",
            name="uq_campaign_unsubscribe_requests_source_event",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_unsubscribe_requests_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="RESTRICT",
            name="fk_campaign_unsubscribe_requests_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_id"],
            [
                f"{SCHEMA}.campaign_recipients.tenant_id",
                f"{SCHEMA}.campaign_recipients.id",
            ],
            ondelete="RESTRICT",
            name="fk_campaign_unsubscribe_requests_recipient",
        ),
        Index(
            "ix_campaign_unsubscribe_requests_destination",
            "tenant_id",
            "destination_hash",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    recipient_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    channel: Mapped[str] = mapped_column(String(40), nullable=False)
    destination_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_owner: Mapped[str] = mapped_column(String(120), nullable=False)
    source_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(120), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignResponse(Base):
    __tablename__ = "campaign_responses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_campaign_responses_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "observation_id", name="uq_campaign_responses_observation"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_responses_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_responses_campaign",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_id"],
            [
                f"{SCHEMA}.campaign_recipients.tenant_id",
                f"{SCHEMA}.campaign_recipients.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_responses_recipient",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "recipient_step_id"],
            [
                f"{SCHEMA}.campaign_recipient_steps.tenant_id",
                f"{SCHEMA}.campaign_recipient_steps.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_responses_step",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "observation_id"],
            [
                f"{SCHEMA}.campaign_observations.tenant_id",
                f"{SCHEMA}.campaign_observations.id",
            ],
            ondelete="CASCADE",
            name="fk_campaign_responses_observation",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recipient_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    recipient_step_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    observation_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    correlation_ref: Mapped[str | None] = mapped_column(String(255))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    emitted_outbox_event_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CampaignCounter(Base):
    __tablename__ = "campaign_counters"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_campaign_counters_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "campaign_id", name="uq_campaign_counters_campaign"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_campaign_counters_tenant",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "campaign_id"],
            [f"{SCHEMA}.campaigns.tenant_id", f"{SCHEMA}.campaigns.id"],
            ondelete="CASCADE",
            name="fk_campaign_counters_campaign",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    campaign_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    total_recipients: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pending: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    suppressed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    intents_published: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    clicked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replied: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rebuilt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


ALL_MODELS = (
    Campaign,
    CampaignRevision,
    CampaignStep,
    CampaignAudience,
    CampaignRecipient,
    CampaignConsentReceipt,
    CampaignRecipientStep,
    CampaignDeliveryIntent,
    CampaignObservation,
    CampaignUnsubscribeRequest,
    CampaignResponse,
    CampaignCounter,
)
TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "SCHEMA",
    "TABLES",
    "Campaign",
    "CampaignAudience",
    "CampaignConsentReceipt",
    "CampaignCounter",
    "CampaignDeliveryIntent",
    "CampaignObservation",
    "CampaignRecipient",
    "CampaignRecipientStep",
    "CampaignResponse",
    "CampaignRevision",
    "CampaignStep",
    "CampaignUnsubscribeRequest",
]
