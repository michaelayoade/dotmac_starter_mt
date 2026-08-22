"""Typed commands and derived outcomes for the fulfillment saga owner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Final, Literal, get_args
from uuid import UUID


class FulfillmentError(ValueError):
    """Base fail-closed contract error."""


class FulfillmentConflict(FulfillmentError):
    """A command conflicts with recorded fulfillment evidence."""


class FulfillmentNotFound(LookupError):
    """The requested run, step, attempt, or request does not exist."""


class StaleOutcome(FulfillmentConflict):
    """An outcome addresses an attempt superseded by a later dispatch."""


class OutcomeClass(str, Enum):
    SUCCEEDED = "succeeded"
    RETRYABLE = "retryable"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    TERMINAL = "terminal"


class RunProgress(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PARTIAL = "partial"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CompensationDisposition(str, Enum):
    SUCCEEDED = "succeeded"
    REFUSED = "refused"
    NOT_SUPPORTED = "not_supported"
    MANUAL_REQUIRED = "manual_required"
    RETRYABLE = "retryable"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class RepairAction(str, Enum):
    """Explicit operator actions; adapters authorize these exact codes."""

    REDRIVE_ATTEMPT = "fulfillment.repair.attempt_redriven"
    REQUEST_COMPENSATION = "fulfillment.repair.compensation_requested"
    RECORD_TERMINAL_OUTCOME = "fulfillment.repair.outcome_terminalized"


def _required(name: str, value: str, limit: int = 200) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        raise FulfillmentConflict(
            f"{name} is required and must be at most {limit} characters"
        )
    return cleaned


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise FulfillmentConflict(f"{name} must be timezone-aware")


#: The closed repair-actor vocabulary, declared ONCE so the static type and the
#: constructor's refusal cannot drift apart. `test_audit_actor_callers` reads
#: this annotation to prove an `actor.actor_type` audit write is auditable
#: without a literal at the call site; widening it there without widening
#: `__post_init__` fails that guard rather than silently enlarging the set.
RepairActorType = Literal["user", "api_key", "service"]

_REPAIR_ACTOR_TYPES: Final[frozenset[str]] = frozenset(get_args(RepairActorType))


@dataclass(frozen=True, slots=True)
class RepairActor:
    """Canonical non-system actor for an authorized repair command."""

    actor_type: RepairActorType
    actor_id: str
    actor_label: str | None = None
    actor_party_id: UUID | None = None

    def __post_init__(self) -> None:
        actor_type = _required("actor_type", self.actor_type, 32)
        if actor_type not in _REPAIR_ACTOR_TYPES:
            raise FulfillmentConflict(
                "operator repair actor_type must be "
                + ", ".join(sorted(_REPAIR_ACTOR_TYPES))
            )
        object.__setattr__(self, "actor_type", actor_type)
        object.__setattr__(self, "actor_id", _required("actor_id", self.actor_id, 120))
        if self.actor_label is not None:
            object.__setattr__(
                self, "actor_label", _required("actor_label", self.actor_label, 160)
            )

    def as_fingerprint_payload(self) -> dict[str, object]:
        return {
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "actor_party_id": (
                str(self.actor_party_id) if self.actor_party_id is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class StepDefinition:
    step_id: str
    participant_code: str
    command_type: str
    spec: Mapping[str, object] = field(default_factory=dict)
    line_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "step_id", _required("step_id", self.step_id, 120))
        object.__setattr__(
            self,
            "participant_code",
            _required("participant_code", self.participant_code, 120),
        )
        object.__setattr__(
            self, "command_type", _required("command_type", self.command_type, 120)
        )
        if self.line_ref is not None:
            object.__setattr__(
                self, "line_ref", _required("line_ref", self.line_ref, 255)
            )
        object.__setattr__(self, "spec", MappingProxyType(dict(self.spec)))

    def as_fingerprint_payload(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "participant_code": self.participant_code,
            "command_type": self.command_type,
            "spec": dict(self.spec),
            "line_ref": self.line_ref,
        }


@dataclass(frozen=True, slots=True)
class RunCreate:
    intent_ref: str
    idempotency_key: str
    correlation_id: str
    steps: tuple[StepDefinition, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "intent_ref", _required("intent_ref", self.intent_ref, 255)
        )
        object.__setattr__(
            self, "idempotency_key", _required("idempotency_key", self.idempotency_key)
        )
        object.__setattr__(
            self, "correlation_id", _required("correlation_id", self.correlation_id)
        )
        object.__setattr__(self, "steps", tuple(self.steps))
        if not self.steps:
            raise FulfillmentConflict("a fulfillment run needs at least one step")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise FulfillmentConflict(
                "fulfillment step ids must be unique within a run"
            )

    def as_fingerprint_payload(self) -> dict[str, object]:
        return {
            "intent_ref": self.intent_ref,
            "correlation_id": self.correlation_id,
            "steps": [step.as_fingerprint_payload() for step in self.steps],
        }


@dataclass(frozen=True, slots=True)
class AttemptRequest:
    step_id: str
    command_id: str
    operation_id: str
    idempotency_key: str
    correlation_id: str
    requested_at: datetime
    reobserve_at: datetime
    causation_id: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "step_id",
            "command_id",
            "operation_id",
            "idempotency_key",
            "correlation_id",
        ):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        if self.causation_id is not None:
            object.__setattr__(
                self, "causation_id", _required("causation_id", self.causation_id)
            )
        _aware("requested_at", self.requested_at)
        _aware("reobserve_at", self.reobserve_at)
        if self.reobserve_at <= self.requested_at:
            raise FulfillmentConflict("reobserve_at must be after requested_at")

    def as_fingerprint_payload(self) -> dict[str, object]:
        return {
            "step_id": self.step_id,
            "command_id": self.command_id,
            "operation_id": self.operation_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "requested_at": self.requested_at.isoformat(),
            "reobserve_at": self.reobserve_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ParticipantCommand:
    run_id: UUID
    step_record_id: UUID
    attempt_id: UUID
    step_id: str
    participant_code: str
    command_type: str
    command_id: str
    operation_id: str
    correlation_id: str
    causation_id: str | None
    spec: Mapping[str, object]
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class OutcomeMessage:
    outcome_id: str
    participant_code: str
    command_id: str
    operation_id: str
    classification: OutcomeClass
    occurred_at: datetime
    provider_status: str | None = None
    error_class: str | None = None
    reason_code: str | None = None
    detail: Mapping[str, object] = field(default_factory=dict)
    reobserve_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("outcome_id", "participant_code", "command_id", "operation_id"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        for name in ("provider_status", "error_class", "reason_code"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _required(name, value, 120))
        _aware("occurred_at", self.occurred_at)
        if self.reobserve_at is not None:
            _aware("reobserve_at", self.reobserve_at)
        if (
            self.classification
            in (OutcomeClass.RETRYABLE, OutcomeClass.RECONCILIATION_REQUIRED)
            and self.reobserve_at is None
        ):
            raise FulfillmentConflict(
                "retryable and reconciliation-required outcomes need reobserve_at"
            )
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))

    def as_fingerprint_payload(self) -> dict[str, object]:
        return {
            "outcome_id": self.outcome_id,
            "participant_code": self.participant_code,
            "command_id": self.command_id,
            "operation_id": self.operation_id,
            "provider_status": self.provider_status,
            "error_class": self.error_class,
            "reason_code": self.reason_code,
            "detail": dict(self.detail),
            "reobserve_at": self.reobserve_at,
        }


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    receipt_id: UUID
    classification: OutcomeClass
    replayed: bool


@dataclass(frozen=True, slots=True)
class ReobservationSchedule:
    run_id: UUID
    step_record_id: UUID
    attempt_id: UUID
    participant_code: str
    operation_id: str
    due_at: datetime
    output_event_type: str = "fulfillment.reobserve_due.v1"


@dataclass(frozen=True, slots=True)
class RunProgressSnapshot:
    run_id: UUID
    progress: RunProgress
    settled_prefix: int
    succeeded_step_ids: tuple[str, ...]
    retryable_step_ids: tuple[str, ...]
    reconciliation_step_ids: tuple[str, ...]
    terminal_step_ids: tuple[str, ...]
    pending_step_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepairAttention:
    """One derived repair-queue item; no mutable queue row exists."""

    run_id: UUID
    step_record_id: UUID
    step_id: str
    attempt_id: UUID | None
    participant_code: str
    classification: OutcomeClass | None
    reason_code: str | None


@dataclass(frozen=True, slots=True)
class CompensationRequest:
    idempotency_key: str
    reason: str
    requested_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "idempotency_key", _required("idempotency_key", self.idempotency_key)
        )
        object.__setattr__(self, "reason", _required("reason", self.reason, 500))
        _aware("requested_at", self.requested_at)


@dataclass(frozen=True, slots=True)
class CompensationCommand:
    request_id: UUID
    run_id: UUID
    step_record_id: UUID
    original_attempt_id: UUID
    step_id: str
    participant_code: str
    command_id: str
    operation_id: str
    reason: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class CompensationOutcome:
    outcome_id: str
    command_id: str
    participant_code: str
    disposition: CompensationDisposition
    occurred_at: datetime
    reason_code: str | None = None
    detail: Mapping[str, object] = field(default_factory=dict)
    reobserve_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in ("outcome_id", "command_id", "participant_code"):
            object.__setattr__(self, name, _required(name, getattr(self, name)))
        if self.reason_code is not None:
            object.__setattr__(
                self, "reason_code", _required("reason_code", self.reason_code, 120)
            )
        _aware("occurred_at", self.occurred_at)
        if self.reobserve_at is not None:
            _aware("reobserve_at", self.reobserve_at)
        if (
            self.disposition
            in {
                CompensationDisposition.RETRYABLE,
                CompensationDisposition.RECONCILIATION_REQUIRED,
            }
            and self.reobserve_at is None
        ):
            raise FulfillmentConflict(
                "uncertain compensation outcomes need reobserve_at"
            )
        object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))

    def as_fingerprint_payload(self) -> dict[str, object]:
        return {
            "outcome_id": self.outcome_id,
            "command_id": self.command_id,
            "participant_code": self.participant_code,
            "disposition": self.disposition.value,
            "reason_code": self.reason_code,
            "detail": dict(self.detail),
            "occurred_at": self.occurred_at.isoformat(),
            "reobserve_at": (
                self.reobserve_at.isoformat() if self.reobserve_at is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class CompensationRecord:
    receipt_id: UUID
    disposition: CompensationDisposition
    replayed: bool


__all__ = [
    "AttemptRequest",
    "CompensationCommand",
    "CompensationDisposition",
    "CompensationOutcome",
    "CompensationRecord",
    "CompensationRequest",
    "FulfillmentConflict",
    "FulfillmentError",
    "FulfillmentNotFound",
    "OutcomeClass",
    "OutcomeMessage",
    "OutcomeRecord",
    "ParticipantCommand",
    "RepairAction",
    "RepairActor",
    "RepairAttention",
    "ReobservationSchedule",
    "RunCreate",
    "RunProgress",
    "RunProgressSnapshot",
    "StaleOutcome",
    "StepDefinition",
]
