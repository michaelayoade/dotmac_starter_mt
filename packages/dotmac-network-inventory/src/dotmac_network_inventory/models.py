"""Tenant managed-network inventory persistence in ``mod_netinv``."""

from __future__ import annotations

import uuid
from datetime import datetime

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("netinv")


class Site(Base):
    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netinv_sites_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_netinv_sites_tenant_code"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    site_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    location_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netinv_nodes_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_netinv_nodes_tenant_code"),
        UniqueConstraint(
            "tenant_id",
            "management_identity",
            name="uq_netinv_nodes_management_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "site_id"],
            [f"{SCHEMA}.sites.tenant_id", f"{SCHEMA}.sites.id"],
            name="fk_netinv_nodes_site",
            ondelete="RESTRICT",
        ),
        Index("ix_netinv_nodes_state", "tenant_id", "state"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    site_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    management_identity: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    role_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    capability_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    asset_ref: Mapped[str | None] = mapped_column(String(200))
    source_ref: Mapped[str | None] = mapped_column(String(240))
    admitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Interface(Base):
    __tablename__ = "interfaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netinv_interfaces_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "node_id", "name", name="uq_netinv_interfaces_node_name"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            [f"{SCHEMA}.nodes.tenant_id", f"{SCHEMA}.nodes.id"],
            name="fk_netinv_interfaces_node",
            ondelete="CASCADE",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    interface_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    mac_address: Mapped[str | None] = mapped_column(String(32))
    admin_state: Mapped[str] = mapped_column(String(24), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Port(Base):
    __tablename__ = "ports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netinv_ports_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "node_id", "name", name="uq_netinv_ports_node_name"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            [f"{SCHEMA}.nodes.tenant_id", f"{SCHEMA}.nodes.id"],
            name="fk_netinv_ports_node",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interface_id"],
            [f"{SCHEMA}.interfaces.tenant_id", f"{SCHEMA}.interfaces.id"],
            name="fk_netinv_ports_interface",
            ondelete="RESTRICT",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    interface_id: Mapped[uuid.UUID | None] = mapped_column()
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    port_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Vlan(Base):
    __tablename__ = "vlans"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netinv_vlans_tenant_id_id"),
        Index(
            "uq_netinv_vlans_global",
            "tenant_id",
            "vlan_id",
            unique=True,
            postgresql_where=text("site_ref IS NULL"),
            sqlite_where=text("site_ref IS NULL"),
        ),
        Index(
            "uq_netinv_vlans_site",
            "tenant_id",
            "vlan_id",
            "site_ref",
            unique=True,
            postgresql_where=text("site_ref IS NOT NULL"),
            sqlite_where=text("site_ref IS NOT NULL"),
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    vlan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    purpose: Mapped[str] = mapped_column(String(120), nullable=False)
    site_ref: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VlanAttachment(Base):
    __tablename__ = "vlan_attachments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_netinv_vlan_attachments_tenant_id_id"
        ),
        Index(
            "uq_netinv_vlan_attachment_interface",
            "tenant_id",
            "vlan_id",
            "interface_id",
            unique=True,
            postgresql_where=text("interface_id IS NOT NULL"),
            sqlite_where=text("interface_id IS NOT NULL"),
        ),
        Index(
            "uq_netinv_vlan_attachment_port",
            "tenant_id",
            "vlan_id",
            "port_id",
            unique=True,
            postgresql_where=text("port_id IS NOT NULL"),
            sqlite_where=text("port_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "vlan_id"],
            [f"{SCHEMA}.vlans.tenant_id", f"{SCHEMA}.vlans.id"],
            name="fk_netinv_vlan_attachments_vlan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "interface_id"],
            [f"{SCHEMA}.interfaces.tenant_id", f"{SCHEMA}.interfaces.id"],
            name="fk_netinv_vlan_attachments_interface",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "port_id"],
            [f"{SCHEMA}.ports.tenant_id", f"{SCHEMA}.ports.id"],
            name="fk_netinv_vlan_attachments_port",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "(interface_id IS NOT NULL) <> (port_id IS NOT NULL)",
            name="ck_netinv_vlan_attachment_one_target",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    vlan_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    interface_id: Mapped[uuid.UUID | None] = mapped_column()
    port_id: Mapped[uuid.UUID | None] = mapped_column()
    tagged: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ConfigurationSnapshot(Base):
    __tablename__ = "configuration_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_netinv_config_snapshots_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "node_id",
            "fingerprint",
            name="uq_netinv_config_snapshot_fingerprint",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "node_id"],
            [f"{SCHEMA}.nodes.tenant_id", f"{SCHEMA}.nodes.id"],
            name="fk_netinv_config_snapshots_node",
            ondelete="CASCADE",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class NetworkInventoryEvent(Base):
    __tablename__ = "network_inventory_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_netinv_events_tenant_id_id"),
        Index(
            "ix_netinv_events_aggregate", "tenant_id", "aggregate_ref", "occurred_at"
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    aggregate_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    Site,
    Node,
    Interface,
    Port,
    Vlan,
    VlanAttachment,
    ConfigurationSnapshot,
    NetworkInventoryEvent,
)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "ConfigurationSnapshot",
    "Interface",
    "NetworkInventoryEvent",
    "Node",
    "Port",
    "SCHEMA",
    "Site",
    "TENANT_TABLES",
    "Vlan",
    "VlanAttachment",
]
