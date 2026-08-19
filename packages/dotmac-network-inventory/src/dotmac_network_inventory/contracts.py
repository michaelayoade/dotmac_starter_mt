"""Immutable contracts for managed network identity and admission."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class NodeKind(StrEnum):
    ROUTER = "router"
    SWITCH = "switch"
    SERVER = "server"
    WIRELESS = "wireless"
    SECURITY = "security"
    OTHER = "other"


class NodeState(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ARCHIVED = "archived"


class InterfaceState(StrEnum):
    ENABLED = "enabled"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class RegisterSite:
    code: str
    name: str
    site_kind: str
    location_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AdmitNode:
    site_id: UUID
    code: str
    name: str
    kind: NodeKind
    management_identity: str
    role_codes: tuple[str, ...] = ()
    capability_codes: tuple[str, ...] = ()
    asset_ref: str | None = None
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterInterface:
    node_id: UUID
    name: str
    interface_kind: str
    mac_address: str | None = None
    admin_state: InterfaceState = InterfaceState.ENABLED
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterPort:
    node_id: UUID
    name: str
    port_kind: str
    interface_id: UUID | None = None
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class DefineVlan:
    vlan_id: int
    name: str
    purpose: str
    site_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AttachVlan:
    vlan_id: UUID
    interface_id: UUID | None = None
    port_id: UUID | None = None
    tagged: bool = True
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class RecordConfigurationSnapshot:
    node_id: UUID
    fingerprint: str
    schema_version: str
    source_ref: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ArchiveNode:
    node_id: UUID
    expected: NodeState
    reason: str
    actor_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SiteLookup:
    site_id: UUID | None = None
    code: str | None = None


@dataclass(frozen=True, slots=True)
class NodeLookup:
    node_id: UUID | None = None
    management_identity: str | None = None
    include_archived: bool = False


@dataclass(frozen=True, slots=True)
class InterfaceLookup:
    interface_id: UUID | None = None
    node_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class VlanLookup:
    vlan_id: int | None = None
    site_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SiteSnapshot:
    id: UUID
    tenant_id: UUID
    code: str
    name: str
    site_kind: str
    location_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class NodeSnapshot:
    id: UUID
    tenant_id: UUID
    site_id: UUID
    code: str
    name: str
    kind: NodeKind
    management_identity: str
    state: NodeState
    role_codes: tuple[str, ...]
    capability_codes: tuple[str, ...]
    asset_ref: str | None
    source_ref: str | None
    admitted_at: datetime
    archived_at: datetime | None


@dataclass(frozen=True, slots=True)
class InterfaceSnapshot:
    id: UUID
    tenant_id: UUID
    node_id: UUID
    name: str
    interface_kind: str
    mac_address: str | None
    admin_state: InterfaceState
    source_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class PortSnapshot:
    id: UUID
    tenant_id: UUID
    node_id: UUID
    interface_id: UUID | None
    name: str
    port_kind: str
    source_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VlanSnapshot:
    id: UUID
    tenant_id: UUID
    vlan_id: int
    name: str
    purpose: str
    site_ref: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class AdmissionResult:
    node: NodeSnapshot
    created: bool


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    id: UUID
    tenant_id: UUID
    node_id: UUID
    fingerprint: str
    schema_version: str
    source_ref: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class NodeAdmitted:
    event_id: UUID
    tenant_id: UUID
    node: NodeSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class NodeArchived:
    event_id: UUID
    tenant_id: UUID
    node: NodeSnapshot
    reason: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class InterfaceChanged:
    event_id: UUID
    tenant_id: UUID
    interface: InterfaceSnapshot
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class VlanAttachmentChanged:
    event_id: UUID
    tenant_id: UUID
    vlan_id: UUID
    interface_id: UUID | None
    port_id: UUID | None
    attached: bool
    occurred_at: datetime


__all__ = [
    "AdmissionResult",
    "AdmitNode",
    "ArchiveNode",
    "AttachVlan",
    "ConfigurationSnapshot",
    "DefineVlan",
    "InterfaceChanged",
    "InterfaceLookup",
    "InterfaceSnapshot",
    "InterfaceState",
    "NodeAdmitted",
    "NodeArchived",
    "NodeKind",
    "NodeLookup",
    "NodeSnapshot",
    "NodeState",
    "PortSnapshot",
    "RecordConfigurationSnapshot",
    "RegisterInterface",
    "RegisterPort",
    "RegisterSite",
    "SiteLookup",
    "SiteSnapshot",
    "VlanAttachmentChanged",
    "VlanLookup",
    "VlanSnapshot",
]
