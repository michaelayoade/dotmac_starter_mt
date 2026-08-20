"""Immutable provider-neutral network command lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class CommandState(StrEnum):
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RequestCommand:
    operation_code: str
    target_ref: str
    capability_code: str
    parameters: tuple[tuple[str, str], ...]
    request_fingerprint: str
    correlation_ref: str
    requested_by_ref: str
    requires_approval: bool = True


@dataclass(frozen=True, slots=True)
class ApproveCommand:
    command_id: UUID
    expected: CommandState
    approved_by_ref: str
    approved_at: datetime
    approval_ref: str


@dataclass(frozen=True, slots=True)
class RejectCommand:
    command_id: UUID
    expected: CommandState
    rejected_by_ref: str
    reason: str
    rejected_at: datetime


@dataclass(frozen=True, slots=True)
class MarkDispatched:
    command_id: UUID
    expected: CommandState
    dispatch_ref: str
    plugin_capability: str
    dispatched_at: datetime


@dataclass(frozen=True, slots=True)
class RecordExecutionObservation:
    command_id: UUID
    dispatch_ref: str
    outcome: ExecutionOutcome
    observed_at: datetime
    evidence_ref: str
    result_fingerprint: str
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class ReconcileCommand:
    command_id: UUID
    observed_dispatch_refs: tuple[str, ...]
    as_of: datetime


@dataclass(frozen=True, slots=True)
class RecoverCommand:
    command_id: UUID
    expected: CommandState
    evidence_ref: str
    requested_by_ref: str
    requested_at: datetime


@dataclass(frozen=True, slots=True)
class CommandLookup:
    command_id: UUID | None = None
    correlation_ref: str | None = None


@dataclass(frozen=True, slots=True)
class DispatchQuery:
    command_id: UUID
    include_terminal: bool = True


@dataclass(frozen=True, slots=True)
class ExecutionEvidenceQuery:
    command_id: UUID
    since: datetime | None = None


@dataclass(frozen=True, slots=True)
class CommandSnapshot:
    id: UUID
    tenant_id: UUID
    operation_code: str
    target_ref: str
    capability_code: str
    parameters: tuple[tuple[str, str], ...]
    request_fingerprint: str
    correlation_ref: str
    requested_by_ref: str
    state: CommandState
    requested_at: datetime
    approved_at: datetime | None
    terminal_at: datetime | None


@dataclass(frozen=True, slots=True)
class DispatchEnvelope:
    command_id: UUID
    tenant_id: UUID
    dispatch_ref: str
    capability_code: str
    target_ref: str
    parameters: tuple[tuple[str, str], ...]
    request_fingerprint: str


@dataclass(frozen=True, slots=True)
class ExecutionEvidence:
    id: UUID
    tenant_id: UUID
    command_id: UUID
    dispatch_ref: str
    outcome: ExecutionOutcome
    observed_at: datetime
    evidence_ref: str
    result_fingerprint: str
    error_code: str | None


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    command: CommandSnapshot
    missing_dispatch_refs: tuple[str, ...]
    unexpected_dispatch_refs: tuple[str, ...]
    changed: bool
    reconciled_at: datetime


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    command: CommandSnapshot
    dispatch: DispatchEnvelope | None
    recovered: bool


@dataclass(frozen=True, slots=True)
class CommandRequested:
    event_id: UUID
    tenant_id: UUID
    command: CommandSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CommandReady:
    event_id: UUID
    tenant_id: UUID
    command: CommandSnapshot
    dispatch: DispatchEnvelope
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CommandCompleted:
    event_id: UUID
    tenant_id: UUID
    command: CommandSnapshot
    evidence: ExecutionEvidence
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CommandFailed:
    event_id: UUID
    tenant_id: UUID
    command: CommandSnapshot
    evidence: ExecutionEvidence
    occurred_at: datetime


__all__ = [
    "ApproveCommand",
    "CommandCompleted",
    "CommandFailed",
    "CommandLookup",
    "CommandReady",
    "CommandRequested",
    "CommandSnapshot",
    "CommandState",
    "DispatchEnvelope",
    "DispatchQuery",
    "ExecutionEvidence",
    "ExecutionEvidenceQuery",
    "ExecutionOutcome",
    "MarkDispatched",
    "ReconcileCommand",
    "ReconciliationReport",
    "RecordExecutionObservation",
    "RecoverCommand",
    "RecoveryResult",
    "RejectCommand",
    "RequestCommand",
]
