"""Immutable collector-neutral network observation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class ObservationKind(StrEnum):
    REACHABILITY = "reachability"
    INTERFACE = "interface"
    BANDWIDTH = "bandwidth"
    LATENCY = "latency"
    LOSS = "loss"
    SIGNAL = "signal"
    OTHER = "other"


class AvailabilityState(StrEnum):
    UNKNOWN = "unknown"
    UP = "up"
    DEGRADED = "degraded"
    DOWN = "down"


class AlertState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class RecordObservation:
    subject_ref: str
    kind: ObservationKind
    source_ref: str
    observed_at: datetime
    fingerprint: str
    attributes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RecordMeasurement:
    subject_ref: str
    metric_code: str
    value: Decimal
    unit: str
    source_ref: str
    observed_at: datetime
    fingerprint: str
    dimensions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RecordAvailability:
    subject_ref: str
    state: AvailabilityState
    source_ref: str
    observed_at: datetime
    reason_code: str | None = None


@dataclass(frozen=True, slots=True)
class OpenAlertEvidence:
    subject_ref: str
    rule_ref: str
    severity: str
    evidence_ref: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ResolveAlertEvidence:
    alert_id: UUID
    expected: AlertState
    evidence_ref: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class RebuildHealth:
    subject_ref: str
    as_of: datetime
    source_observation_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class ObservationQuery:
    subject_ref: str
    kind: ObservationKind | None = None
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True, slots=True)
class HealthQuery:
    subject_ref: str
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class AlertQuery:
    subject_ref: str | None = None
    state: AlertState | None = None


@dataclass(frozen=True, slots=True)
class ObservationReceipt:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    kind: ObservationKind
    source_ref: str
    observed_at: datetime
    fingerprint: str
    duplicate: bool


@dataclass(frozen=True, slots=True)
class MeasurementSnapshot:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    metric_code: str
    value: Decimal
    unit: str
    source_ref: str
    observed_at: datetime
    fingerprint: str
    dimensions: tuple[tuple[str, str], ...]
    duplicate: bool


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    state: AvailabilityState
    reason_code: str | None
    as_of: datetime
    source_observation_ids: tuple[UUID, ...]
    rebuilt_at: datetime


@dataclass(frozen=True, slots=True)
class AlertSnapshot:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    rule_ref: str
    severity: str
    state: AlertState
    opened_at: datetime
    resolved_at: datetime | None
    latest_evidence_ref: str


@dataclass(frozen=True, slots=True)
class ObservationRecorded:
    event_id: UUID
    tenant_id: UUID
    receipt: ObservationReceipt
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class HealthChanged:
    event_id: UUID
    tenant_id: UUID
    health: HealthSnapshot
    previous_state: AvailabilityState | None
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AlertEvidenceOpened:
    event_id: UUID
    tenant_id: UUID
    alert: AlertSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AlertEvidenceResolved:
    event_id: UUID
    tenant_id: UUID
    alert: AlertSnapshot
    occurred_at: datetime


__all__ = [
    "AlertEvidenceOpened",
    "AlertEvidenceResolved",
    "AlertQuery",
    "AlertSnapshot",
    "AlertState",
    "AvailabilityState",
    "HealthChanged",
    "HealthQuery",
    "HealthSnapshot",
    "MeasurementSnapshot",
    "ObservationKind",
    "ObservationQuery",
    "ObservationReceipt",
    "ObservationRecorded",
    "OpenAlertEvidence",
    "RebuildHealth",
    "RecordAvailability",
    "RecordMeasurement",
    "RecordObservation",
    "ResolveAlertEvidence",
]
