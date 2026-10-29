"""Immutable, product-neutral IPAM commands, queries, snapshots and facts.

Product identity is an opaque UUID. Anything the module must not know (session
occupancy, subscription lifecycle, device lists) arrives as a typed argument
supplied by the composition root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AddressFamily(StrEnum):
    IPV4 = "ipv4"
    IPV6 = "ipv6"


class PoolPurpose(StrEnum):
    CUSTOMER = "customer"
    INFRASTRUCTURE = "infrastructure"
    MANAGEMENT = "management"


class ReservationKind(StrEnum):
    RESERVED = "reserved"
    DEVICE = "device"
    MANAGEMENT = "management"


class PrefixState(StrEnum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    ASSIGNED = "assigned"


# --- caller-supplied facts -------------------------------------------------


@dataclass(frozen=True, slots=True)
class OccupiedAddress:
    """An address the caller knows is in use (live session, served column)."""

    address: str
    holder_ref: str
    source: str


@dataclass(frozen=True, slots=True)
class Cooldown:
    """Reuse cool-down policy. Sub holds the setting; the module applies it."""

    window_seconds: int
    now: datetime


# --- pool / block / reservation commands ------------------------------------


@dataclass(frozen=True, slots=True)
class CreatePool:
    name: str
    cidr: str
    purpose: PoolPurpose
    ip_version: AddressFamily = AddressFamily.IPV4
    site_ref: UUID | None = None
    gateway: str | None = None
    dns_primary: str | None = None
    dns_secondary: str | None = None
    delegation_prefix_length: int | None = None
    allow_network_broadcast: bool = False
    notes: str | None = None
    pool_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class UpdatePool:
    pool_id: UUID
    name: str | None = None
    gateway: str | None = None
    dns_primary: str | None = None
    dns_secondary: str | None = None
    is_active: bool | None = None
    site_ref: UUID | None = None
    purpose: PoolPurpose | None = None
    allow_network_broadcast: bool | None = None
    delegation_prefix_length: int | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class AddBlock:
    pool_id: UUID
    cidr: str
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class RetireBlock:
    block_id: UUID


@dataclass(frozen=True, slots=True)
class ReserveAddress:
    pool_id: UUID
    address: str
    kind: ReservationKind = ReservationKind.RESERVED
    device_ref: UUID | None = None
    notes: str | None = None
    expires_at: datetime | None = None


# --- allocation / assignment commands -------------------------------------


@dataclass(frozen=True, slots=True)
class AllocateAddress:
    """Allocate-or-reactivate one address for an exact service (never ``.first()``
    by subscriber)."""

    pool_ids: tuple[UUID, ...]
    subscription_id: UUID
    subscriber_id: UUID | None
    family: AddressFamily = AddressFamily.IPV4
    occupied: frozenset[OccupiedAddress] = frozenset()
    cooldown: Cooldown | None = None
    mark_primary: bool = True


@dataclass(frozen=True, slots=True)
class AssignAddress:
    """Explicit address (manual override / restore replay)."""

    address: str
    subscription_id: UUID
    subscriber_id: UUID | None
    pool_id: UUID | None = None
    family: AddressFamily = AddressFamily.IPV4
    occupied: frozenset[OccupiedAddress] = frozenset()
    mark_primary: bool = True


@dataclass(frozen=True, slots=True)
class ReleaseAssignment:
    assignment_id: UUID
    reason: str
    expected_active: bool = True
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReleaseForSubject:
    """Release every active service address of one subject (terminal release).

    Management/device addresses are never released here."""

    reason: str
    subscription_id: UUID | None = None
    subscriber_id: UUID | None = None
    now: datetime | None = None


@dataclass(frozen=True, slots=True)
class MoveAssignment:
    """Atomic move: release + assign under expected states, one event pair."""

    subscription_id: UUID
    from_address: str
    to_address: str
    reason: str
    occupied: frozenset[OccupiedAddress] = frozenset()
    now: datetime | None = None
    #: Target pool; defaults to the pool of the from-address.
    to_pool_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class MarkPrimary:
    subscription_id: UUID
    ipv4_address_id: UUID | None


@dataclass(frozen=True, slots=True)
class AllocateManagementAddress:
    pool_id: UUID
    device_ref: UUID
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class SweepExpired:
    now: datetime


# --- repair / ownership (preview -> fingerprint -> apply) ------------------


class RepairDecision(StrEnum):
    READY_CREATE = "ready_create"
    READY_LINK = "ready_link"
    READY_KEEP = "ready_keep"
    READY_RELEASE = "ready_release"
    READY_RELEASE_RETENTION_EXPIRED = "ready_release_retention_expired"
    RETENTION_NOT_EXPIRED = "retention_not_expired"
    ADDRESS_IN_LIVE_USE = "address_in_live_use"
    ADDRESS_NOT_RELEASABLE = "address_not_releasable"
    NOOP = "noop"
    SUBSCRIPTION_NOT_FOUND = "subscription_not_found"
    SUBSCRIBER_NOT_FOUND = "subscriber_not_found"
    SUBSCRIPTION_NOT_SERVICEABLE = "subscription_not_serviceable"
    SUBSCRIPTION_NOT_TERMINAL = "subscription_not_terminal"
    TARGET_ADDRESS_NOT_FOUND = "target_address_not_found"
    TARGET_ADDRESS_NOT_SERVICEABLE = "target_address_not_serviceable"
    TARGET_ADDRESS_IN_ROUTED_BLOCK = "target_address_in_routed_block"
    TARGET_OWNED_BY_OTHER_SERVICE = "target_owned_by_other_service"
    DEACTIVATION_ASSIGNMENT_NOT_FOUND = "deactivation_assignment_not_found"
    DEACTIVATION_NOT_EXACT_SERVICE = "deactivation_not_exact_service"
    DESIRED_ASSIGNMENT_SELECTED_FOR_DEACTIVATION = (
        "desired_assignment_selected_for_deactivation"
    )
    INCOMPLETE_DEACTIVATION_SET = "incomplete_deactivation_set"
    # IPv6 link
    TARGET_ASSIGNMENT_NOT_FOUND = "target_assignment_not_found"
    SERVICE_ALREADY_HAS_IPV6_ASSIGNMENT = "service_already_has_ipv6_assignment"
    # unlink
    ASSIGNMENT_NOT_FOUND = "assignment_not_found"
    ASSIGNMENT_NOT_ACTIVE = "assignment_not_active"
    ASSIGNMENT_NOT_LINKED = "assignment_not_linked"
    SUBSCRIPTION_MISMATCH = "subscription_mismatch"
    SUBSCRIBER_MISMATCH = "subscriber_mismatch"
    READY_UNLINK = "ready_unlink"


_APPLICABLE = frozenset(
    {
        RepairDecision.READY_CREATE,
        RepairDecision.READY_LINK,
        RepairDecision.READY_KEEP,
        RepairDecision.READY_RELEASE,
        RepairDecision.READY_RELEASE_RETENTION_EXPIRED,
        RepairDecision.READY_UNLINK,
    }
)


@dataclass(frozen=True, slots=True)
class RepairFacts:
    """Caller-owned subscription truth. Part of the fingerprint, so a changed
    fact makes a stale preview refuse."""

    subscription_exists: bool = True
    subscriber_exists: bool = True
    serviceable: bool = True
    terminal: bool = False
    #: Caller selected the retention-release path (status in its retention set
    #: and a cutoff supplied).
    retention_release: bool = False
    retention_expired: bool = False
    #: Addresses the caller found in live use by another service/session.
    live_use_addresses: frozenset[str] = frozenset()
    #: Served column / live-session evidence for unlink fingerprints.
    served_address: str | None = None
    live_session_present: bool | None = None
    #: The subscription's owning subscriber (for ownership comparisons).
    subscription_subscriber_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ActiveAssignmentEvidence:
    assignment_id: UUID
    subscriber_id: UUID | None
    subscription_id: UUID | None
    address_id: UUID
    address: str


@dataclass(frozen=True, slots=True)
class RepairPreview:
    subscription_id: UUID
    subscriber_id: UUID | None
    desired_address_id: UUID | None
    desired_address: str | None
    deactivate_assignment_ids: tuple[UUID, ...]
    active_assignments: tuple[ActiveAssignmentEvidence, ...]
    decision: RepairDecision
    fingerprint: str

    @property
    def applicable(self) -> bool:
        return self.decision in _APPLICABLE


@dataclass(frozen=True, slots=True)
class ApplyRepair:
    subscription_id: UUID
    desired_address_id: UUID | None
    deactivate_assignment_ids: tuple[UUID, ...]
    preview_fingerprint: str
    idempotency_key: str
    facts: RepairFacts
    subscriber_id: UUID | None = None
    retention_cutoff: datetime | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class RepairOutcome:
    subscription_id: UUID
    desired_assignment_id: UUID | None
    linked_count: int
    created_count: int
    deactivated_count: int
    preview_fingerprint: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class LinkPreview:
    """IPv6 service link preview (link-only twin of the IPv4 ``ready_link``)."""

    subscription_id: UUID
    subscriber_id: UUID | None
    desired_address_id: UUID
    desired_address: str | None
    active_assignments: tuple[ActiveAssignmentEvidence, ...]
    decision: RepairDecision
    fingerprint: str

    @property
    def applicable(self) -> bool:
        return self.decision is RepairDecision.READY_LINK


@dataclass(frozen=True, slots=True)
class ApplyIPv6Link:
    subscription_id: UUID
    desired_address_id: UUID
    preview_fingerprint: str
    idempotency_key: str
    facts: RepairFacts
    subscriber_id: UUID | None = None
    reason: str = ""


@dataclass(frozen=True, slots=True)
class LinkOutcome:
    subscription_id: UUID
    desired_assignment_id: UUID | None
    linked_count: int
    preview_fingerprint: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class UnlinkPreview:
    assignment_id: UUID
    subscriber_id: UUID | None
    current_subscription_id: UUID | None
    expected_subscription_id: UUID
    ip_version: str | None
    address_id: UUID | None
    address: str | None
    served_address: str | None
    served_address_matches_assignment: bool
    live_session_present: bool | None
    is_primary: bool
    decision: RepairDecision
    fingerprint: str

    @property
    def applicable(self) -> bool:
        return self.decision is RepairDecision.READY_UNLINK


@dataclass(frozen=True, slots=True)
class ApplyUnlink:
    assignment_id: UUID
    expected_subscription_id: UUID
    preview_fingerprint: str
    idempotency_key: str
    facts: RepairFacts
    reason: str = ""


@dataclass(frozen=True, slots=True)
class UnlinkOutcome:
    assignment_id: UUID
    previous_subscription_id: UUID | None
    preview_fingerprint: str
    replayed: bool


# --- prefixes ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllocateDelegatedPrefix:
    pool_id: UUID
    subscriber_id: UUID
    subscription_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ReleaseDelegatedPrefix:
    prefix_id: UUID | None = None
    subscription_id: UUID | None = None
    subscriber_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class UpsertRoutedPrefix:
    subscriber_id: UUID
    cidr: str
    subscription_id: UUID | None = None
    metric: int = 1
    source: str | None = None


@dataclass(frozen=True, slots=True)
class RetireRoutedPrefix:
    subscriber_id: UUID
    cidr: str


@dataclass(frozen=True, slots=True)
class RecordUtilization:
    captured_at: datetime
    pool_ids: tuple[UUID, ...] | None = None


@dataclass(frozen=True, slots=True)
class PruneUtilization:
    cutoff: datetime


# --- snapshots (immutable) ------------------------------------------------


@dataclass(frozen=True, slots=True)
class PoolSnapshot:
    id: UUID
    name: str
    ip_version: str
    cidr: str
    purpose: str
    site_ref: UUID | None
    gateway: str | None
    is_active: bool
    allow_network_broadcast: bool
    delegation_prefix_length: int | None


@dataclass(frozen=True, slots=True)
class BlockSnapshot:
    id: UUID
    pool_id: UUID
    cidr: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class AddressSnapshot:
    id: UUID
    address: str
    family: str
    pool_id: UUID | None
    is_reserved: bool
    allocation_type: str | None
    device_ref: UUID | None
    assigned: bool


@dataclass(frozen=True, slots=True)
class AssignmentSnapshot:
    id: UUID
    subscriber_id: UUID | None
    subscription_id: UUID | None
    ip_version: str
    address_id: UUID
    address: str
    pool_id: UUID | None
    is_primary: bool
    is_active: bool
    prefix_length: int | None
    gateway: str | None
    dns_primary: str | None
    dns_secondary: str | None
    released_at: datetime | None = None
    release_reason: str | None = None


@dataclass(frozen=True, slots=True)
class DelegatedPrefixSnapshot:
    id: UUID
    pool_id: UUID
    prefix: str
    prefix_length: int
    state: str
    subscriber_id: UUID | None
    subscription_id: UUID | None


@dataclass(frozen=True, slots=True)
class RoutedPrefixSnapshot:
    id: UUID
    subscriber_id: UUID
    subscription_id: UUID | None
    cidr: str
    metric: int
    is_active: bool


@dataclass(frozen=True, slots=True)
class UtilizationSnapshot:
    pool_id: UUID
    captured_at: datetime
    total: int
    used: int
    reserved: int
    available: int
    percent: int


@dataclass(frozen=True, slots=True)
class AmbiguousPrimary:
    """``primary_ipv4_for`` could not name one address."""

    subscription_id: UUID
    addresses: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    address: str
    available: bool
    reason: str
    in_managed_block: bool


@dataclass(frozen=True, slots=True)
class AssignmentLookup:
    subscription_ids: tuple[UUID, ...] = ()
    subscriber_ids: tuple[UUID, ...] = ()
    addresses: tuple[str, ...] = ()
    active_only: bool = True


@dataclass(frozen=True, slots=True)
class ConsistencyFinding:
    kind: str
    subject: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ConsistencyReport:
    findings: tuple[ConsistencyFinding, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return not self.findings

    def count(self, kind: str) -> int:
        return sum(1 for item in self.findings if item.kind == kind)


@dataclass(frozen=True, slots=True)
class EventRecord:
    aggregate_ref: str
    event_type: str
    payload: dict[str, object]
    occurred_at: datetime


# --- events (typed payload contracts for ``ipam_events.payload``) ---------
#
# Each event is written to ``ipam_events`` (append-only evidence). The frozen
# dataclasses below ARE the payload schema: ``_common.event`` refuses a payload
# key the schema does not declare, so the vocabulary cannot drift silently.


@dataclass(frozen=True, slots=True)
class AddressAssigned:
    assignment_id: UUID
    address: str
    ip_version: str
    reactivated: bool = False
    moved: bool = False


@dataclass(frozen=True, slots=True)
class AssignmentReleased:
    assignment_id: UUID
    reason: str
    address: str | None = None
    moved: bool = False


@dataclass(frozen=True, slots=True)
class AssignmentMoved:
    from_address: str
    to_address: str
    reason: str


@dataclass(frozen=True, slots=True)
class PrimaryChanged:
    assignment_id: UUID


@dataclass(frozen=True, slots=True)
class AssignmentRepaired:
    preview_fingerprint: str
    decision: str
    reason: str = ""
    subscription_id: UUID | None = None
    assignment_id: UUID | None = None
    desired_assignment_id: UUID | None = None
    desired_address_id: UUID | None = None
    deactivate_assignment_ids: tuple[UUID, ...] = ()
    previous_subscription_id: UUID | None = None
    linked_count: int = 0
    created_count: int = 0
    deactivated_count: int = 0
    unlinked_count: int = 0


@dataclass(frozen=True, slots=True)
class PrefixDelegated:
    prefix: str
    state: str
    subscription_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RoutedPrefixChanged:
    cidr: str
    active: bool


EVENT_ADDRESS_ASSIGNED = "AddressAssigned"
EVENT_ASSIGNMENT_RELEASED = "AssignmentReleased"
EVENT_ASSIGNMENT_MOVED = "AssignmentMoved"
EVENT_PRIMARY_CHANGED = "PrimaryChanged"
EVENT_ASSIGNMENT_REPAIRED = "AssignmentRepaired"
EVENT_PREFIX_DELEGATED = "PrefixDelegated"
EVENT_ROUTED_PREFIX_CHANGED = "RoutedPrefixChanged"

EVENT_SCHEMAS: dict[str, type] = {
    EVENT_ADDRESS_ASSIGNED: AddressAssigned,
    EVENT_ASSIGNMENT_RELEASED: AssignmentReleased,
    EVENT_ASSIGNMENT_MOVED: AssignmentMoved,
    EVENT_PRIMARY_CHANGED: PrimaryChanged,
    EVENT_ASSIGNMENT_REPAIRED: AssignmentRepaired,
    EVENT_PREFIX_DELEGATED: PrefixDelegated,
    EVENT_ROUTED_PREFIX_CHANGED: RoutedPrefixChanged,
}

__all__ = [
    "ActiveAssignmentEvidence",
    "AddBlock",
    "AddressAssigned",
    "AddressFamily",
    "AddressSnapshot",
    "AllocateAddress",
    "AllocateDelegatedPrefix",
    "AllocateManagementAddress",
    "AmbiguousPrimary",
    "ApplyIPv6Link",
    "ApplyRepair",
    "ApplyUnlink",
    "AssignAddress",
    "AssignmentLookup",
    "AssignmentMoved",
    "AssignmentReleased",
    "AssignmentRepaired",
    "AssignmentSnapshot",
    "AvailabilityResult",
    "BlockSnapshot",
    "ConsistencyFinding",
    "ConsistencyReport",
    "Cooldown",
    "CreatePool",
    "DelegatedPrefixSnapshot",
    "EVENT_ADDRESS_ASSIGNED",
    "EVENT_ASSIGNMENT_MOVED",
    "EVENT_ASSIGNMENT_RELEASED",
    "EVENT_ASSIGNMENT_REPAIRED",
    "EVENT_PREFIX_DELEGATED",
    "EVENT_PRIMARY_CHANGED",
    "EVENT_ROUTED_PREFIX_CHANGED",
    "EventRecord",
    "LinkOutcome",
    "LinkPreview",
    "MarkPrimary",
    "MoveAssignment",
    "OccupiedAddress",
    "PoolPurpose",
    "PoolSnapshot",
    "PrefixDelegated",
    "PrefixState",
    "PrimaryChanged",
    "PruneUtilization",
    "RecordUtilization",
    "ReleaseAssignment",
    "ReleaseDelegatedPrefix",
    "ReleaseForSubject",
    "RepairDecision",
    "RepairFacts",
    "RepairOutcome",
    "RepairPreview",
    "ReservationKind",
    "ReserveAddress",
    "RetireBlock",
    "RetireRoutedPrefix",
    "RoutedPrefixChanged",
    "RoutedPrefixSnapshot",
    "SweepExpired",
    "UnlinkOutcome",
    "UnlinkPreview",
    "UpdatePool",
    "UpsertRoutedPrefix",
    "UtilizationSnapshot",
]
