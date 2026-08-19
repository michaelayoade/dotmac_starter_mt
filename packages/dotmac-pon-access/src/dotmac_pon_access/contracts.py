"""Immutable provider-neutral OLT/ONT/PON lifecycle contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class OltState(StrEnum):
    ACTIVE = "active"
    MAINTENANCE = "maintenance"
    RETIRED = "retired"


class OntState(StrEnum):
    DISCOVERED = "discovered"
    ADMITTED = "admitted"
    ASSIGNED = "assigned"
    COMMISSIONED = "commissioned"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class DesiredConfigState(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    DRIFTED = "drifted"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RegisterOlt:
    code: str
    name: str
    management_ref: str
    vendor_family: str
    capability_codes: tuple[str, ...]
    node_ref: str | None = None
    asset_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterPonPort:
    olt_id: UUID
    slot: int
    port: int
    label: str
    capacity: int
    fiber_endpoint_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AdmitOnt:
    serial_number: str
    vendor_family: str
    pon_port_id: UUID
    registration_ref: str
    observed_at: datetime
    asset_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AssignOnt:
    ont_id: UUID
    expected: OntState
    service_subject_ref: str
    assignment_ref: str
    assigned_at: datetime


@dataclass(frozen=True, slots=True)
class CommissionOnt:
    ont_id: UUID
    expected: OntState
    profile_code: str
    desired_config_ref: str
    operation_ref: str
    commissioned_at: datetime


@dataclass(frozen=True, slots=True)
class SetDesiredService:
    ont_id: UUID
    service_ref: str
    profile_code: str
    vlan_ref: str | None
    ip_assignment_ref: str | None
    desired_fingerprint: str
    decision_ref: str


@dataclass(frozen=True, slots=True)
class RecordPonObservation:
    subject_ref: str
    observation_kind: str
    value: str
    unit: str | None
    source_ref: str
    observed_at: datetime
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ReconcilePon:
    ont_id: UUID
    service_ref: str
    observed_fingerprint: str
    evidence_ref: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class RecordBackupEvidence:
    olt_id: UUID
    backup_ref: str
    configuration_fingerprint: str
    captured_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class OltLookup:
    olt_id: UUID | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class PonPortLookup:
    pon_port_id: UUID | None = None
    olt_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class OntLookup:
    ont_id: UUID | None = None
    serial_number: str | None = None
    service_subject_ref: str | None = None


@dataclass(frozen=True, slots=True)
class DesiredServiceQuery:
    ont_id: UUID
    service_ref: str | None = None


@dataclass(frozen=True, slots=True)
class OltSnapshot:
    id: UUID
    tenant_id: UUID
    code: str
    name: str
    management_ref: str
    vendor_family: str
    capability_codes: tuple[str, ...]
    state: OltState
    node_ref: str | None
    asset_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PonPortSnapshot:
    id: UUID
    tenant_id: UUID
    olt_id: UUID
    slot: int
    port: int
    label: str
    capacity: int
    fiber_endpoint_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class OntSnapshot:
    id: UUID
    tenant_id: UUID
    serial_number: str
    vendor_family: str
    pon_port_id: UUID
    state: OntState
    service_subject_ref: str | None
    assignment_ref: str | None
    registration_ref: str
    asset_ref: str | None
    admitted_at: datetime
    commissioned_at: datetime | None


@dataclass(frozen=True, slots=True)
class CommissioningResult:
    ont: OntSnapshot
    profile_code: str
    desired_config_ref: str
    operation_ref: str
    changed: bool


@dataclass(frozen=True, slots=True)
class DesiredServiceSnapshot:
    id: UUID
    tenant_id: UUID
    ont_id: UUID
    service_ref: str
    profile_code: str
    vlan_ref: str | None
    ip_assignment_ref: str | None
    desired_fingerprint: str
    observed_fingerprint: str | None
    state: DesiredConfigState
    decision_ref: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PonDriftReport:
    desired: DesiredServiceSnapshot
    drifted: bool
    evidence_ref: str
    reason_code: str | None
    reconciled_at: datetime


@dataclass(frozen=True, slots=True)
class BackupEvidence:
    id: UUID
    tenant_id: UUID
    olt_id: UUID
    backup_ref: str
    configuration_fingerprint: str
    captured_at: datetime
    source_ref: str


@dataclass(frozen=True, slots=True)
class OntAdmitted:
    event_id: UUID
    tenant_id: UUID
    ont: OntSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OntAssigned:
    event_id: UUID
    tenant_id: UUID
    ont: OntSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class OntCommissioned:
    event_id: UUID
    tenant_id: UUID
    result: CommissioningResult
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PonDriftDetected:
    event_id: UUID
    tenant_id: UUID
    report: PonDriftReport
    occurred_at: datetime


__all__ = [
    "AdmitOnt",
    "AssignOnt",
    "BackupEvidence",
    "CommissionOnt",
    "CommissioningResult",
    "DesiredConfigState",
    "DesiredServiceQuery",
    "DesiredServiceSnapshot",
    "OntAdmitted",
    "OntAssigned",
    "OntCommissioned",
    "OntLookup",
    "OntSnapshot",
    "OntState",
    "OltLookup",
    "OltSnapshot",
    "OltState",
    "PonDriftDetected",
    "PonDriftReport",
    "PonPortLookup",
    "PonPortSnapshot",
    "ReconcilePon",
    "RecordBackupEvidence",
    "RecordPonObservation",
    "RegisterOlt",
    "RegisterPonPort",
    "SetDesiredService",
]
