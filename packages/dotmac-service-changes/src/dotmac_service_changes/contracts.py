"""Service-change request commands, vocabularies and outcomes."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


class ServiceChangeError(Exception):
    """Base refusal."""


class Conflict(ServiceChangeError):
    """The request, checkpoint or evidence state is inadmissible."""


class ServiceChangeType(enum.StrEnum):
    PLAN_CHANGE = "PLAN_CHANGE"
    RELOCATION = "RELOCATION"
    VACATION_HOLD = "VACATION_HOLD"
    VACATION_RESUME = "VACATION_RESUME"


class ServiceChangeStatus(enum.StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    APPLIED = "APPLIED"
    CANCELLED = "CANCELLED"


class ExecutionState(enum.StrEnum):
    """The linear execution chain, ported from Sub's `execution_state`.

    Linear on purpose: `advance_execution` accepts only the next state, so a
    request cannot reach `FULFILLMENT_RELEASED` without a recorded settlement,
    which is the ordering the source enforced by convention and this module
    enforces by refusal.
    """

    AWAITING_PAYMENT = "AWAITING_PAYMENT"
    PAYMENT_SETTLED = "PAYMENT_SETTLED"
    FULFILLMENT_RELEASED = "FULFILLMENT_RELEASED"
    DELIVERY_IN_PROGRESS = "DELIVERY_IN_PROGRESS"
    DELIVERY_VERIFIED = "DELIVERY_VERIFIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# The declared order. `FAILED` is reachable from any non-terminal state and is
# therefore deliberately absent from this chain.
EXECUTION_ORDER: tuple[ExecutionState, ...] = (
    ExecutionState.AWAITING_PAYMENT,
    ExecutionState.PAYMENT_SETTLED,
    ExecutionState.FULFILLMENT_RELEASED,
    ExecutionState.DELIVERY_IN_PROGRESS,
    ExecutionState.DELIVERY_VERIFIED,
    ExecutionState.COMPLETED,
)


class CheckpointDomain(enum.StrEnum):
    """The owners a service change crosses. One named domain per checkpoint."""

    QUALIFICATION = "QUALIFICATION"
    BILLING = "BILLING"
    PAYMENT = "PAYMENT"
    FULFILLMENT = "FULFILLMENT"
    SERVICE_ORDER = "SERVICE_ORDER"
    WORK_ORDER = "WORK_ORDER"
    SERVICE_ACCESS = "SERVICE_ACCESS"


@dataclass(frozen=True, slots=True)
class OpenServiceChange:
    subject_reference: str
    change_type: ServiceChangeType
    confirmation_key: str
    current_offer_reference: str | None = None
    requested_offer_reference: str | None = None
    target_location_reference: str | None = None
    effective_from: datetime | None = None


@dataclass(frozen=True, slots=True)
class DecideServiceChange:
    request_id: UUID
    approve: bool
    actor: str
    rationale: str
    decided_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecordCheckpoint:
    request_id: UUID
    domain: CheckpointDomain
    evidence_reference: str
    facts: dict[str, Any]
    observed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AdvanceExecution:
    request_id: UUID
    to_state: ExecutionState
    reason_code: str
    at: datetime | None = None


__all__ = [
    "EXECUTION_ORDER",
    "AdvanceExecution",
    "CheckpointDomain",
    "Conflict",
    "DecideServiceChange",
    "ExecutionState",
    "OpenServiceChange",
    "RecordCheckpoint",
    "ServiceChangeError",
    "ServiceChangeStatus",
    "ServiceChangeType",
]
