"""Flush-only managed network inventory owner."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_network_inventory.contracts import (
    AdmissionResult,
    AdmitNode,
    ArchiveNode,
    AttachVlan,
    ConfigurationSnapshot,
    DefineVlan,
    InterfaceLookup,
    InterfaceSnapshot,
    InterfaceState,
    NodeKind,
    NodeLookup,
    NodeSnapshot,
    NodeState,
    PortSnapshot,
    RecordConfigurationSnapshot,
    RegisterInterface,
    RegisterPort,
    RegisterSite,
    SiteLookup,
    SiteSnapshot,
    VlanLookup,
    VlanSnapshot,
)
from dotmac_network_inventory.models import (
    ConfigurationSnapshot as ConfigurationRow,
)
from dotmac_network_inventory.models import (
    Interface,
    NetworkInventoryEvent,
    Node,
    Port,
    Site,
    Vlan,
    VlanAttachment,
)


class NetworkInventoryError(ValueError):
    """Base error for a refused network inventory decision."""


class NetworkInventoryNotFound(NetworkInventoryError):
    """A tenant-local managed network identity was not found."""


class NetworkInventoryConflict(NetworkInventoryError):
    """An identity or expected-state decision conflicts."""


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise NetworkInventoryError(f"{label} must not be blank")
    return cleaned


def _event(
    db: Session, tenant_id: UUID, ref: str, kind: str, payload: dict[str, str]
) -> None:
    db.add(
        NetworkInventoryEvent(
            tenant_id=tenant_id,
            aggregate_ref=ref,
            event_type=kind,
            payload=payload,
            occurred_at=datetime.now(UTC),
        )
    )


def _site(db: Session, tenant_id: UUID, site_id: UUID) -> Site:
    row = db.scalar(select(Site).where(Site.tenant_id == tenant_id, Site.id == site_id))
    if row is None:
        raise NetworkInventoryNotFound("site not found")
    return row


def _node(db: Session, tenant_id: UUID, node_id: UUID, *, lock: bool = False) -> Node:
    statement = select(Node).where(Node.tenant_id == tenant_id, Node.id == node_id)
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise NetworkInventoryNotFound("node not found")
    return row


def _site_snapshot(row: Site) -> SiteSnapshot:
    return SiteSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        name=row.name,
        site_kind=row.site_kind,
        location_ref=row.location_ref,
        created_at=row.created_at,
    )


def _node_snapshot(row: Node) -> NodeSnapshot:
    return NodeSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        site_id=row.site_id,
        code=row.code,
        name=row.name,
        kind=NodeKind(row.kind),
        management_identity=row.management_identity,
        state=NodeState(row.state),
        role_codes=tuple(row.role_codes),
        capability_codes=tuple(row.capability_codes),
        asset_ref=row.asset_ref,
        source_ref=row.source_ref,
        admitted_at=row.admitted_at,
        archived_at=row.archived_at,
    )


def _interface_snapshot(row: Interface) -> InterfaceSnapshot:
    return InterfaceSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        node_id=row.node_id,
        name=row.name,
        interface_kind=row.interface_kind,
        mac_address=row.mac_address,
        admin_state=InterfaceState(row.admin_state),
        source_ref=row.source_ref,
        created_at=row.created_at,
    )


def _port_snapshot(row: Port) -> PortSnapshot:
    return PortSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        node_id=row.node_id,
        interface_id=row.interface_id,
        name=row.name,
        port_kind=row.port_kind,
        source_ref=row.source_ref,
        created_at=row.created_at,
    )


def _vlan_snapshot(row: Vlan) -> VlanSnapshot:
    return VlanSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        vlan_id=row.vlan_id,
        name=row.name,
        purpose=row.purpose,
        site_ref=row.site_ref,
        created_at=row.created_at,
    )


def register_site(
    db: Session, *, tenant_id: UUID, command: RegisterSite
) -> SiteSnapshot:
    row = Site(
        tenant_id=tenant_id,
        code=_clean(command.code, "site code"),
        name=_clean(command.name, "site name"),
        site_kind=_clean(command.site_kind, "site kind"),
        location_ref=_clean(command.location_ref, "location reference")
        if command.location_ref
        else None,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db, tenant_id, f"site:{row.id}", "site_registered", {"code": row.code}
            )
            db.flush()
    except IntegrityError as exc:
        raise NetworkInventoryConflict("site code already exists") from exc
    return _site_snapshot(row)


def admit_node(db: Session, *, tenant_id: UUID, command: AdmitNode) -> AdmissionResult:
    _site(db, tenant_id, command.site_id)
    existing = db.scalar(
        select(Node).where(
            Node.tenant_id == tenant_id,
            Node.management_identity == command.management_identity,
        )
    )
    if existing is not None:
        same = existing.code == command.code and existing.site_id == command.site_id
        if not same:
            raise NetworkInventoryConflict(
                "management identity belongs to another node"
            )
        return AdmissionResult(node=_node_snapshot(existing), created=False)
    now = datetime.now(UTC)
    row = Node(
        tenant_id=tenant_id,
        site_id=command.site_id,
        code=_clean(command.code, "node code"),
        name=_clean(command.name, "node name"),
        kind=command.kind.value,
        management_identity=_clean(command.management_identity, "management identity"),
        state=NodeState.ACTIVE.value,
        role_codes=sorted(set(command.role_codes)),
        capability_codes=sorted(set(command.capability_codes)),
        asset_ref=_clean(command.asset_ref, "asset reference")
        if command.asset_ref
        else None,
        source_ref=_clean(command.source_ref, "source reference")
        if command.source_ref
        else None,
        admitted_at=now,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id,
                f"node:{row.id}",
                "node_admitted",
                {"management_identity": row.management_identity},
            )
            db.flush()
    except IntegrityError as exc:
        raise NetworkInventoryConflict(
            "node code or management identity already exists"
        ) from exc
    return AdmissionResult(node=_node_snapshot(row), created=True)


def register_interface(
    db: Session, *, tenant_id: UUID, command: RegisterInterface
) -> InterfaceSnapshot:
    node = _node(db, tenant_id, command.node_id)
    if NodeState(node.state) is NodeState.ARCHIVED:
        raise NetworkInventoryConflict("archived node cannot gain interfaces")
    row = Interface(
        tenant_id=tenant_id,
        node_id=node.id,
        name=_clean(command.name, "interface name"),
        interface_kind=_clean(command.interface_kind, "interface kind"),
        mac_address=_clean(command.mac_address, "MAC address")
        if command.mac_address
        else None,
        admin_state=command.admin_state.value,
        source_ref=_clean(command.source_ref, "source reference")
        if command.source_ref
        else None,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id,
                f"interface:{row.id}",
                "interface_registered",
                {"node_id": str(node.id)},
            )
            db.flush()
    except IntegrityError as exc:
        raise NetworkInventoryConflict("interface name already exists on node") from exc
    return _interface_snapshot(row)


def register_port(
    db: Session, *, tenant_id: UUID, command: RegisterPort
) -> PortSnapshot:
    node = _node(db, tenant_id, command.node_id)
    if NodeState(node.state) is NodeState.ARCHIVED:
        raise NetworkInventoryConflict("archived node cannot gain ports")
    if command.interface_id is not None:
        interface = db.scalar(
            select(Interface).where(
                Interface.tenant_id == tenant_id, Interface.id == command.interface_id
            )
        )
        if interface is None or interface.node_id != node.id:
            raise NetworkInventoryConflict("interface is not on the selected node")
    row = Port(
        tenant_id=tenant_id,
        node_id=node.id,
        interface_id=command.interface_id,
        name=_clean(command.name, "port name"),
        port_kind=_clean(command.port_kind, "port kind"),
        source_ref=_clean(command.source_ref, "source reference")
        if command.source_ref
        else None,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise NetworkInventoryConflict("port name already exists on node") from exc
    return _port_snapshot(row)


def define_vlan(db: Session, *, tenant_id: UUID, command: DefineVlan) -> VlanSnapshot:
    if not 1 <= command.vlan_id <= 4094:
        raise NetworkInventoryError("VLAN id must be between 1 and 4094")
    row = Vlan(
        tenant_id=tenant_id,
        vlan_id=command.vlan_id,
        name=_clean(command.name, "VLAN name"),
        purpose=_clean(command.purpose, "VLAN purpose"),
        site_ref=_clean(command.site_ref, "site reference")
        if command.site_ref
        else None,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise NetworkInventoryConflict("VLAN identity already exists") from exc
    return _vlan_snapshot(row)


def attach_vlan(db: Session, *, tenant_id: UUID, command: AttachVlan) -> UUID:
    if (command.interface_id is None) == (command.port_id is None):
        raise NetworkInventoryError("exactly one interface or port target is required")
    vlan = db.scalar(
        select(Vlan).where(Vlan.tenant_id == tenant_id, Vlan.id == command.vlan_id)
    )
    if vlan is None:
        raise NetworkInventoryNotFound("VLAN not found")
    if (
        command.interface_id is not None
        and db.scalar(
            select(Interface.id).where(
                Interface.tenant_id == tenant_id, Interface.id == command.interface_id
            )
        )
        is None
    ):
        raise NetworkInventoryNotFound("interface not found")
    if (
        command.port_id is not None
        and db.scalar(
            select(Port.id).where(
                Port.tenant_id == tenant_id, Port.id == command.port_id
            )
        )
        is None
    ):
        raise NetworkInventoryNotFound("port not found")
    row = VlanAttachment(
        tenant_id=tenant_id,
        vlan_id=vlan.id,
        interface_id=command.interface_id,
        port_id=command.port_id,
        tagged=command.tagged,
        source_ref=command.source_ref,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id,
                f"vlan-attachment:{row.id}",
                "vlan_attachment_changed",
                {
                    "vlan_id": str(row.vlan_id),
                    "target_id": str(row.interface_id or row.port_id),
                },
            )
            db.flush()
    except IntegrityError as exc:
        raise NetworkInventoryConflict("VLAN is already attached to target") from exc
    return row.id


def record_configuration_snapshot(
    db: Session, *, tenant_id: UUID, command: RecordConfigurationSnapshot
) -> ConfigurationSnapshot:
    _node(db, tenant_id, command.node_id)
    existing = db.scalar(
        select(ConfigurationRow).where(
            ConfigurationRow.tenant_id == tenant_id,
            ConfigurationRow.node_id == command.node_id,
            ConfigurationRow.fingerprint == command.fingerprint,
        )
    )
    if existing is not None:
        row = existing
    else:
        row = ConfigurationRow(
            tenant_id=tenant_id,
            node_id=command.node_id,
            fingerprint=_clean(command.fingerprint, "configuration fingerprint"),
            schema_version=_clean(command.schema_version, "schema version"),
            source_ref=_clean(command.source_ref, "source reference"),
            observed_at=command.observed_at,
        )
        from dotmac_kernel.db import conflict_savepoint

        try:
            with conflict_savepoint(db):
                db.add(row)
                db.flush()
        except IntegrityError as exc:
            raise NetworkInventoryConflict(
                "configuration fingerprint already exists"
            ) from exc
    return ConfigurationSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        node_id=row.node_id,
        fingerprint=row.fingerprint,
        schema_version=row.schema_version,
        source_ref=row.source_ref,
        observed_at=row.observed_at,
    )


def archive_node(db: Session, *, tenant_id: UUID, command: ArchiveNode) -> NodeSnapshot:
    row = _node(db, tenant_id, command.node_id, lock=True)
    current = NodeState(row.state)
    if current is not command.expected:
        raise NetworkInventoryConflict("node state changed")
    if current is NodeState.ARCHIVED:
        return _node_snapshot(row)
    row.state = NodeState.ARCHIVED.value
    row.archived_at = datetime.now(UTC)
    _event(
        db,
        tenant_id,
        f"node:{row.id}",
        "node_archived",
        {"reason": _clean(command.reason, "archive reason")},
    )
    db.flush()
    return _node_snapshot(row)


def lookup_sites(
    db: Session, *, tenant_id: UUID, query: SiteLookup
) -> tuple[SiteSnapshot, ...]:
    statement = select(Site).where(Site.tenant_id == tenant_id)
    if query.site_id is not None:
        statement = statement.where(Site.id == query.site_id)
    if query.code is not None:
        statement = statement.where(Site.code == query.code)
    return tuple(_site_snapshot(row) for row in db.scalars(statement))


def lookup_nodes(
    db: Session, *, tenant_id: UUID, query: NodeLookup
) -> tuple[NodeSnapshot, ...]:
    statement = select(Node).where(Node.tenant_id == tenant_id)
    if query.node_id is not None:
        statement = statement.where(Node.id == query.node_id)
    if query.management_identity is not None:
        statement = statement.where(
            Node.management_identity == query.management_identity
        )
    if not query.include_archived:
        statement = statement.where(Node.state != NodeState.ARCHIVED.value)
    return tuple(_node_snapshot(row) for row in db.scalars(statement))


def lookup_interfaces(
    db: Session, *, tenant_id: UUID, query: InterfaceLookup
) -> tuple[InterfaceSnapshot, ...]:
    statement = select(Interface).where(Interface.tenant_id == tenant_id)
    if query.interface_id is not None:
        statement = statement.where(Interface.id == query.interface_id)
    if query.node_id is not None:
        statement = statement.where(Interface.node_id == query.node_id)
    return tuple(_interface_snapshot(row) for row in db.scalars(statement))


def lookup_vlans(
    db: Session, *, tenant_id: UUID, query: VlanLookup
) -> tuple[VlanSnapshot, ...]:
    statement = select(Vlan).where(Vlan.tenant_id == tenant_id)
    if query.vlan_id is not None:
        statement = statement.where(Vlan.vlan_id == query.vlan_id)
    if query.site_ref is not None:
        statement = statement.where(Vlan.site_ref == query.site_ref)
    return tuple(_vlan_snapshot(row) for row in db.scalars(statement))


__all__ = [
    "NetworkInventoryConflict",
    "NetworkInventoryError",
    "NetworkInventoryNotFound",
    "admit_node",
    "archive_node",
    "attach_vlan",
    "define_vlan",
    "lookup_interfaces",
    "lookup_nodes",
    "lookup_sites",
    "lookup_vlans",
    "record_configuration_snapshot",
    "register_interface",
    "register_port",
    "register_site",
]
