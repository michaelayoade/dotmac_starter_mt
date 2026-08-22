"""Immutable contracts for outside-plant structures and continuity evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID


class StructureKind(StrEnum):
    CABINET = "cabinet"
    CLOSURE = "closure"
    POLE = "pole"
    DUCT = "duct"
    MANHOLE = "manhole"
    BUILDING_ENTRY = "building_entry"
    OTHER = "other"


class StrandState(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    CONNECTED = "connected"
    DAMAGED = "damaged"
    RETIRED = "retired"


class ChangeState(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class RegisterStructure:
    code: str
    name: str
    kind: StructureKind
    location_ref: str
    asset_ref: str | None = None
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterCable:
    code: str
    name: str
    strand_count: int
    start_structure_id: UUID
    end_structure_id: UUID
    route_ref: str | None = None
    asset_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterStrand:
    cable_id: UUID
    ordinal: int
    colour_code: str
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RecordSplice:
    structure_id: UUID
    left_strand_id: UUID
    right_strand_id: UUID
    loss_db: Decimal | None
    evidence_ref: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RecordTermination:
    structure_id: UUID
    strand_id: UUID
    endpoint_ref: str
    port_ref: str | None
    evidence_ref: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class RecordFieldObservation:
    subject_ref: str
    observation_kind: str
    result_code: str
    evidence_ref: str
    observed_at: datetime
    actor_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ProposeChange:
    code: str
    summary: str
    subject_refs: tuple[str, ...]
    desired_fingerprint: str
    requested_by_ref: str


@dataclass(frozen=True, slots=True)
class ApproveChange:
    change_id: UUID
    expected: ChangeState
    approval_ref: str
    approved_by_ref: str
    approved_at: datetime


@dataclass(frozen=True, slots=True)
class AcceptChange:
    change_id: UUID
    expected: ChangeState
    as_built_fingerprint: str
    evidence_refs: tuple[str, ...]
    accepted_by_ref: str
    accepted_at: datetime


@dataclass(frozen=True, slots=True)
class StructureLookup:
    structure_id: UUID | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class CableLookup:
    cable_id: UUID | None = None
    structure_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ContinuityQuery:
    from_ref: str
    to_ref: str
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class ChangeLookup:
    change_id: UUID | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class StructureSnapshot:
    id: UUID
    tenant_id: UUID
    code: str
    name: str
    kind: StructureKind
    location_ref: str
    asset_ref: str | None
    source_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CableSnapshot:
    id: UUID
    tenant_id: UUID
    code: str
    name: str
    strand_count: int
    start_structure_id: UUID
    end_structure_id: UUID
    route_ref: str | None
    asset_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class StrandSnapshot:
    id: UUID
    tenant_id: UUID
    cable_id: UUID
    ordinal: int
    colour_code: str
    state: StrandState
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ContinuityPath:
    tenant_id: UUID
    from_ref: str
    to_ref: str
    strand_ids: tuple[UUID, ...]
    splice_ids: tuple[UUID, ...]
    continuous: bool
    reason_code: str | None
    as_of: datetime


@dataclass(frozen=True, slots=True)
class FieldObservationSnapshot:
    id: UUID
    tenant_id: UUID
    subject_ref: str
    observation_kind: str
    result_code: str
    evidence_ref: str
    observed_at: datetime
    actor_ref: str | None


@dataclass(frozen=True, slots=True)
class ChangeSnapshot:
    id: UUID
    tenant_id: UUID
    code: str
    summary: str
    subject_refs: tuple[str, ...]
    state: ChangeState
    desired_fingerprint: str
    as_built_fingerprint: str | None
    requested_by_ref: str
    approval_ref: str | None
    accepted_at: datetime | None


@dataclass(frozen=True, slots=True)
class StructureRegistered:
    event_id: UUID
    tenant_id: UUID
    structure: StructureSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SpliceRecorded:
    event_id: UUID
    tenant_id: UUID
    splice_id: UUID
    left_strand_id: UUID
    right_strand_id: UUID
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class FiberChangeAccepted:
    event_id: UUID
    tenant_id: UUID
    change: ChangeSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ContinuityChanged:
    event_id: UUID
    tenant_id: UUID
    path: ContinuityPath
    occurred_at: datetime


__all__ = [
    "AcceptChange",
    "ApproveChange",
    "CableLookup",
    "CableSnapshot",
    "ChangeLookup",
    "ChangeSnapshot",
    "ChangeState",
    "ContinuityChanged",
    "ContinuityPath",
    "ContinuityQuery",
    "FiberChangeAccepted",
    "FieldObservationSnapshot",
    "ProposeChange",
    "RecordFieldObservation",
    "RecordSplice",
    "RecordTermination",
    "RegisterCable",
    "RegisterStrand",
    "RegisterStructure",
    "SpliceRecorded",
    "StrandSnapshot",
    "StrandState",
    "StructureKind",
    "StructureLookup",
    "StructureRegistered",
    "StructureSnapshot",
]
