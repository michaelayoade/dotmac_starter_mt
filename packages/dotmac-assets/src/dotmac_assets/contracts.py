"""Typed commands and closed lifecycle vocabulary for durable assets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from uuid import UUID


class AssetState(str, Enum):
    REGISTERED = "registered"
    IN_SERVICE = "in_service"
    OUT_OF_SERVICE = "out_of_service"
    RETIRED = "retired"
    DISPOSED = "disposed"


class AssetCondition(str, Enum):
    NEW = "new"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"
    DAMAGED = "damaged"


class AssignmentStatus(str, Enum):
    ACTIVE = "active"
    RETURNED = "returned"
    TRANSFERRED = "transferred"
    LOST = "lost"


class MaintenanceKind(str, Enum):
    PREVENTIVE = "preventive"
    CORRECTIVE = "corrective"
    INSPECTION = "inspection"
    OTHER = "other"


class MaintenanceStatus(str, Enum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class DisposalMethod(str, Enum):
    SALE = "sale"
    SCRAP = "scrap"
    DONATION = "donation"
    THEFT = "theft"
    INSURANCE = "insurance"
    TRADE_IN = "trade_in"
    TRANSFER = "transfer"


class DisposalStatus(str, Enum):
    REQUESTED = "requested"
    APPROVED = "approved"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AssetCreate:
    code: str
    name: str
    kind: str
    description: str | None = None
    serial_number: str | None = None
    tag: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    acquired_on: date | None = None
    location_id: UUID | None = None
    condition: AssetCondition = AssetCondition.GOOD
    source_ref: str | None = None
    actor_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AssetSnapshot:
    id: UUID
    tenant_id: UUID
    code: str
    name: str
    kind: str
    description: str | None
    serial_number: str | None
    tag: str | None
    manufacturer: str | None
    model: str | None
    acquired_on: date | None
    state: AssetState
    condition: AssetCondition
    location_id: UUID | None
    source_ref: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AssignmentCreate:
    asset_id: UUID
    custodian_id: UUID
    starts_on: date
    expected_return_on: date | None = None
    condition_on_issue: AssetCondition | None = None
    location_id: UUID | None = None
    notes: str | None = None
    actor_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AssignmentEnd:
    assignment_id: UUID
    expected: AssignmentStatus
    requested: AssignmentStatus
    ended_on: date
    condition_on_return: AssetCondition | None = None
    notes: str | None = None
    actor_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class AssignmentTransfer:
    assignment_id: UUID
    new_custodian_id: UUID
    transferred_on: date
    expected: AssignmentStatus = AssignmentStatus.ACTIVE
    expected_return_on: date | None = None
    condition_on_issue: AssetCondition | None = None
    new_location_id: UUID | None = None
    notes: str | None = None
    actor_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceSchedule:
    asset_id: UUID
    kind: MaintenanceKind
    summary: str
    scheduled_for: date
    description: str | None = None
    provider_ref: str | None = None
    actor_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceComplete:
    maintenance_id: UUID
    expected: MaintenanceStatus
    completed_on: date
    work_performed: str
    next_due_on: date | None = None
    return_to_service: bool = True
    notes: str | None = None
    actor_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceCancel:
    maintenance_id: UUID
    expected: MaintenanceStatus
    reason: str
    actor_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class DisposalRequest:
    asset_id: UUID
    method: DisposalMethod
    requested_on: date
    reason: str
    actor_id: UUID
    recipient_ref: str | None = None
    external_authorization_ref: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class DisposalApprove:
    disposal_id: UUID
    expected: DisposalStatus
    actor_id: UUID
    approved_at: datetime


@dataclass(frozen=True, slots=True)
class DisposalComplete:
    disposal_id: UUID
    expected: DisposalStatus
    disposed_on: date
    actor_id: UUID
    external_finance_ref: str | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class DisposalCancel:
    disposal_id: UUID
    expected: DisposalStatus
    reason: str
    actor_id: UUID


__all__ = [
    "AssetCondition",
    "AssetCreate",
    "AssetSnapshot",
    "AssetState",
    "AssignmentCreate",
    "AssignmentEnd",
    "AssignmentStatus",
    "AssignmentTransfer",
    "DisposalApprove",
    "DisposalCancel",
    "DisposalComplete",
    "DisposalMethod",
    "DisposalRequest",
    "DisposalStatus",
    "MaintenanceComplete",
    "MaintenanceCancel",
    "MaintenanceKind",
    "MaintenanceSchedule",
    "MaintenanceStatus",
]
