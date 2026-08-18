"""One provider-neutral campaign lifecycle in the caller's transaction.

Every query carries tenant scope even though Postgres RLS enforces it again.
Services mutate and flush; no function creates a session, commits or rolls back.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from typing import Final
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotmac_kernel.consent import normalize_address, suppress, suppression_reason
from dotmac_kernel.idempotency import execute_once, fingerprint_of
from dotmac_kernel.messaging.models import OutboxEvent
from dotmac_kernel.messaging.outbox import enqueue_event
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from dotmac_campaigns.contracts import (
    AudienceBatch,
    AudienceIngestionResult,
    CampaignSnapshot,
    CampaignStatus,
    ConsentReceiptView,
    CounterView,
    CreateCampaign,
    DeliveryGateResult,
    DeliveryIntentView,
    DeliveryState,
    DriftReport,
    DueWorkResult,
    DueWorkTrigger,
    Observation,
    ObservationKind,
    ObservationResult,
    RecipientStepStatus,
    RecipientStepView,
    RecipientView,
    Renderer,
    RenderRequest,
    ResponseFact,
    ReviseCampaign,
    SenderRequest,
    SenderResolver,
    TimerIdentity,
    TimerOutput,
    TimerPort,
    UnsubscribeRequest,
    UnsubscribeResult,
    fingerprint,
)
from dotmac_campaigns.models import (
    Campaign,
    CampaignAudience,
    CampaignConsentReceipt,
    CampaignCounter,
    CampaignDeliveryIntent,
    CampaignObservation,
    CampaignRecipient,
    CampaignRecipientStep,
    CampaignResponse,
    CampaignRevision,
    CampaignStep,
    CampaignUnsubscribeRequest,
)

CAMPAIGN_CATEGORY: Final[str] = "campaign"
DUE_EVENT_TYPE: Final[str] = "campaigns.recipient_step_due.v1"
DELIVERY_EVENT_TYPE: Final[str] = "campaigns.delivery_intent.v1"
RESPONSE_EVENT_TYPE: Final[str] = "campaigns.response.v1"


class CampaignError(ValueError):
    """Base for fail-closed campaign contract errors."""


class CampaignNotFound(CampaignError):
    pass


class InvalidTransition(CampaignError):
    pass


class SnapshotImmutable(CampaignError):
    pass


class SequenceBlocked(CampaignError):
    pass


class DeliveryIntentNotFound(CampaignError):
    pass


class RepairNotPossible(CampaignError):
    pass


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CampaignError(f"{name} must be timezone-aware")


def _as_utc(value: datetime) -> datetime:
    """Restore SQLite's dropped offset at persistence boundaries."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _campaign(
    db: Session, tenant_id: UUID, campaign_id: UUID, *, lock: bool = False
) -> Campaign:
    statement = select(Campaign).where(
        Campaign.tenant_id == tenant_id, Campaign.id == campaign_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise CampaignNotFound(f"campaign {campaign_id} was not found")
    return row


def _revision(db: Session, tenant_id: UUID, campaign: Campaign) -> CampaignRevision:
    revision: CampaignRevision | None = None
    if campaign.active_revision_id is not None:
        revision = db.scalar(
            select(CampaignRevision).where(
                CampaignRevision.tenant_id == tenant_id,
                CampaignRevision.id == campaign.active_revision_id,
            )
        )
    if revision is None:
        revision = db.scalar(
            select(CampaignRevision)
            .where(
                CampaignRevision.tenant_id == tenant_id,
                CampaignRevision.campaign_id == campaign.id,
            )
            .order_by(CampaignRevision.revision_number.desc())
            .limit(1)
        )
    if revision is None:
        raise CampaignError("campaign has no revision")
    return revision


def _steps(db: Session, tenant_id: UUID, revision_id: UUID) -> list[CampaignStep]:
    return list(
        db.scalars(
            select(CampaignStep)
            .where(
                CampaignStep.tenant_id == tenant_id,
                CampaignStep.revision_id == revision_id,
            )
            .order_by(CampaignStep.position)
        )
    )


def _step_identity(row: CampaignRecipientStep) -> TimerIdentity:
    return TimerIdentity(
        owner="campaigns",
        entity_kind="recipient_step",
        entity_id=str(row.id),
        purpose=f"delivery_due:{row.position}",
    )


def _address_hash(channel: str, address: str) -> str:
    normalized = normalize_address(channel, address)
    return hashlib.sha256(f"{channel}:{normalized}".encode()).hexdigest()


def _add_revision(
    db: Session,
    *,
    tenant_id: UUID,
    campaign: Campaign,
    command: CreateCampaign,
    revision_number: int,
    recorded_at: datetime,
) -> CampaignRevision:
    revision = CampaignRevision(
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        revision_number=revision_number,
        kind=command.kind.value,
        channel=command.channel,
        timezone=command.timezone,
        send_window_start=command.send_window_start,
        send_window_end=command.send_window_end,
        created_at=recorded_at,
        sender_key=command.sender_key,
    )
    db.add(revision)
    db.flush()
    for item in command.steps:
        db.add(
            CampaignStep(
                tenant_id=tenant_id,
                campaign_id=campaign.id,
                revision_id=revision.id,
                position=item.position,
                delay_seconds=int(item.delay.total_seconds()),
                template_slug=item.template_slug,
                template_channel=item.template_channel,
                advance_on=sorted(state.value for state in item.advance_on),
                created_at=recorded_at,
            )
        )
    db.flush()
    return revision


def create_campaign(
    db: Session,
    *,
    tenant_id: UUID,
    command: CreateCampaign,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    recorded_at: datetime,
) -> Campaign:
    _aware("recorded_at", recorded_at)
    _aware("idempotency_expires_at", idempotency_expires_at)

    def operation(session: Session) -> Mapping[str, object]:
        campaign = Campaign(
            tenant_id=tenant_id,
            code=command.code,
            name=command.name,
            status=CampaignStatus.DRAFT.value,
            scheduled_at=command.scheduled_at,
            evidence_expires_at=command.evidence_expires_at,
            pii_expires_at=command.pii_expires_at,
            created_at=recorded_at,
            updated_at=recorded_at,
        )
        session.add(campaign)
        session.flush()
        _add_revision(
            session,
            tenant_id=tenant_id,
            campaign=campaign,
            command=command,
            revision_number=1,
            recorded_at=recorded_at,
        )
        session.add(
            CampaignCounter(
                tenant_id=tenant_id,
                campaign_id=campaign.id,
                rebuilt_at=recorded_at,
                updated_at=recorded_at,
            )
        )
        session.flush()
        return {"campaign_id": str(campaign.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="campaigns.create",
        key=idempotency_key,
        operation=operation,
        operation_name="campaigns.create_campaign",
        fingerprint=fingerprint_of(command.fingerprint_payload()),
        correlation_id=command.code,
        expires_at=idempotency_expires_at,
    )
    return _campaign(db, tenant_id, UUID(str(outcome.result["campaign_id"])))


def revise_campaign(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    command: ReviseCampaign,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    recorded_at: datetime,
) -> CampaignRevision:
    campaign = _campaign(db, tenant_id, campaign_id, lock=True)
    if campaign.status != CampaignStatus.DRAFT.value:
        raise SnapshotImmutable("a campaign can be revised only while draft")
    create = command.as_create(campaign.code)

    def operation(session: Session) -> Mapping[str, object]:
        latest = session.scalar(
            select(func.max(CampaignRevision.revision_number)).where(
                CampaignRevision.tenant_id == tenant_id,
                CampaignRevision.campaign_id == campaign_id,
            )
        )
        revision = _add_revision(
            session,
            tenant_id=tenant_id,
            campaign=campaign,
            command=create,
            revision_number=int(latest or 0) + 1,
            recorded_at=recorded_at,
        )
        campaign.name = command.name
        campaign.scheduled_at = command.scheduled_at
        campaign.evidence_expires_at = command.evidence_expires_at
        campaign.pii_expires_at = command.pii_expires_at
        campaign.updated_at = recorded_at
        session.flush()
        return {"revision_id": str(revision.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="campaigns.revise",
        key=idempotency_key,
        operation=operation,
        fingerprint=fingerprint_of(create.fingerprint_payload()),
        expires_at=idempotency_expires_at,
    )
    revision = db.get(CampaignRevision, UUID(str(outcome.result["revision_id"])))
    if revision is None or revision.tenant_id != tenant_id:
        raise CampaignError("idempotent revision result is missing")
    return revision


def _consent_receipt(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    recipient: CampaignRecipient,
    recipient_step_id: UUID | None,
    phase: str,
    evaluated_at: datetime,
    forced_reason: str | None = None,
) -> CampaignConsentReceipt:
    reason = forced_reason
    if reason is None and recipient.address is not None:
        reason = suppression_reason(
            db,
            tenant_id,
            channel=recipient.channel,
            address=recipient.address,
            category=CAMPAIGN_CATEGORY,
        )
    allowed = reason is None
    receipt_fingerprint = fingerprint(
        {
            "policy_owner": "dotmac_kernel.consent",
            "recipient_id": recipient.id,
            "recipient_step_id": recipient_step_id,
            "phase": phase,
            "allowed": allowed,
            "reason": reason,
            "destination_hash": recipient.address_hash,
            "evaluated_at": evaluated_at,
        }
    )
    receipt = CampaignConsentReceipt(
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        recipient_id=recipient.id,
        recipient_step_id=recipient_step_id,
        phase=phase,
        allowed=allowed,
        reason=reason,
        policy_owner="dotmac_kernel.consent",
        destination_hash=recipient.address_hash,
        fingerprint=receipt_fingerprint,
        evaluated_at=evaluated_at,
        created_at=evaluated_at,
    )
    db.add(receipt)
    db.flush()
    return receipt


def ingest_audience(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    batch: AudienceBatch,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    evaluated_at: datetime,
) -> AudienceIngestionResult:
    _aware("evaluated_at", evaluated_at)
    campaign = _campaign(db, tenant_id, campaign_id, lock=True)
    if campaign.status != CampaignStatus.DRAFT.value:
        raise SnapshotImmutable(
            "an audience can change only while the campaign is draft"
        )
    revision = _revision(db, tenant_id, campaign)
    for candidate in batch.candidates:
        if candidate.channel != revision.channel:
            raise CampaignError(
                f"candidate channel {candidate.channel!r} does not match "
                "campaign channel"
            )
    request_fingerprint = fingerprint(batch.fingerprint_payload())

    def operation(session: Session) -> Mapping[str, object]:
        audience = CampaignAudience(
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            source_owner=batch.source_owner,
            source_version=batch.source_version,
            source_fingerprint=batch.source_fingerprint,
            eligibility_reason=batch.eligibility_reason,
            request_fingerprint=request_fingerprint,
            created_at=evaluated_at,
        )
        session.add(audience)
        session.flush()
        suppressed_count = 0
        for candidate in batch.candidates:
            address_hash = _address_hash(candidate.channel, candidate.address)
            snapshot_fingerprint = fingerprint(
                {
                    "source_owner": batch.source_owner,
                    "source_version": batch.source_version,
                    "source_subject_id": candidate.source_subject_id,
                    "channel": candidate.channel,
                    "address_hash": address_hash,
                    "context": dict(candidate.context),
                    "eligibility_reason": candidate.eligibility_reason,
                }
            )
            recipient = CampaignRecipient(
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                audience_id=audience.id,
                source_owner=batch.source_owner,
                source_subject_id=candidate.source_subject_id,
                channel=candidate.channel,
                address=candidate.address,
                address_hash=address_hash,
                context=dict(candidate.context),
                eligibility_reason=candidate.eligibility_reason,
                snapshot_fingerprint=snapshot_fingerprint,
                pii_expires_at=campaign.pii_expires_at,
                created_at=evaluated_at,
            )
            session.add(recipient)
            session.flush()
            receipt = _consent_receipt(
                session,
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                recipient=recipient,
                recipient_step_id=None,
                phase="audience",
                evaluated_at=evaluated_at,
            )
            if not receipt.allowed:
                suppressed_count += 1
        session.flush()
        return {
            "audience_id": str(audience.id),
            "created": len(batch.candidates),
            "eligible": len(batch.candidates) - suppressed_count,
            "suppressed": suppressed_count,
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="campaigns.ingest_audience",
        key=idempotency_key,
        operation=operation,
        fingerprint=request_fingerprint,
        correlation_id=str(campaign_id),
        expires_at=idempotency_expires_at,
    )
    rebuild_counters(
        db, tenant_id=tenant_id, campaign_id=campaign_id, rebuilt_at=evaluated_at
    )
    return AudienceIngestionResult(
        audience_id=UUID(str(outcome.result["audience_id"])),
        created=int(str(outcome.result["created"])),
        eligible=int(str(outcome.result["eligible"])),
        suppressed=int(str(outcome.result["suppressed"])),
        replayed=outcome.replayed,
    )


def _latest_audience_receipt(
    db: Session, tenant_id: UUID, recipient_id: UUID
) -> CampaignConsentReceipt | None:
    return db.scalar(
        select(CampaignConsentReceipt)
        .where(
            CampaignConsentReceipt.tenant_id == tenant_id,
            CampaignConsentReceipt.recipient_id == recipient_id,
            CampaignConsentReceipt.phase == "audience",
        )
        .order_by(CampaignConsentReceipt.evaluated_at.desc())
        .limit(1)
    )


def schedule_campaign(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    timers: TimerPort,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    recorded_at: datetime,
) -> Campaign:
    _aware("recorded_at", recorded_at)

    def operation(session: Session) -> Mapping[str, object]:
        campaign = _campaign(session, tenant_id, campaign_id, lock=True)
        if campaign.status != CampaignStatus.DRAFT.value:
            raise InvalidTransition(
                f"cannot schedule a campaign in {campaign.status!r} state"
            )
        revision = _revision(session, tenant_id, campaign)
        recipients = list(
            session.scalars(
                select(CampaignRecipient).where(
                    CampaignRecipient.tenant_id == tenant_id,
                    CampaignRecipient.campaign_id == campaign_id,
                )
            )
        )
        if not recipients:
            raise InvalidTransition("a campaign needs an audience before scheduling")
        first_step = _steps(session, tenant_id, revision.id)[0]
        revision.frozen_at = recorded_at
        for row in session.scalars(
            select(CampaignStep).where(
                CampaignStep.tenant_id == tenant_id,
                CampaignStep.revision_id == revision.id,
            )
        ):
            row.frozen_at = recorded_at
        for audience in session.scalars(
            select(CampaignAudience).where(
                CampaignAudience.tenant_id == tenant_id,
                CampaignAudience.campaign_id == campaign_id,
            )
        ):
            audience.frozen_at = recorded_at
        for recipient in recipients:
            recipient.frozen_at = recorded_at
        session.flush()

        campaign.active_revision_id = revision.id
        campaign.status = CampaignStatus.SCHEDULED.value
        campaign.updated_at = recorded_at
        created_steps = 0
        for recipient in recipients:
            audience_receipt = _latest_audience_receipt(
                session, tenant_id, recipient.id
            )
            if audience_receipt is None or not audience_receipt.allowed:
                continue
            due_at = _as_utc(campaign.scheduled_at)
            recipient_step = CampaignRecipientStep(
                tenant_id=tenant_id,
                campaign_id=campaign_id,
                recipient_id=recipient.id,
                step_id=first_step.id,
                position=0,
                status=RecipientStepStatus.SCHEDULED.value,
                delivery_state=DeliveryState.PENDING.value,
                due_at=due_at,
                created_at=recorded_at,
                updated_at=recorded_at,
            )
            session.add(recipient_step)
            session.flush()
            scheduled = timers.schedule(
                session,
                tenant_id=tenant_id,
                identity=_step_identity(recipient_step),
                due_at=due_at,
                output=TimerOutput(DUE_EVENT_TYPE),
                recorded_at=recorded_at,
                expires_at=_as_utc(campaign.evidence_expires_at),
            )
            recipient_step.timer_generation = scheduled.generation
            created_steps += 1
        session.flush()
        return {"campaign_id": str(campaign.id), "steps": created_steps}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="campaigns.schedule",
        key=idempotency_key,
        operation=operation,
        fingerprint=fingerprint_of({"campaign_id": campaign_id}),
        correlation_id=str(campaign_id),
        expires_at=idempotency_expires_at,
    )
    campaign = _campaign(db, tenant_id, UUID(str(outcome.result["campaign_id"])))
    rebuild_counters(
        db, tenant_id=tenant_id, campaign_id=campaign_id, rebuilt_at=recorded_at
    )
    return campaign


def next_send_at(
    candidate: datetime,
    *,
    timezone: str,
    window_start: time,
    window_end: time,
) -> datetime:
    """Return candidate when inside the window, otherwise its next opening.

    Equal bounds preserve Sub parity: they mean the whole day. A start later
    than the end is a window crossing midnight.
    """
    _aware("candidate", candidate)
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise CampaignError(f"unknown campaign timezone {timezone!r}") from exc
    local = candidate.astimezone(zone)
    current = local.timetz().replace(tzinfo=None)
    if window_start == window_end:
        return candidate
    if window_start < window_end:
        if window_start <= current < window_end:
            return candidate
        opening_date = (
            local.date() if current < window_start else local.date() + timedelta(days=1)
        )
    else:
        if current >= window_start or current < window_end:
            return candidate
        opening_date = local.date()
    opening = datetime.combine(opening_date, window_start, tzinfo=zone)
    return opening.astimezone(UTC)


def _recipient_step_for_trigger(
    db: Session, tenant_id: UUID, trigger: DueWorkTrigger
) -> CampaignRecipientStep:
    if (
        trigger.identity.owner != "campaigns"
        or trigger.identity.entity_kind != "recipient_step"
    ):
        raise SequenceBlocked("due-work identity is not owned by campaigns")
    try:
        recipient_step_id = UUID(trigger.identity.entity_id)
    except ValueError as exc:
        raise SequenceBlocked("due-work recipient-step identity is invalid") from exc
    row = db.scalar(
        select(CampaignRecipientStep)
        .where(
            CampaignRecipientStep.tenant_id == tenant_id,
            CampaignRecipientStep.id == recipient_step_id,
        )
        .with_for_update()
    )
    if row is None:
        raise SequenceBlocked("due-work recipient step does not exist")
    if trigger.identity.purpose != f"delivery_due:{row.position}":
        raise SequenceBlocked("due-work purpose does not match the recipient step")
    return row


def _predecessor(
    db: Session, tenant_id: UUID, row: CampaignRecipientStep
) -> CampaignRecipientStep | None:
    if row.position == 0:
        return None
    return db.scalar(
        select(CampaignRecipientStep).where(
            CampaignRecipientStep.tenant_id == tenant_id,
            CampaignRecipientStep.recipient_id == row.recipient_id,
            CampaignRecipientStep.position == row.position - 1,
        )
    )


def _delivery_payload(
    intent: CampaignDeliveryIntent, *, consent_receipt_id: UUID | None = None
) -> dict[str, object]:
    return {
        "contract": "campaigns.delivery_intent.v1",
        "campaign_id": str(intent.campaign_id),
        "recipient_step_id": str(intent.recipient_step_id),
        "delivery_intent_id": str(intent.id),
        "dispatch_id": str(intent.dispatch_id),
        "channel": intent.channel,
        "address": intent.address,
        "address_hash": intent.address_hash,
        "sender": {
            "key": intent.sender_key,
            "address": intent.sender_address,
            "display_name": intent.sender_display_name,
            "fingerprint": intent.sender_fingerprint,
        },
        "template_revision": intent.template_revision,
        "subject": intent.rendered_subject,
        "body": intent.rendered_body,
        "rendered_fingerprint": intent.rendered_fingerprint,
        "category": CAMPAIGN_CATEGORY,
        "delivery_gate_required": True,
        "consent_receipt_id": str(consent_receipt_id) if consent_receipt_id else None,
    }


def accept_due_work(
    db: Session,
    *,
    tenant_id: UUID,
    trigger: DueWorkTrigger,
    timers: TimerPort,
    renderer: Renderer,
    senders: SenderResolver,
    idempotency_key: str,
    idempotency_expires_at: datetime,
    accepted_at: datetime,
) -> DueWorkResult:
    _aware("accepted_at", accepted_at)
    row = _recipient_step_for_trigger(db, tenant_id, trigger)
    campaign = _campaign(db, tenant_id, row.campaign_id, lock=True)
    if campaign.status == CampaignStatus.CANCELLED.value:
        return DueWorkResult(row.id, "cancelled")
    if campaign.status == CampaignStatus.PAUSED.value:
        return DueWorkResult(row.id, "paused")
    if campaign.status not in {
        CampaignStatus.SCHEDULED.value,
        CampaignStatus.SENDING.value,
    }:
        raise InvalidTransition(
            f"campaign cannot accept due work while {campaign.status}"
        )
    if row.position > 0:
        predecessor = _predecessor(db, tenant_id, row)
        if predecessor is None or predecessor.resolved_at is None:
            raise SequenceBlocked("an unresolved predecessor blocks this step")
        step = db.scalar(
            select(CampaignStep).where(
                CampaignStep.tenant_id == tenant_id, CampaignStep.id == row.step_id
            )
        )
        if step is None or predecessor.delivery_state not in step.advance_on:
            raise SequenceBlocked("predecessor outcome does not permit this step")
    acceptance = timers.accept(
        db,
        tenant_id=tenant_id,
        trigger=trigger,
        accepted_at=accepted_at,
    )
    if not acceptance.current:
        return DueWorkResult(row.id, acceptance.reason, replayed=acceptance.replayed)

    revision = _revision(db, tenant_id, campaign)
    due = next_send_at(
        accepted_at,
        timezone=revision.timezone,
        window_start=revision.send_window_start,
        window_end=revision.send_window_end,
    )
    if due > accepted_at:
        scheduled = timers.schedule(
            db,
            tenant_id=tenant_id,
            identity=_step_identity(row),
            due_at=due,
            output=TimerOutput(DUE_EVENT_TYPE),
            recorded_at=accepted_at,
            expires_at=_as_utc(campaign.evidence_expires_at),
        )
        row.status = RecipientStepStatus.DEFERRED.value
        row.due_at = due
        row.timer_generation = scheduled.generation
        row.updated_at = accepted_at
        db.flush()
        return DueWorkResult(row.id, "deferred", next_due_at=due)

    recipient = db.scalar(
        select(CampaignRecipient).where(
            CampaignRecipient.tenant_id == tenant_id,
            CampaignRecipient.id == row.recipient_id,
        )
    )
    step = db.scalar(
        select(CampaignStep).where(
            CampaignStep.tenant_id == tenant_id, CampaignStep.id == row.step_id
        )
    )
    if recipient is None or step is None:
        raise CampaignError("recipient-step snapshot is incomplete")
    if recipient.address is None:
        raise RepairNotPossible("recipient PII was scrubbed before delivery")
    consent = _consent_receipt(
        db,
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        recipient=recipient,
        recipient_step_id=row.id,
        phase="delayed_step",
        evaluated_at=accepted_at,
    )
    if not consent.allowed:
        row.status = RecipientStepStatus.SUPPRESSED.value
        row.delivery_state = DeliveryState.SUPPRESSED.value
        row.resolved_at = accepted_at
        row.updated_at = accepted_at
        db.flush()
        rebuild_counters(
            db, tenant_id=tenant_id, campaign_id=campaign.id, rebuilt_at=accepted_at
        )
        _halt_remaining_steps(db, tenant_id=tenant_id, row=row, at=accepted_at)
        return DueWorkResult(row.id, "suppressed")

    def operation(session: Session) -> Mapping[str, object]:
        existing = session.scalar(
            select(CampaignDeliveryIntent).where(
                CampaignDeliveryIntent.tenant_id == tenant_id,
                CampaignDeliveryIntent.recipient_step_id == row.id,
            )
        )
        if existing is not None:
            return {
                "recipient_step_id": str(row.id),
                "delivery_intent_id": str(existing.id),
                "dispatch_id": str(existing.dispatch_id),
                "status": "intent_published",
            }
        rendered = renderer.render(
            RenderRequest(
                tenant_id=tenant_id,
                template_slug=step.template_slug,
                channel=step.template_channel,
                context=dict(recipient.context),
            )
        )
        sender = senders.resolve(
            SenderRequest(
                tenant_id=tenant_id,
                channel=revision.channel,
                sender_key=revision.sender_key,
            )
        )
        dispatch_id = uuid.uuid4()
        request_fingerprint = fingerprint(
            {
                "campaign_id": campaign.id,
                "recipient_step_id": row.id,
                "recipient_snapshot": recipient.snapshot_fingerprint,
                "sender": sender.fingerprint_sha256,
                "rendered": rendered.fingerprint_sha256,
            }
        )
        # Allocate the domain fact first so the outbox payload carries its id;
        # both rows still flush in this one caller transaction.
        placeholder_outbox_id = uuid.uuid4()
        intent = CampaignDeliveryIntent(
            tenant_id=tenant_id,
            campaign_id=campaign.id,
            recipient_step_id=row.id,
            dispatch_id=dispatch_id,
            request_fingerprint=request_fingerprint,
            channel=recipient.channel,
            address=recipient.address,
            address_hash=recipient.address_hash,
            sender_key=sender.sender_key,
            sender_address=sender.address,
            sender_display_name=sender.display_name,
            sender_fingerprint=sender.fingerprint_sha256,
            template_revision=rendered.template_revision,
            rendered_subject=rendered.subject,
            rendered_body=rendered.body,
            rendered_fingerprint=rendered.fingerprint_sha256,
            outbox_event_id=placeholder_outbox_id,
            published_at=accepted_at,
            pii_expires_at=campaign.pii_expires_at,
            created_at=accepted_at,
        )
        session.add(intent)
        session.flush()
        outbox = enqueue_event(
            session,
            tenant_id=tenant_id,
            event_type=DELIVERY_EVENT_TYPE,
            payload=_delivery_payload(intent, consent_receipt_id=consent.id),
            correlation_id=str(dispatch_id),
        )
        intent.outbox_event_id = outbox.id
        row.status = RecipientStepStatus.INTENT_PUBLISHED.value
        row.delivery_state = DeliveryState.INTENT_PUBLISHED.value
        row.updated_at = accepted_at
        campaign.status = CampaignStatus.SENDING.value
        campaign.started_at = campaign.started_at or accepted_at
        campaign.updated_at = accepted_at
        session.flush()
        return {
            "recipient_step_id": str(row.id),
            "delivery_intent_id": str(intent.id),
            "dispatch_id": str(intent.dispatch_id),
            "status": "intent_published",
        }

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="campaigns.accept_due_work",
        key=idempotency_key,
        operation=operation,
        fingerprint=fingerprint_of(
            {
                "timer_id": trigger.timer_id,
                "generation": trigger.generation,
                "recipient_step_id": row.id,
            }
        ),
        correlation_id=str(row.id),
        expires_at=idempotency_expires_at,
    )
    rebuild_counters(
        db, tenant_id=tenant_id, campaign_id=campaign.id, rebuilt_at=accepted_at
    )
    return DueWorkResult(
        recipient_step_id=UUID(str(outcome.result["recipient_step_id"])),
        status=str(outcome.result["status"]),
        delivery_intent_id=UUID(str(outcome.result["delivery_intent_id"])),
        dispatch_id=UUID(str(outcome.result["dispatch_id"])),
        replayed=outcome.replayed,
    )


def authorize_delivery(
    db: Session,
    *,
    tenant_id: UUID,
    dispatch_id: UUID,
    evaluated_at: datetime,
) -> DeliveryGateResult:
    """Final gate called immediately before provider transport.

    Publication is not permission to deliver. A suppression or cancellation
    recorded after queueing therefore still wins.
    """
    intent = db.scalar(
        select(CampaignDeliveryIntent)
        .where(
            CampaignDeliveryIntent.tenant_id == tenant_id,
            CampaignDeliveryIntent.dispatch_id == dispatch_id,
        )
        .with_for_update()
    )
    if intent is None:
        raise DeliveryIntentNotFound(f"dispatch {dispatch_id} was not found")
    step = db.scalar(
        select(CampaignRecipientStep).where(
            CampaignRecipientStep.tenant_id == tenant_id,
            CampaignRecipientStep.id == intent.recipient_step_id,
        )
    )
    if step is None:
        raise DeliveryIntentNotFound("delivery intent lost its recipient evidence")
    recipient = db.scalar(
        select(CampaignRecipient).where(
            CampaignRecipient.tenant_id == tenant_id,
            CampaignRecipient.id == step.recipient_id,
        )
    )
    campaign = _campaign(db, tenant_id, intent.campaign_id, lock=True)
    if recipient is None:
        raise DeliveryIntentNotFound("delivery intent lost its recipient evidence")
    forced_reason = (
        "campaign_cancelled"
        if campaign.status == CampaignStatus.CANCELLED.value
        else None
    )
    receipt = _consent_receipt(
        db,
        tenant_id=tenant_id,
        campaign_id=campaign.id,
        recipient=recipient,
        recipient_step_id=step.id,
        phase="delivery",
        evaluated_at=evaluated_at,
        forced_reason=forced_reason,
    )
    if not receipt.allowed:
        step.status = RecipientStepStatus.SUPPRESSED.value
        step.delivery_state = DeliveryState.SUPPRESSED.value
        step.resolved_at = evaluated_at
        step.updated_at = evaluated_at
        rebuild_counters(
            db, tenant_id=tenant_id, campaign_id=campaign.id, rebuilt_at=evaluated_at
        )
    db.flush()
    return DeliveryGateResult(
        dispatch_id=dispatch_id,
        allowed=receipt.allowed,
        reason=receipt.reason,
        consent_receipt_id=receipt.id,
    )


def delivery_intent(
    db: Session, *, tenant_id: UUID, dispatch_id: UUID
) -> DeliveryIntentView:
    """Read the provider-neutral publication fact without exposing stored PII."""

    row = db.scalar(
        select(CampaignDeliveryIntent).where(
            CampaignDeliveryIntent.tenant_id == tenant_id,
            CampaignDeliveryIntent.dispatch_id == dispatch_id,
        )
    )
    if row is None:
        raise DeliveryIntentNotFound(f"dispatch {dispatch_id} was not found")
    return DeliveryIntentView(
        id=row.id,
        campaign_id=row.campaign_id,
        recipient_step_id=row.recipient_step_id,
        dispatch_id=row.dispatch_id,
        channel=row.channel,
        address_hash=row.address_hash,
        sender_key=row.sender_key,
        template_revision=row.template_revision,
        rendered_fingerprint=row.rendered_fingerprint,
        outbox_event_id=row.outbox_event_id,
        published_at=_as_utc(row.published_at),
        scrubbed_at=_as_utc(row.scrubbed_at) if row.scrubbed_at is not None else None,
    )


_DELIVERY_PRECEDENCE: Final[dict[str, int]] = {
    DeliveryState.PENDING.value: 0,
    DeliveryState.INTENT_PUBLISHED.value: 10,
    DeliveryState.ACCEPTED.value: 20,
    DeliveryState.FAILED.value: 30,
    DeliveryState.REJECTED.value: 30,
    DeliveryState.DELIVERED.value: 40,
    DeliveryState.BOUNCED.value: 50,
    DeliveryState.SUPPRESSED.value: 50,
    DeliveryState.CANCELLED.value: 50,
}
_RESOLVED_DELIVERY: Final[frozenset[str]] = frozenset(
    {
        DeliveryState.FAILED.value,
        DeliveryState.REJECTED.value,
        DeliveryState.DELIVERED.value,
        DeliveryState.BOUNCED.value,
        DeliveryState.SUPPRESSED.value,
        DeliveryState.CANCELLED.value,
    }
)


def _materialize_successor(
    db: Session,
    *,
    tenant_id: UUID,
    current: CampaignRecipientStep,
    timers: TimerPort,
    occurred_at: datetime,
) -> None:
    revision_id = db.scalar(
        select(CampaignStep.revision_id).where(
            CampaignStep.tenant_id == tenant_id, CampaignStep.id == current.step_id
        )
    )
    if revision_id is None:
        return
    next_step = db.scalar(
        select(CampaignStep).where(
            CampaignStep.tenant_id == tenant_id,
            CampaignStep.revision_id == revision_id,
            CampaignStep.position == current.position + 1,
        )
    )
    if next_step is None:
        return
    existing = db.scalar(
        select(CampaignRecipientStep).where(
            CampaignRecipientStep.tenant_id == tenant_id,
            CampaignRecipientStep.recipient_id == current.recipient_id,
            CampaignRecipientStep.step_id == next_step.id,
        )
    )
    if existing is not None:
        return
    if current.resolved_at is None:
        raise SequenceBlocked(
            "an unresolved predecessor cannot materialize a successor"
        )
    allowed = current.delivery_state in next_step.advance_on
    due_at = occurred_at + timedelta(seconds=next_step.delay_seconds)
    successor = CampaignRecipientStep(
        tenant_id=tenant_id,
        campaign_id=current.campaign_id,
        recipient_id=current.recipient_id,
        step_id=next_step.id,
        position=next_step.position,
        status=(
            RecipientStepStatus.SCHEDULED.value
            if allowed
            else RecipientStepStatus.SKIPPED_PREDECESSOR.value
        ),
        delivery_state=(
            DeliveryState.PENDING.value if allowed else DeliveryState.CANCELLED.value
        ),
        due_at=due_at,
        resolved_at=None if allowed else occurred_at,
        created_at=occurred_at,
        updated_at=occurred_at,
    )
    db.add(successor)
    db.flush()
    if allowed:
        campaign = _campaign(db, tenant_id, current.campaign_id)
        timer = timers.schedule(
            db,
            tenant_id=tenant_id,
            identity=_step_identity(successor),
            due_at=due_at,
            output=TimerOutput(DUE_EVENT_TYPE),
            recorded_at=occurred_at,
            expires_at=_as_utc(campaign.evidence_expires_at),
        )
        successor.timer_generation = timer.generation
    else:
        _materialize_successor(
            db,
            tenant_id=tenant_id,
            current=successor,
            timers=timers,
            occurred_at=occurred_at,
        )
    db.flush()


def _halt_remaining_steps(
    db: Session, *, tenant_id: UUID, row: CampaignRecipientStep, at: datetime
) -> None:
    """Persist skipped evidence without inventing a scheduler side effect."""

    class _NoSchedule:
        def schedule(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("a halted successor must never schedule")

    # The branch is non-advancing, so `_materialize_successor` never calls the
    # port. The tiny local object makes that invariant executable.
    _materialize_successor(
        db,
        tenant_id=tenant_id,
        current=row,
        timers=_NoSchedule(),  # type: ignore[arg-type]
        occurred_at=at,
    )


def record_observation(
    db: Session,
    *,
    tenant_id: UUID,
    observation: Observation,
    timers: TimerPort,
    idempotency_expires_at: datetime,
    recorded_at: datetime,
) -> ObservationResult:
    intent = db.scalar(
        select(CampaignDeliveryIntent).where(
            CampaignDeliveryIntent.tenant_id == tenant_id,
            CampaignDeliveryIntent.dispatch_id == observation.dispatch_id,
        )
    )
    if intent is None:
        raise DeliveryIntentNotFound(
            f"dispatch {observation.dispatch_id} was not found"
        )

    def operation(session: Session) -> Mapping[str, object]:
        step = session.scalar(
            select(CampaignRecipientStep)
            .where(
                CampaignRecipientStep.tenant_id == tenant_id,
                CampaignRecipientStep.id == intent.recipient_step_id,
            )
            .with_for_update()
        )
        if step is None:
            raise DeliveryIntentNotFound("delivery intent lost its recipient step")
        row = CampaignObservation(
            tenant_id=tenant_id,
            campaign_id=intent.campaign_id,
            recipient_step_id=step.id,
            delivery_intent_id=intent.id,
            dispatch_id=intent.dispatch_id,
            kind=observation.kind.value,
            delivery_state=(
                observation.delivery_state.value if observation.delivery_state else None
            ),
            source_owner=observation.source_owner,
            source_event_id=observation.source_event_id,
            source_fingerprint=observation.source_fingerprint,
            correlation_ref=observation.correlation_ref,
            occurred_at=observation.occurred_at,
            created_at=recorded_at,
        )
        session.add(row)
        session.flush()
        if observation.kind == ObservationKind.DELIVERY:
            assert observation.delivery_state is not None
            candidate = observation.delivery_state.value
            if (
                _DELIVERY_PRECEDENCE[candidate]
                > _DELIVERY_PRECEDENCE[step.delivery_state]
            ):
                step.delivery_state = candidate
            if step.delivery_state in _RESOLVED_DELIVERY:
                step.status = RecipientStepStatus.RESOLVED.value
                step.resolved_at = step.resolved_at or observation.occurred_at
        elif observation.kind == ObservationKind.OPEN:
            step.first_opened_at = step.first_opened_at or observation.occurred_at
            step.open_count += 1
        elif observation.kind == ObservationKind.CLICK:
            step.first_clicked_at = step.first_clicked_at or observation.occurred_at
            step.click_count += 1
            # CRM parity: a click proves an open even when the open pixel never fired.
            step.first_opened_at = step.first_opened_at or observation.occurred_at
            step.open_count = max(step.open_count, 1)
        elif observation.kind == ObservationKind.REPLY:
            step.first_replied_at = step.first_replied_at or observation.occurred_at
            step.reply_count += 1

        if observation.kind in {
            ObservationKind.REPLY,
            ObservationKind.CONVERSION_CORRELATION,
        }:
            recipient_id = step.recipient_id
            response_payload = {
                "contract": "campaigns.response.v1",
                "campaign_id": str(intent.campaign_id),
                "recipient_id": str(recipient_id),
                "recipient_step_id": str(step.id),
                "observation_id": str(row.id),
                "kind": observation.kind.value,
                "correlation_ref": observation.correlation_ref,
                "occurred_at": observation.occurred_at.isoformat(),
            }
            event = enqueue_event(
                session,
                tenant_id=tenant_id,
                event_type=RESPONSE_EVENT_TYPE,
                payload=response_payload,
                correlation_id=str(row.id),
            )
            session.add(
                CampaignResponse(
                    tenant_id=tenant_id,
                    campaign_id=intent.campaign_id,
                    recipient_id=recipient_id,
                    recipient_step_id=step.id,
                    observation_id=row.id,
                    kind=observation.kind.value,
                    correlation_ref=observation.correlation_ref,
                    fingerprint=fingerprint(response_payload),
                    emitted_outbox_event_id=event.id,
                    occurred_at=observation.occurred_at,
                    created_at=recorded_at,
                )
            )
        step.updated_at = recorded_at
        session.flush()
        if step.resolved_at is not None:
            _materialize_successor(
                session,
                tenant_id=tenant_id,
                current=step,
                timers=timers,
                occurred_at=observation.occurred_at,
            )
        return {"observation_id": str(row.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="campaigns.observation",
        key=f"{observation.source_owner}:{observation.source_event_id}",
        operation=operation,
        fingerprint=fingerprint_of(observation.fingerprint_payload()),
        correlation_id=str(observation.dispatch_id),
        expires_at=idempotency_expires_at,
    )
    rebuild_counters(
        db,
        tenant_id=tenant_id,
        campaign_id=intent.campaign_id,
        rebuilt_at=recorded_at,
    )
    reconcile_campaign(
        db,
        tenant_id=tenant_id,
        campaign_id=intent.campaign_id,
        reconciled_at=recorded_at,
    )
    return ObservationResult(
        observation_id=UUID(str(outcome.result["observation_id"])),
        replayed=outcome.replayed,
    )


def pause_campaign(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    paused_at: datetime,
) -> Campaign:
    campaign = _campaign(db, tenant_id, campaign_id, lock=True)
    if campaign.status not in {
        CampaignStatus.SCHEDULED.value,
        CampaignStatus.SENDING.value,
    }:
        raise InvalidTransition(f"cannot pause a campaign in {campaign.status!r}")
    campaign.status = CampaignStatus.PAUSED.value
    campaign.paused_at = paused_at
    campaign.updated_at = paused_at
    db.flush()
    return campaign


def resume_campaign(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    timers: TimerPort,
    resumed_at: datetime,
) -> Campaign:
    campaign = _campaign(db, tenant_id, campaign_id, lock=True)
    if campaign.status != CampaignStatus.PAUSED.value:
        raise InvalidTransition("only a paused campaign can resume")
    campaign.status = (
        CampaignStatus.SENDING.value
        if campaign.started_at is not None
        else CampaignStatus.SCHEDULED.value
    )
    campaign.paused_at = None
    campaign.updated_at = resumed_at
    for row in db.scalars(
        select(CampaignRecipientStep).where(
            CampaignRecipientStep.tenant_id == tenant_id,
            CampaignRecipientStep.campaign_id == campaign_id,
            CampaignRecipientStep.status.in_(
                [
                    RecipientStepStatus.SCHEDULED.value,
                    RecipientStepStatus.DEFERRED.value,
                ]
            ),
        )
    ):
        due_at = max(_as_utc(row.due_at), resumed_at)
        timer = timers.schedule(
            db,
            tenant_id=tenant_id,
            identity=_step_identity(row),
            due_at=due_at,
            output=TimerOutput(DUE_EVENT_TYPE),
            recorded_at=resumed_at,
            expires_at=_as_utc(campaign.evidence_expires_at),
        )
        row.due_at = due_at
        row.timer_generation = timer.generation
        row.status = RecipientStepStatus.SCHEDULED.value
        row.updated_at = resumed_at
    db.flush()
    return campaign


def cancel_campaign(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    timers: TimerPort,
    reason: str,
    cancelled_at: datetime,
) -> Campaign:
    campaign = _campaign(db, tenant_id, campaign_id, lock=True)
    if campaign.status in {
        CampaignStatus.CANCELLED.value,
        CampaignStatus.COMPLETED.value,
    }:
        if campaign.status == CampaignStatus.CANCELLED.value:
            return campaign
        raise InvalidTransition("a completed campaign cannot be cancelled")
    campaign.status = CampaignStatus.CANCELLED.value
    campaign.cancelled_at = cancelled_at
    campaign.cancellation_reason = reason.strip() or "cancelled"
    campaign.updated_at = cancelled_at
    for row in db.scalars(
        select(CampaignRecipientStep).where(
            CampaignRecipientStep.tenant_id == tenant_id,
            CampaignRecipientStep.campaign_id == campaign_id,
            CampaignRecipientStep.status.in_(
                [
                    RecipientStepStatus.SCHEDULED.value,
                    RecipientStepStatus.DEFERRED.value,
                ]
            ),
        )
    ):
        timers.cancel(
            db,
            tenant_id=tenant_id,
            identity=_step_identity(row),
            recorded_at=cancelled_at,
        )
        row.status = RecipientStepStatus.CANCELLED.value
        row.delivery_state = DeliveryState.CANCELLED.value
        row.resolved_at = cancelled_at
        row.updated_at = cancelled_at
    db.flush()
    rebuild_counters(
        db, tenant_id=tenant_id, campaign_id=campaign_id, rebuilt_at=cancelled_at
    )
    return campaign


def reconcile_campaign(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    reconciled_at: datetime,
) -> Campaign:
    campaign = _campaign(db, tenant_id, campaign_id, lock=True)
    if campaign.status in {
        CampaignStatus.CANCELLED.value,
        CampaignStatus.COMPLETED.value,
        CampaignStatus.DRAFT.value,
    }:
        return campaign
    unresolved = db.scalar(
        select(func.count())
        .select_from(CampaignRecipientStep)
        .where(
            CampaignRecipientStep.tenant_id == tenant_id,
            CampaignRecipientStep.campaign_id == campaign_id,
            CampaignRecipientStep.resolved_at.is_(None),
        )
    )
    if int(unresolved or 0) == 0:
        revision = _revision(db, tenant_id, campaign)
        last_position = db.scalar(
            select(func.max(CampaignStep.position)).where(
                CampaignStep.tenant_id == tenant_id,
                CampaignStep.revision_id == revision.id,
            )
        )
        recipients = int(
            db.scalar(
                select(func.count())
                .select_from(CampaignRecipient)
                .where(
                    CampaignRecipient.tenant_id == tenant_id,
                    CampaignRecipient.campaign_id == campaign_id,
                )
            )
            or 0
        )
        terminal_rows = int(
            db.scalar(
                select(func.count())
                .select_from(CampaignRecipientStep)
                .where(
                    CampaignRecipientStep.tenant_id == tenant_id,
                    CampaignRecipientStep.campaign_id == campaign_id,
                    CampaignRecipientStep.position == int(last_position or 0),
                    CampaignRecipientStep.resolved_at.is_not(None),
                )
            )
            or 0
        )
        suppressed_without_step = _audience_suppressed_count(db, tenant_id, campaign_id)
        if terminal_rows + suppressed_without_step >= recipients:
            campaign.status = CampaignStatus.COMPLETED.value
            campaign.completed_at = reconciled_at
            campaign.updated_at = reconciled_at
            db.flush()
    return campaign


def complete_campaign(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    completed_at: datetime,
) -> Campaign:
    campaign = reconcile_campaign(
        db,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        reconciled_at=completed_at,
    )
    if campaign.status != CampaignStatus.COMPLETED.value:
        raise InvalidTransition("campaign still has unresolved recipient progression")
    return campaign


def request_unsubscribe(
    db: Session,
    *,
    tenant_id: UUID,
    command: UnsubscribeRequest,
    idempotency_expires_at: datetime,
) -> UnsubscribeResult:
    request_fp = fingerprint(
        {
            **command.fingerprint_payload(),
            "address": None,
            "address_hash": _address_hash(command.channel, command.address),
        }
    )

    def operation(session: Session) -> Mapping[str, object]:
        suppress(
            session,
            tenant_id,
            channel=command.channel,
            address=command.address,
            reason="unsubscribe",
        )
        row = CampaignUnsubscribeRequest(
            tenant_id=tenant_id,
            campaign_id=command.campaign_id,
            recipient_id=command.recipient_id,
            channel=command.channel,
            destination_hash=_address_hash(command.channel, command.address),
            source_owner=command.source_owner,
            source_event_id=command.source_event_id,
            source_fingerprint=command.source_fingerprint,
            reason="unsubscribe",
            requested_at=command.requested_at,
            created_at=command.requested_at,
        )
        session.add(row)
        session.flush()
        return {"request_id": str(row.id)}

    outcome = execute_once(
        db,
        tenant_id=tenant_id,
        scope="campaigns.unsubscribe",
        key=f"{command.source_owner}:{command.source_event_id}",
        operation=operation,
        fingerprint=request_fp,
        expires_at=idempotency_expires_at,
    )
    row = db.get(CampaignUnsubscribeRequest, UUID(str(outcome.result["request_id"])))
    if row is None or row.tenant_id != tenant_id:
        raise CampaignError("unsubscribe replay evidence is missing")
    return UnsubscribeResult(request_id=row.id, replayed=outcome.replayed)


def response_facts(
    db: Session, *, tenant_id: UUID, campaign_id: UUID
) -> tuple[ResponseFact, ...]:
    """Return facts an assembly may submit to Sales without making its decision."""

    _campaign(db, tenant_id, campaign_id)
    rows = db.scalars(
        select(CampaignResponse)
        .where(
            CampaignResponse.tenant_id == tenant_id,
            CampaignResponse.campaign_id == campaign_id,
        )
        .order_by(CampaignResponse.occurred_at, CampaignResponse.id)
    )
    return tuple(
        ResponseFact(
            id=row.id,
            campaign_id=row.campaign_id,
            recipient_id=row.recipient_id,
            recipient_step_id=row.recipient_step_id,
            observation_id=row.observation_id,
            kind=ObservationKind(row.kind),
            correlation_ref=row.correlation_ref,
            fingerprint_sha256=row.fingerprint,
            occurred_at=_as_utc(row.occurred_at),
        )
        for row in rows
    )


def _audience_suppressed_ids(
    db: Session, tenant_id: UUID, campaign_id: UUID
) -> set[UUID]:
    recipients = list(
        db.scalars(
            select(CampaignRecipient).where(
                CampaignRecipient.tenant_id == tenant_id,
                CampaignRecipient.campaign_id == campaign_id,
            )
        )
    )
    return {
        recipient.id
        for recipient in recipients
        if (
            (receipt := _latest_audience_receipt(db, tenant_id, recipient.id))
            is not None
            and not receipt.allowed
        )
    }


def _audience_suppressed_count(db: Session, tenant_id: UUID, campaign_id: UUID) -> int:
    return len(_audience_suppressed_ids(db, tenant_id, campaign_id))


def _expected_counters(
    db: Session, tenant_id: UUID, campaign_id: UUID
) -> dict[str, int]:
    recipients = list(
        db.scalars(
            select(CampaignRecipient.id).where(
                CampaignRecipient.tenant_id == tenant_id,
                CampaignRecipient.campaign_id == campaign_id,
            )
        )
    )
    rows = list(
        db.scalars(
            select(CampaignRecipientStep).where(
                CampaignRecipientStep.tenant_id == tenant_id,
                CampaignRecipientStep.campaign_id == campaign_id,
            )
        )
    )
    suppressed_ids = _audience_suppressed_ids(db, tenant_id, campaign_id)
    suppressed_ids.update(
        row.recipient_id
        for row in rows
        if row.delivery_state == DeliveryState.SUPPRESSED.value
    )
    failed_states = {
        DeliveryState.FAILED.value,
        DeliveryState.REJECTED.value,
        DeliveryState.BOUNCED.value,
    }
    return {
        "total_recipients": len(recipients),
        "pending": sum(row.resolved_at is None for row in rows),
        "suppressed": len(suppressed_ids),
        "intents_published": int(
            db.scalar(
                select(func.count())
                .select_from(CampaignDeliveryIntent)
                .where(
                    CampaignDeliveryIntent.tenant_id == tenant_id,
                    CampaignDeliveryIntent.campaign_id == campaign_id,
                )
            )
            or 0
        ),
        "accepted": sum(
            row.delivery_state == DeliveryState.ACCEPTED.value for row in rows
        ),
        "delivered": sum(
            row.delivery_state == DeliveryState.DELIVERED.value for row in rows
        ),
        "failed": sum(row.delivery_state in failed_states for row in rows),
        "opened": sum(row.first_opened_at is not None for row in rows),
        "clicked": sum(row.first_clicked_at is not None for row in rows),
        "replied": sum(row.first_replied_at is not None for row in rows),
    }


def rebuild_counters(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    rebuilt_at: datetime,
) -> CampaignCounter:
    _campaign(db, tenant_id, campaign_id)
    expected = _expected_counters(db, tenant_id, campaign_id)
    counter = db.scalar(
        select(CampaignCounter)
        .where(
            CampaignCounter.tenant_id == tenant_id,
            CampaignCounter.campaign_id == campaign_id,
        )
        .with_for_update()
    )
    if counter is None:
        counter = CampaignCounter(tenant_id=tenant_id, campaign_id=campaign_id)
        db.add(counter)
    for name, value in expected.items():
        setattr(counter, name, value)
    counter.rebuilt_at = rebuilt_at
    counter.updated_at = rebuilt_at
    db.flush()
    return counter


def _missing_publication_ids(
    db: Session, tenant_id: UUID, campaign_id: UUID
) -> tuple[UUID, ...]:
    intents = list(
        db.scalars(
            select(CampaignDeliveryIntent).where(
                CampaignDeliveryIntent.tenant_id == tenant_id,
                CampaignDeliveryIntent.campaign_id == campaign_id,
            )
        )
    )
    return tuple(
        intent.id
        for intent in intents
        if db.scalar(
            select(OutboxEvent.id).where(
                OutboxEvent.tenant_id == tenant_id,
                OutboxEvent.id == intent.outbox_event_id,
            )
        )
        is None
    )


def report_drift(db: Session, *, tenant_id: UUID, campaign_id: UUID) -> DriftReport:
    _campaign(db, tenant_id, campaign_id)
    expected = _expected_counters(db, tenant_id, campaign_id)
    counter = db.scalar(
        select(CampaignCounter).where(
            CampaignCounter.tenant_id == tenant_id,
            CampaignCounter.campaign_id == campaign_id,
        )
    )
    fields: dict[str, tuple[int, int]] = {}
    for name, value in expected.items():
        actual = int(getattr(counter, name)) if counter is not None else 0
        if actual != value:
            fields[name] = (actual, value)
    return DriftReport(
        fields=fields,
        missing_publications=_missing_publication_ids(db, tenant_id, campaign_id),
    )


def repair_missing_publications(
    db: Session,
    *,
    tenant_id: UUID,
    campaign_id: UUID,
    repaired_at: datetime,
) -> int:
    missing = _missing_publication_ids(db, tenant_id, campaign_id)
    repaired = 0
    for intent_id in missing:
        intent = db.scalar(
            select(CampaignDeliveryIntent)
            .where(
                CampaignDeliveryIntent.tenant_id == tenant_id,
                CampaignDeliveryIntent.id == intent_id,
            )
            .with_for_update()
        )
        if intent is None:
            continue
        if intent.address is None or intent.rendered_body is None:
            raise RepairNotPossible(
                f"delivery intent {intent.id} is past its PII repair window"
            )
        event = enqueue_event(
            db,
            tenant_id=tenant_id,
            event_type=DELIVERY_EVENT_TYPE,
            payload=_delivery_payload(intent),
            correlation_id=str(intent.dispatch_id),
        )
        intent.outbox_event_id = event.id
        repaired += 1
    db.flush()
    return repaired


def purge_expired_pii(
    db: Session,
    *,
    tenant_id: UUID,
    before: datetime,
    limit: int,
    scrubbed_at: datetime,
) -> int:
    if limit < 1:
        raise CampaignError("privacy purge limit must be positive")
    recipients = list(
        db.scalars(
            select(CampaignRecipient)
            .where(
                CampaignRecipient.tenant_id == tenant_id,
                CampaignRecipient.pii_expires_at <= before,
                CampaignRecipient.scrubbed_at.is_(None),
            )
            .order_by(CampaignRecipient.pii_expires_at, CampaignRecipient.id)
            .limit(limit)
            .with_for_update()
        )
    )
    for recipient in recipients:
        recipient.address = None
        recipient.context = {}
        recipient.scrubbed_at = scrubbed_at
    remaining = max(0, limit - len(recipients))
    intents: list[CampaignDeliveryIntent] = []
    if remaining:
        intents = list(
            db.scalars(
                select(CampaignDeliveryIntent)
                .where(
                    CampaignDeliveryIntent.tenant_id == tenant_id,
                    CampaignDeliveryIntent.pii_expires_at <= before,
                    CampaignDeliveryIntent.scrubbed_at.is_(None),
                )
                .order_by(
                    CampaignDeliveryIntent.pii_expires_at,
                    CampaignDeliveryIntent.id,
                )
                .limit(remaining)
                .with_for_update()
            )
        )
        for intent in intents:
            intent.address = None
            intent.sender_address = None
            intent.sender_display_name = None
            intent.rendered_subject = None
            intent.rendered_body = None
            intent.scrubbed_at = scrubbed_at
    db.flush()
    return len(recipients) + len(intents)


def campaign_snapshot(
    db: Session, *, tenant_id: UUID, campaign_id: UUID
) -> CampaignSnapshot:
    campaign = _campaign(db, tenant_id, campaign_id)
    revision = _revision(db, tenant_id, campaign)
    counter = db.scalar(
        select(CampaignCounter).where(
            CampaignCounter.tenant_id == tenant_id,
            CampaignCounter.campaign_id == campaign_id,
        )
    )
    if counter is None:
        counter = rebuild_counters(
            db,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            rebuilt_at=datetime.now(UTC),
        )
    recipients: list[RecipientView] = []
    for recipient in db.scalars(
        select(CampaignRecipient)
        .where(
            CampaignRecipient.tenant_id == tenant_id,
            CampaignRecipient.campaign_id == campaign_id,
        )
        .order_by(CampaignRecipient.created_at, CampaignRecipient.id)
    ):
        receipts = tuple(
            ConsentReceiptView(
                phase=row.phase,
                allowed=row.allowed,
                reason=row.reason,
                evaluated_at=row.evaluated_at,
            )
            for row in db.scalars(
                select(CampaignConsentReceipt)
                .where(
                    CampaignConsentReceipt.tenant_id == tenant_id,
                    CampaignConsentReceipt.recipient_id == recipient.id,
                )
                .order_by(CampaignConsentReceipt.evaluated_at)
            )
        )
        step_views = tuple(
            RecipientStepView(
                id=row.id,
                position=row.position,
                status=RecipientStepStatus(row.status),
                delivery_state=DeliveryState(row.delivery_state),
                due_at=row.due_at,
                first_opened_at=row.first_opened_at,
                first_clicked_at=row.first_clicked_at,
                first_replied_at=row.first_replied_at,
            )
            for row in db.scalars(
                select(CampaignRecipientStep)
                .where(
                    CampaignRecipientStep.tenant_id == tenant_id,
                    CampaignRecipientStep.recipient_id == recipient.id,
                )
                .order_by(CampaignRecipientStep.position)
            )
        )
        recipients.append(
            RecipientView(
                id=recipient.id,
                source_owner=recipient.source_owner,
                source_subject_id=recipient.source_subject_id,
                address_hash=recipient.address_hash,
                consent_receipts=receipts,
                steps=step_views,
            )
        )
    return CampaignSnapshot(
        id=campaign.id,
        code=campaign.code,
        name=campaign.name,
        status=CampaignStatus(campaign.status),
        revision_number=revision.revision_number,
        counters=CounterView(
            total_recipients=counter.total_recipients,
            pending=counter.pending,
            suppressed=counter.suppressed,
            intents_published=counter.intents_published,
            accepted=counter.accepted,
            delivered=counter.delivered,
            failed=counter.failed,
            opened=counter.opened,
            clicked=counter.clicked,
            replied=counter.replied,
        ),
        recipients=tuple(recipients),
    )


def recipient_timeline(
    db: Session, *, tenant_id: UUID, recipient_id: UUID
) -> RecipientView:
    recipient = db.scalar(
        select(CampaignRecipient).where(
            CampaignRecipient.tenant_id == tenant_id,
            CampaignRecipient.id == recipient_id,
        )
    )
    if recipient is None:
        raise CampaignNotFound(f"campaign recipient {recipient_id} was not found")
    snapshot = campaign_snapshot(
        db, tenant_id=tenant_id, campaign_id=recipient.campaign_id
    )
    return next(item for item in snapshot.recipients if item.id == recipient_id)


__all__ = [
    "CAMPAIGN_CATEGORY",
    "CampaignError",
    "CampaignNotFound",
    "DeliveryIntentNotFound",
    "InvalidTransition",
    "RepairNotPossible",
    "SequenceBlocked",
    "SnapshotImmutable",
    "accept_due_work",
    "authorize_delivery",
    "campaign_snapshot",
    "cancel_campaign",
    "complete_campaign",
    "create_campaign",
    "ingest_audience",
    "next_send_at",
    "pause_campaign",
    "purge_expired_pii",
    "rebuild_counters",
    "reconcile_campaign",
    "record_observation",
    "repair_missing_publications",
    "report_drift",
    "request_unsubscribe",
    "recipient_timeline",
    "resume_campaign",
    "revise_campaign",
    "schedule_campaign",
]
