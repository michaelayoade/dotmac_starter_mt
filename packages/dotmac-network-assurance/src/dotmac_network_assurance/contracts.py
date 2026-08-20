"""Immutable evidence-only network incident and SLA contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class IncidentState(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class ImpactSeverity(StrEnum):
    INFO = "info"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class MaintenanceState(StrEnum):
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class OpenIncident:
    code: str
    summary: str
    severity: ImpactSeverity
    detected_at: datetime
    detection_ref: str
    source_observation_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClassifyImpact:
    incident_id: UUID
    subject_ref: str
    subject_kind: str
    severity: ImpactSeverity
    topology_path_ref: str | None
    reason_code: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class UpdateIncident:
    incident_id: UUID
    expected: IncidentState
    requested: IncidentState
    evidence_ref: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class ResolveIncident:
    incident_id: UUID
    expected: IncidentState
    resolution_code: str
    resolution_summary: str
    resolved_at: datetime
    evidence_ref: str


@dataclass(frozen=True, slots=True)
class ScheduleMaintenance:
    code: str
    summary: str
    starts_at: datetime
    ends_at: datetime
    scope_refs: tuple[str, ...]
    change_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RecordNotificationEvidence:
    incident_id: UUID
    subject_ref: str
    channel: str
    delivery_ref: str
    delivered_at: datetime


@dataclass(frozen=True, slots=True)
class RecordSlaEvidence:
    subject_ref: str
    period_start: datetime
    period_end: datetime
    available_seconds: Decimal
    unavailable_seconds: Decimal
    source_ref: str


@dataclass(frozen=True, slots=True)
class IncidentLookup:
    incident_id: UUID | None = None
    code: str | None = None
    include_resolved: bool = False


@dataclass(frozen=True, slots=True)
class ImpactQuery:
    incident_id: UUID | None = None
    subject_ref: str | None = None


@dataclass(frozen=True, slots=True)
class MaintenanceQuery:
    at: datetime
    scope_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SlaEvidenceQuery:
    subject_ref: str
    period_start: datetime
    period_end: datetime


@dataclass(frozen=True, slots=True)
class IncidentSnapshot:
    id: UUID
    tenant_id: UUID
    code: str
    summary: str
    severity: ImpactSeverity
    state: IncidentState
    detection_ref: str
    source_observation_refs: tuple[str, ...]
    detected_at: datetime
    resolved_at: datetime | None
    resolution_code: str | None
    resolution_summary: str | None


@dataclass(frozen=True, slots=True)
class ImpactSnapshot:
    id: UUID
    tenant_id: UUID
    incident_id: UUID
    subject_ref: str
    subject_kind: str
    severity: ImpactSeverity
    topology_path_ref: str | None
    reason_code: str
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class MaintenanceSnapshot:
    id: UUID
    tenant_id: UUID
    code: str
    summary: str
    state: MaintenanceState
    starts_at: datetime
    ends_at: datetime
    scope_refs: tuple[str, ...]
    change_ref: str | None


@dataclass(frozen=True, slots=True)
class SlaEvidenceSnapshot:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    period_start: datetime
    period_end: datetime
    available_seconds: Decimal
    unavailable_seconds: Decimal
    availability_ratio: Decimal
    source_ref: str


@dataclass(frozen=True, slots=True)
class EscalationRecommendation:
    incident_id: UUID
    severity: ImpactSeverity
    recommended_queue: str
    reason_code: str
    evidence_refs: tuple[str, ...]
    evaluated_at: datetime


@dataclass(frozen=True, slots=True)
class IncidentOpened:
    event_id: UUID
    tenant_id: UUID
    incident: IncidentSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class IncidentChanged:
    event_id: UUID
    tenant_id: UUID
    incident: IncidentSnapshot
    previous_state: IncidentState
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class IncidentResolved:
    event_id: UUID
    tenant_id: UUID
    incident: IncidentSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ImpactClassified:
    event_id: UUID
    tenant_id: UUID
    impact: ImpactSnapshot
    occurred_at: datetime


__all__ = [
    "ClassifyImpact",
    "EscalationRecommendation",
    "ImpactClassified",
    "ImpactQuery",
    "ImpactSeverity",
    "ImpactSnapshot",
    "IncidentChanged",
    "IncidentLookup",
    "IncidentOpened",
    "IncidentResolved",
    "IncidentSnapshot",
    "IncidentState",
    "MaintenanceQuery",
    "MaintenanceSnapshot",
    "MaintenanceState",
    "OpenIncident",
    "RecordNotificationEvidence",
    "RecordSlaEvidence",
    "ResolveIncident",
    "ScheduleMaintenance",
    "SlaEvidenceQuery",
    "SlaEvidenceSnapshot",
    "UpdateIncident",
]
