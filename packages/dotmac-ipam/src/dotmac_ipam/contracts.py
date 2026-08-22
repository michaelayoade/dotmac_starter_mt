"""Immutable, product-neutral IPAM commands, queries, snapshots, and events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from uuid import UUID

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network


class AddressFamily(StrEnum):
    IPV4 = "ipv4"
    IPV6 = "ipv6"


class AddressState(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    ASSIGNED = "assigned"
    QUARANTINED = "quarantined"


class AssignmentState(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"


@dataclass(frozen=True, slots=True)
class CreateAddressSpace:
    code: str
    name: str
    family: AddressFamily
    prefix: IPNetwork
    routing_domain_ref: str | None = None


@dataclass(frozen=True, slots=True)
class CreatePool:
    address_space_id: UUID
    code: str
    name: str
    prefix: IPNetwork
    allocation_prefix_length: int
    purpose: str


@dataclass(frozen=True, slots=True)
class ReserveAddress:
    pool_id: UUID
    address: IPAddress | None
    purpose: str
    reservation_ref: str
    expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class AssignAddress:
    pool_id: UUID
    subject_ref: str
    assignment_kind: str
    source_ref: str
    address: IPAddress | None = None
    reservation_id: UUID | None = None
    reservation_ref: str | None = None
    valid_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReleaseAssignment:
    assignment_id: UUID
    expected: AssignmentState
    reason: str
    source_ref: str


@dataclass(frozen=True, slots=True)
class RecordUtilization:
    pool_id: UUID
    observed_at: datetime
    total: int
    available: int
    reserved: int
    assigned: int
    source_ref: str


@dataclass(frozen=True, slots=True)
class RepairAssignment:
    assignment_id: UUID
    expected_address_id: UUID
    expected_subject_ref: str
    reason: str
    source_ref: str


@dataclass(frozen=True, slots=True)
class AddressLookup:
    address_space_id: UUID
    address: IPAddress


@dataclass(frozen=True, slots=True)
class AssignmentLookup:
    assignment_id: UUID | None = None
    subject_ref: str | None = None
    active_only: bool = True


@dataclass(frozen=True, slots=True)
class UtilizationQuery:
    pool_id: UUID
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class AddressSpaceSnapshot:
    id: UUID
    tenant_id: UUID
    code: str
    name: str
    family: AddressFamily
    prefix: IPNetwork
    routing_domain_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    id: UUID
    tenant_id: UUID
    address_space_id: UUID
    code: str
    name: str
    prefix: IPNetwork
    allocation_prefix_length: int
    purpose: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AddressSnapshot:
    id: UUID
    tenant_id: UUID
    pool_id: UUID
    address: IPAddress
    state: AddressState
    reservation_purpose: str | None
    reservation_ref: str | None
    reserved_until: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class AssignmentSnapshot:
    id: UUID
    tenant_id: UUID
    address_id: UUID
    subject_ref: str
    assignment_kind: str
    source_ref: str
    state: AssignmentState
    assigned_at: datetime
    valid_until: datetime | None
    released_at: datetime | None
    release_reason: str | None


@dataclass(frozen=True, slots=True)
class UtilizationSnapshot:
    id: UUID
    tenant_id: UUID
    pool_id: UUID
    observed_at: datetime
    total: int
    available: int
    reserved: int
    assigned: int
    source_ref: str


@dataclass(frozen=True, slots=True)
class RepairReport:
    assignment: AssignmentSnapshot
    changed: bool
    previous_address_id: UUID | None
    reason: str


@dataclass(frozen=True, slots=True)
class AddressReserved:
    event_id: UUID
    tenant_id: UUID
    address: AddressSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AddressAssigned:
    event_id: UUID
    tenant_id: UUID
    assignment: AssignmentSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AssignmentReleased:
    event_id: UUID
    tenant_id: UUID
    assignment: AssignmentSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class AssignmentRepaired:
    event_id: UUID
    tenant_id: UUID
    report: RepairReport
    occurred_at: datetime


__all__ = [
    "AddressAssigned",
    "AddressFamily",
    "AddressLookup",
    "AddressReserved",
    "AddressSnapshot",
    "AddressSpaceSnapshot",
    "AddressState",
    "AssignAddress",
    "AssignmentLookup",
    "AssignmentReleased",
    "AssignmentRepaired",
    "AssignmentSnapshot",
    "AssignmentState",
    "CreateAddressSpace",
    "CreatePool",
    "IPAddress",
    "IPNetwork",
    "PoolSnapshot",
    "RecordUtilization",
    "ReleaseAssignment",
    "RepairAssignment",
    "RepairReport",
    "ReserveAddress",
    "UtilizationQuery",
    "UtilizationSnapshot",
]
