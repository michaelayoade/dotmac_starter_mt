"""Immutable contracts for declared/observed links and rebuildable paths."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class LinkKind(StrEnum):
    PHYSICAL = "physical"
    LOGICAL = "logical"
    WIRELESS = "wireless"
    TUNNEL = "tunnel"
    FORWARDING = "forwarding"


class LinkState(StrEnum):
    DECLARED = "declared"
    OBSERVED = "observed"
    WITHDRAWN = "withdrawn"


class ReachabilityState(StrEnum):
    UNKNOWN = "unknown"
    REACHABLE = "reachable"
    PARTIAL = "partial"
    UNREACHABLE = "unreachable"


@dataclass(frozen=True, slots=True)
class DeclareLink:
    left_ref: str
    right_ref: str
    kind: LinkKind
    source_ref: str
    direction: str = "bidirectional"
    cost: int = 1


@dataclass(frozen=True, slots=True)
class WithdrawLink:
    link_id: UUID
    expected: LinkState
    reason: str
    source_ref: str


@dataclass(frozen=True, slots=True)
class RecordObservedLink:
    left_ref: str
    right_ref: str
    kind: LinkKind
    source_ref: str
    observed_at: datetime
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ResolveForwarding:
    source_ref: str
    destination_ref: str
    declared_link_ids: tuple[UUID, ...]
    observed_link_ids: tuple[UUID, ...]
    as_of: datetime


@dataclass(frozen=True, slots=True)
class RebuildTopology:
    projection_ref: str
    declared_link_ids: tuple[UUID, ...]
    observed_link_ids: tuple[UUID, ...]
    as_of: datetime


@dataclass(frozen=True, slots=True)
class LinkLookup:
    link_id: UUID | None = None
    endpoint_ref: str | None = None
    include_withdrawn: bool = False


@dataclass(frozen=True, slots=True)
class PathQuery:
    source_ref: str
    destination_ref: str
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReachabilityQuery:
    subject_ref: str
    from_ref: str | None = None
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class CoverageQuery:
    scope_ref: str
    as_of: datetime | None = None


@dataclass(frozen=True, slots=True)
class LinkSnapshot:
    id: UUID
    tenant_id: UUID
    left_ref: str
    right_ref: str
    kind: LinkKind
    state: LinkState
    direction: str
    cost: int
    source_ref: str
    observed_at: datetime | None
    created_at: datetime
    withdrawn_at: datetime | None


@dataclass(frozen=True, slots=True)
class PathSnapshot:
    id: UUID
    tenant_id: UUID
    source_ref: str
    destination_ref: str
    hop_refs: tuple[str, ...]
    link_ids: tuple[UUID, ...]
    total_cost: int
    reachable: bool
    as_of: datetime
    rebuilt_at: datetime


@dataclass(frozen=True, slots=True)
class ReachabilitySnapshot:
    tenant_id: UUID
    subject_ref: str
    from_ref: str
    state: ReachabilityState
    path_id: UUID | None
    reason_code: str | None
    as_of: datetime


@dataclass(frozen=True, slots=True)
class CoverageGap:
    tenant_id: UUID
    scope_ref: str
    missing_ref: str
    reason_code: str
    as_of: datetime


@dataclass(frozen=True, slots=True)
class TopologyRebuildReport:
    projection_ref: str
    link_count: int
    path_count: int
    gap_count: int
    changed: bool
    rebuilt_at: datetime


@dataclass(frozen=True, slots=True)
class LinkDeclared:
    event_id: UUID
    tenant_id: UUID
    link: LinkSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class LinkWithdrawn:
    event_id: UUID
    tenant_id: UUID
    link: LinkSnapshot
    reason: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ObservedLinkRecorded:
    event_id: UUID
    tenant_id: UUID
    link: LinkSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class PathChanged:
    event_id: UUID
    tenant_id: UUID
    path: PathSnapshot
    previous_fingerprint: str | None
    occurred_at: datetime


__all__ = [
    "CoverageGap",
    "CoverageQuery",
    "DeclareLink",
    "LinkDeclared",
    "LinkKind",
    "LinkLookup",
    "LinkSnapshot",
    "LinkState",
    "LinkWithdrawn",
    "ObservedLinkRecorded",
    "PathChanged",
    "PathQuery",
    "PathSnapshot",
    "ReachabilityQuery",
    "ReachabilitySnapshot",
    "ReachabilityState",
    "RebuildTopology",
    "RecordObservedLink",
    "ResolveForwarding",
    "TopologyRebuildReport",
    "WithdrawLink",
]
