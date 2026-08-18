"""Typed campaign commands, values, ports and read contracts.

The types contain no product or provider vocabulary. Assemblies translate their
own facts into these values and bind the three mechanics ports. Consent,
idempotency and outbox are not ports: their one owner is the kernel and the
service calls it directly.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from enum import Enum
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session


class ContractError(ValueError):
    """A malformed public contract value."""


class CampaignKind(str, Enum):
    ONE_TIME = "one_time"
    NURTURE = "nurture"


class CampaignStatus(str, Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    SENDING = "sending"
    PAUSED = "paused"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class RecipientStepStatus(str, Enum):
    SCHEDULED = "scheduled"
    DEFERRED = "deferred"
    INTENT_PUBLISHED = "intent_published"
    SUPPRESSED = "suppressed"
    SKIPPED_PREDECESSOR = "skipped_predecessor"
    CANCELLED = "cancelled"
    RESOLVED = "resolved"


class DeliveryState(str, Enum):
    PENDING = "pending"
    INTENT_PUBLISHED = "intent_published"
    ACCEPTED = "accepted"
    FAILED = "failed"
    REJECTED = "rejected"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    SUPPRESSED = "suppressed"
    CANCELLED = "cancelled"


class ObservationKind(str, Enum):
    DELIVERY = "delivery"
    OPEN = "open"
    CLICK = "click"
    REPLY = "reply"
    CONVERSION_CORRELATION = "conversion_correlation"


def _required(name: str, value: str, limit: int = 255) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > limit:
        raise ContractError(f"{name} is required and must be at most {limit} chars")
    return normalized


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{name} must be timezone-aware")


def _sha256(name: str, value: str) -> str:
    normalized = value.removeprefix("sha256:").lower()
    if len(normalized) != 64 or any(ch not in "0123456789abcdef" for ch in normalized):
        raise ContractError(f"{name} must be a SHA-256 hex digest")
    return normalized


def fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class SequenceStep:
    position: int
    delay: timedelta
    template_slug: str
    template_channel: str
    advance_on: frozenset[DeliveryState] = field(
        default_factory=lambda: frozenset({DeliveryState.DELIVERED})
    )

    def __post_init__(self) -> None:
        if self.position < 0:
            raise ContractError("step position cannot be negative")
        if self.delay < timedelta(0):
            raise ContractError("step delay cannot be negative")
        object.__setattr__(
            self, "template_slug", _required("template_slug", self.template_slug, 120)
        )
        object.__setattr__(
            self,
            "template_channel",
            _required("template_channel", self.template_channel, 40).lower(),
        )
        if self.position > 0 and not self.advance_on:
            raise ContractError(
                "a delayed step requires at least one predecessor outcome"
            )

    def as_dict(self) -> dict[str, object]:
        return {
            "position": self.position,
            "delay_seconds": int(self.delay.total_seconds()),
            "template_slug": self.template_slug,
            "template_channel": self.template_channel,
            "advance_on": sorted(state.value for state in self.advance_on),
        }


@dataclass(frozen=True, slots=True)
class CreateCampaign:
    code: str
    name: str
    kind: CampaignKind
    channel: str
    timezone: str
    scheduled_at: datetime
    send_window_start: time
    send_window_end: time
    sender_key: str
    steps: tuple[SequenceStep, ...]
    evidence_expires_at: datetime
    pii_expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", _required("code", self.code, 80).lower())
        object.__setattr__(self, "name", _required("name", self.name, 200))
        object.__setattr__(
            self, "channel", _required("channel", self.channel, 40).lower()
        )
        object.__setattr__(self, "timezone", _required("timezone", self.timezone, 80))
        object.__setattr__(
            self, "sender_key", _required("sender_key", self.sender_key, 120)
        )
        _aware("scheduled_at", self.scheduled_at)
        _aware("evidence_expires_at", self.evidence_expires_at)
        _aware("pii_expires_at", self.pii_expires_at)
        if self.pii_expires_at > self.evidence_expires_at:
            raise ContractError("PII retention cannot outlive campaign evidence")
        if not self.steps:
            raise ContractError("a campaign requires at least one step")
        expected = list(range(len(self.steps)))
        actual = [step.position for step in self.steps]
        if actual != expected:
            raise ContractError("step positions must be contiguous and start at zero")
        if self.kind == CampaignKind.ONE_TIME and len(self.steps) != 1:
            raise ContractError("a one-time campaign has exactly one step")
        if self.steps[0].delay != timedelta(0):
            raise ContractError("the first campaign step has zero delay")

    def as_dict(self) -> dict[str, object]:
        """Constructor-compatible values, also stable under kernel fingerprinting."""
        return {
            "code": self.code,
            "name": self.name,
            "kind": self.kind,
            "channel": self.channel,
            "timezone": self.timezone,
            "scheduled_at": self.scheduled_at,
            "send_window_start": self.send_window_start,
            "send_window_end": self.send_window_end,
            "sender_key": self.sender_key,
            "steps": self.steps,
            "evidence_expires_at": self.evidence_expires_at,
            "pii_expires_at": self.pii_expires_at,
        }

    def fingerprint_payload(self) -> dict[str, object]:
        payload = self.as_dict()
        payload["kind"] = self.kind.value
        payload["steps"] = [step.as_dict() for step in self.steps]
        return payload


@dataclass(frozen=True, slots=True)
class ReviseCampaign:
    name: str
    kind: CampaignKind
    channel: str
    timezone: str
    scheduled_at: datetime
    send_window_start: time
    send_window_end: time
    sender_key: str
    steps: tuple[SequenceStep, ...]
    evidence_expires_at: datetime
    pii_expires_at: datetime

    def as_create(self, code: str) -> CreateCampaign:
        return CreateCampaign(
            code=code,
            name=self.name,
            kind=self.kind,
            channel=self.channel,
            timezone=self.timezone,
            scheduled_at=self.scheduled_at,
            send_window_start=self.send_window_start,
            send_window_end=self.send_window_end,
            sender_key=self.sender_key,
            steps=self.steps,
            evidence_expires_at=self.evidence_expires_at,
            pii_expires_at=self.pii_expires_at,
        )


@dataclass(frozen=True, slots=True)
class AudienceCandidate:
    source_subject_id: str
    channel: str
    address: str
    context: Mapping[str, object]
    eligibility_reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_subject_id",
            _required("source_subject_id", self.source_subject_id, 255),
        )
        object.__setattr__(
            self, "channel", _required("channel", self.channel, 40).lower()
        )
        object.__setattr__(self, "address", _required("address", self.address, 500))
        object.__setattr__(
            self,
            "eligibility_reason",
            _required("eligibility_reason", self.eligibility_reason, 120),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "source_subject_id": self.source_subject_id,
            "channel": self.channel,
            "address": self.address,
            "context": dict(self.context),
            "eligibility_reason": self.eligibility_reason,
        }


@dataclass(frozen=True, slots=True)
class AudienceBatch:
    source_owner: str
    source_version: str
    source_fingerprint: str
    eligibility_reason: str
    candidates: tuple[AudienceCandidate, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_owner", _required("source_owner", self.source_owner, 120)
        )
        object.__setattr__(
            self,
            "source_version",
            _required("source_version", self.source_version, 120),
        )
        object.__setattr__(
            self,
            "source_fingerprint",
            _sha256("source_fingerprint", self.source_fingerprint),
        )
        object.__setattr__(
            self,
            "eligibility_reason",
            _required("eligibility_reason", self.eligibility_reason, 120),
        )
        if not self.candidates:
            raise ContractError("an audience batch requires at least one candidate")

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "source_owner": self.source_owner,
            "source_version": self.source_version,
            "source_fingerprint": self.source_fingerprint,
            "eligibility_reason": self.eligibility_reason,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class TimerIdentity:
    owner: str
    entity_kind: str
    entity_id: str
    purpose: str

    def __post_init__(self) -> None:
        for name, limit in (
            ("owner", 120),
            ("entity_kind", 120),
            ("entity_id", 255),
            ("purpose", 120),
        ):
            object.__setattr__(self, name, _required(name, getattr(self, name), limit))


@dataclass(frozen=True, slots=True)
class TimerOutput:
    event_type: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "event_type", _required("event_type", self.event_type, 120)
        )


@dataclass(frozen=True, slots=True)
class DueWorkTrigger:
    timer_id: UUID
    identity: TimerIdentity
    generation: int
    due_at: datetime
    output_event_type: str

    def __post_init__(self) -> None:
        if self.generation < 1:
            raise ContractError("timer generation must be positive")
        _aware("due_at", self.due_at)
        _required("output_event_type", self.output_event_type, 120)


@dataclass(frozen=True, slots=True)
class ScheduledTimer:
    timer_id: UUID
    identity: TimerIdentity
    generation: int
    due_at: datetime
    output: TimerOutput

    def trigger(self) -> DueWorkTrigger:
        return DueWorkTrigger(
            timer_id=self.timer_id,
            identity=self.identity,
            generation=self.generation,
            due_at=self.due_at,
            output_event_type=self.output.event_type,
        )


@dataclass(frozen=True, slots=True)
class TimerAcceptance:
    current: bool
    replayed: bool = False
    reason: str = "current"


@dataclass(frozen=True, slots=True)
class TimerCancellation:
    cancelled: bool
    reason: str


class TimerPort(Protocol):
    def schedule(
        self,
        db: Session | None,
        *,
        tenant_id: UUID,
        identity: TimerIdentity,
        due_at: datetime,
        output: TimerOutput,
        recorded_at: datetime,
        expires_at: datetime | None,
    ) -> ScheduledTimer: ...

    def cancel(
        self,
        db: Session | None,
        *,
        tenant_id: UUID,
        identity: TimerIdentity,
        recorded_at: datetime,
    ) -> TimerCancellation: ...

    def accept(
        self,
        db: Session | None,
        *,
        tenant_id: UUID,
        trigger: DueWorkTrigger,
        accepted_at: datetime | None = None,
    ) -> TimerAcceptance: ...


@dataclass(frozen=True, slots=True)
class RenderRequest:
    tenant_id: UUID
    template_slug: str
    channel: str
    context: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    template_revision: str
    subject: str | None
    body: str
    fingerprint_sha256: str

    def __post_init__(self) -> None:
        _required("template_revision", self.template_revision, 255)
        if not self.body:
            raise ContractError("rendered body is required")
        _sha256("fingerprint_sha256", self.fingerprint_sha256)


class Renderer(Protocol):
    def render(self, request: RenderRequest) -> RenderedMessage: ...


@dataclass(frozen=True, slots=True)
class SenderRequest:
    tenant_id: UUID
    channel: str
    sender_key: str


@dataclass(frozen=True, slots=True)
class SenderSnapshot:
    sender_key: str
    address: str
    display_name: str | None
    fingerprint_sha256: str

    def __post_init__(self) -> None:
        _required("sender_key", self.sender_key, 120)
        _required("address", self.address, 500)
        _sha256("fingerprint_sha256", self.fingerprint_sha256)


class SenderResolver(Protocol):
    def resolve(self, request: SenderRequest) -> SenderSnapshot: ...


@dataclass(frozen=True, slots=True)
class AudienceIngestionResult:
    audience_id: UUID
    created: int
    eligible: int
    suppressed: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class DueWorkResult:
    recipient_step_id: UUID
    status: str
    delivery_intent_id: UUID | None = None
    dispatch_id: UUID | None = None
    next_due_at: datetime | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class Observation:
    dispatch_id: UUID
    kind: ObservationKind
    source_owner: str
    source_event_id: str
    source_fingerprint: str
    occurred_at: datetime
    delivery_state: DeliveryState | None = None
    correlation_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_owner", _required("source_owner", self.source_owner, 120)
        )
        object.__setattr__(
            self,
            "source_event_id",
            _required("source_event_id", self.source_event_id, 255),
        )
        object.__setattr__(
            self,
            "source_fingerprint",
            _sha256("source_fingerprint", self.source_fingerprint),
        )
        _aware("occurred_at", self.occurred_at)
        if self.kind == ObservationKind.DELIVERY and self.delivery_state is None:
            raise ContractError("a delivery observation requires delivery_state")
        if self.kind != ObservationKind.DELIVERY and self.delivery_state is not None:
            raise ContractError("only a delivery observation carries delivery_state")

    def fingerprint_payload(self) -> dict[str, object]:
        return {
            "dispatch_id": self.dispatch_id,
            "kind": self.kind.value,
            "delivery_state": self.delivery_state.value
            if self.delivery_state
            else None,
            "source_owner": self.source_owner,
            "source_event_id": self.source_event_id,
            "source_fingerprint": self.source_fingerprint,
            "occurred_at": self.occurred_at,
            "correlation_ref": self.correlation_ref,
        }


@dataclass(frozen=True, slots=True)
class ObservationResult:
    observation_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class ConsentReceiptView:
    phase: str
    allowed: bool
    reason: str | None
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class RecipientStepView:
    id: UUID
    position: int
    status: RecipientStepStatus
    delivery_state: DeliveryState
    due_at: datetime
    first_opened_at: datetime | None
    first_clicked_at: datetime | None
    first_replied_at: datetime | None


@dataclass(frozen=True, slots=True)
class RecipientView:
    id: UUID
    source_owner: str
    source_subject_id: str
    address_hash: str
    consent_receipts: tuple[ConsentReceiptView, ...]
    steps: tuple[RecipientStepView, ...]


@dataclass(frozen=True, slots=True)
class CounterView:
    total_recipients: int
    pending: int
    suppressed: int
    intents_published: int
    accepted: int
    delivered: int
    failed: int
    opened: int
    clicked: int
    replied: int


@dataclass(frozen=True, slots=True)
class CampaignSnapshot:
    id: UUID
    code: str
    name: str
    status: CampaignStatus
    revision_number: int
    counters: CounterView
    recipients: tuple[RecipientView, ...]


@dataclass(frozen=True, slots=True)
class DriftReport:
    fields: Mapping[str, tuple[int, int]]
    missing_publications: tuple[UUID, ...]

    @property
    def has_drift(self) -> bool:
        return bool(self.fields or self.missing_publications)


@dataclass(frozen=True, slots=True)
class DeliveryGateResult:
    dispatch_id: UUID
    allowed: bool
    reason: str | None
    consent_receipt_id: UUID


__all__ = [
    "AudienceBatch",
    "AudienceCandidate",
    "AudienceIngestionResult",
    "CampaignKind",
    "CampaignSnapshot",
    "CampaignStatus",
    "ConsentReceiptView",
    "ContractError",
    "CounterView",
    "CreateCampaign",
    "DeliveryGateResult",
    "DeliveryState",
    "DriftReport",
    "DueWorkResult",
    "DueWorkTrigger",
    "Observation",
    "ObservationKind",
    "ObservationResult",
    "RecipientStepStatus",
    "RecipientStepView",
    "RecipientView",
    "RenderRequest",
    "RenderedMessage",
    "Renderer",
    "ReviseCampaign",
    "ScheduledTimer",
    "SenderRequest",
    "SenderResolver",
    "SenderSnapshot",
    "SequenceStep",
    "TimerAcceptance",
    "TimerCancellation",
    "TimerIdentity",
    "TimerOutput",
    "TimerPort",
    "fingerprint",
]
