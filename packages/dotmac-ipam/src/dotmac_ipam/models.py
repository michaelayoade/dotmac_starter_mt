"""Tenant-only IPAM persistence in the allocated ``mod_ipam`` schema.

0.2.0a1 extraction re-cut: table and column names are Sub's own (``ip_pools``,
``ip_assignments`` ...) so Sub readers swap imports only. Product identity
(``subscriber_id``, ``subscription_id``, ``site_ref``, ``device_ref``) is an
opaque UUID with no foreign key. Every table carries ``tenant_id``, a
``(tenant_id, id)`` key, and composite intra-module foreign keys.

Writes are accepted only inside a module command scope; see
:mod:`dotmac_ipam.guard`.
"""

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
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("ipam")

_PRIMARY_WHERE = (
    "is_active AND is_primary AND ipv4_address_id IS NOT NULL "
    "AND subscription_id IS NOT NULL"
)


def _tenant_fk() -> ForeignKey:
    return ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE")


def _created() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IpPool(Base):
    __tablename__ = "ip_pools"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ip_pools_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_ip_pools_tenant_name"),
        CheckConstraint("ip_version IN ('ipv4', 'ipv6')", name="ck_ip_pools_version"),
        CheckConstraint(
            "purpose IN ('customer', 'infrastructure', 'management')",
            name="ck_ip_pools_purpose",
        ),
        CheckConstraint(
            "purpose <> 'customer' OR site_ref IS NOT NULL",
            name="ck_ip_pools_customer_site",
        ),
        Index("ix_ip_pools_site_ref", "tenant_id", "site_ref"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(_tenant_fk(), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    ip_version: Mapped[str] = mapped_column(
        String(8), nullable=False, default="ipv4", server_default="ipv4"
    )
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    gateway: Mapped[str | None] = mapped_column(String(64))
    dns_primary: Mapped[str | None] = mapped_column(String(64))
    dns_secondary: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    delegation_prefix_length: Mapped[int | None] = mapped_column(Integer)
    # Opaque inventory site id. IPAM never validates existence (composition does).
    site_ref: Mapped[uuid.UUID | None] = mapped_column()
    purpose: Mapped[str] = mapped_column(String(20), nullable=False)
    allow_network_broadcast: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class IpBlock(Base):
    __tablename__ = "ip_blocks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ip_blocks_tenant_id_id"),
        UniqueConstraint("tenant_id", "pool_id", "cidr", name="uq_ip_blocks_pool_cidr"),
        ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            [f"{SCHEMA}.ip_pools.tenant_id", f"{SCHEMA}.ip_pools.id"],
            ondelete="RESTRICT",
            name="fk_ip_blocks_pool",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(_tenant_fk(), nullable=False)
    pool_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class IPv4Address(Base):
    __tablename__ = "ipv4_addresses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ipv4_addresses_tenant_id_id"),
        UniqueConstraint("tenant_id", "address", name="uq_ipv4_addresses_address"),
        ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            [f"{SCHEMA}.ip_pools.tenant_id", f"{SCHEMA}.ip_pools.id"],
            ondelete="RESTRICT",
            name="fk_ipv4_addresses_pool",
        ),
        CheckConstraint(
            "allocation_type IS NULL OR allocation_type IN "
            "('management', 'wan', 'static', 'device')",
            name="ck_ipv4_addresses_allocation_type",
        ),
        Index("ix_ipv4_addresses_pool", "tenant_id", "pool_id"),
        Index("ix_ipv4_addresses_device_ref", "tenant_id", "device_ref"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(_tenant_fk(), nullable=False)
    address: Mapped[str] = mapped_column(String(15), nullable=False)
    pool_id: Mapped[uuid.UUID | None] = mapped_column()
    is_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    # Opaque holder (ONT/NAS/router). Was ``ont_unit_id``; the module never
    # names a device type.
    device_ref: Mapped[uuid.UUID | None] = mapped_column()
    allocation_type: Mapped[str | None] = mapped_column(String(20))
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class IPv6Address(Base):
    __tablename__ = "ipv6_addresses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ipv6_addresses_tenant_id_id"),
        UniqueConstraint("tenant_id", "address", name="uq_ipv6_addresses_address"),
        ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            [f"{SCHEMA}.ip_pools.tenant_id", f"{SCHEMA}.ip_pools.id"],
            ondelete="RESTRICT",
            name="fk_ipv6_addresses_pool",
        ),
        Index("ix_ipv6_addresses_pool", "tenant_id", "pool_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(_tenant_fk(), nullable=False)
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    pool_id: Mapped[uuid.UUID | None] = mapped_column()
    is_reserved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class IPAssignment(Base):
    __tablename__ = "ip_assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ip_assignments_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "ipv4_address_id"],
            [f"{SCHEMA}.ipv4_addresses.tenant_id", f"{SCHEMA}.ipv4_addresses.id"],
            ondelete="RESTRICT",
            name="fk_ip_assignments_ipv4",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "ipv6_address_id"],
            [f"{SCHEMA}.ipv6_addresses.tenant_id", f"{SCHEMA}.ipv6_addresses.id"],
            ondelete="RESTRICT",
            name="fk_ip_assignments_ipv6",
        ),
        # One ACTIVE assignment per address; released rows stay for history.
        Index(
            "uq_ip_assignments_ipv4_active",
            "tenant_id",
            "ipv4_address_id",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
        Index(
            "uq_ip_assignments_ipv6_active",
            "tenant_id",
            "ipv6_address_id",
            unique=True,
            postgresql_where=text("is_active"),
            sqlite_where=text("is_active"),
        ),
        # At most one active PRIMARY IPv4 per exact service.
        Index(
            "uq_ip_assignments_primary_ipv4_active",
            "tenant_id",
            "subscription_id",
            unique=True,
            postgresql_where=text(_PRIMARY_WHERE),
            sqlite_where=text(_PRIMARY_WHERE),
        ),
        Index("ix_ip_assignments_subscription", "tenant_id", "subscription_id"),
        Index("ix_ip_assignments_subscriber", "tenant_id", "subscriber_id"),
        CheckConstraint(
            "ip_version IN ('ipv4', 'ipv6')", name="ck_ip_assignments_version"
        ),
        CheckConstraint(
            "(ip_version = 'ipv4' AND ipv4_address_id IS NOT NULL "
            "AND ipv6_address_id IS NULL) OR "
            "(ip_version = 'ipv6' AND ipv6_address_id IS NOT NULL "
            "AND ipv4_address_id IS NULL)",
            name="ck_ip_assignments_version_address",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(_tenant_fk(), nullable=False)
    # Opaque product identities (same column names as Sub; no FK).
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column()
    subscription_id: Mapped[uuid.UUID | None] = mapped_column()
    ip_version: Mapped[str] = mapped_column(
        String(8), nullable=False, default="ipv4", server_default="ipv4"
    )
    ipv4_address_id: Mapped[uuid.UUID | None] = mapped_column()
    ipv6_address_id: Mapped[uuid.UUID | None] = mapped_column()
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    prefix_length: Mapped[int | None] = mapped_column(Integer)
    gateway: Mapped[str | None] = mapped_column(String(64))
    dns_primary: Mapped[str | None] = mapped_column(String(64))
    dns_secondary: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class SubscriberAdditionalRoute(Base):
    __tablename__ = "subscriber_additional_routes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_subscriber_additional_routes_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "subscriber_id",
            "cidr",
            name="uq_subscriber_additional_routes_subscriber_cidr",
        ),
        Index(
            "ix_subscriber_additional_routes_subscriber", "tenant_id", "subscriber_id"
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(_tenant_fk(), nullable=False)
    subscriber_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    subscription_id: Mapped[uuid.UUID | None] = mapped_column()
    cidr: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix_length: Mapped[int] = mapped_column(Integer, nullable=False)
    metric: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Ipv6DelegatedPrefix(Base):
    __tablename__ = "ipv6_delegated_prefixes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_ipv6_delegated_prefixes_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "pool_id",
            "prefix",
            "prefix_length",
            name="uq_ipv6_pd_pool_prefix",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            [f"{SCHEMA}.ip_pools.tenant_id", f"{SCHEMA}.ip_pools.id"],
            ondelete="RESTRICT",
            name="fk_ipv6_pd_pool",
        ),
        CheckConstraint(
            "state IN ('available', 'reserved', 'assigned')", name="ck_ipv6_pd_state"
        ),
        Index("ix_ipv6_pd_state", "tenant_id", "pool_id", "state"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(_tenant_fk(), nullable=False)
    pool_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    prefix: Mapped[str] = mapped_column(String(64), nullable=False)
    prefix_length: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="available", server_default="available"
    )
    subscriber_id: Mapped[uuid.UUID | None] = mapped_column()
    subscription_id: Mapped[uuid.UUID | None] = mapped_column()
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class IpPoolUtilizationSnapshot(Base):
    __tablename__ = "ip_pool_utilization_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_ip_pool_utilization_snapshots_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            [f"{SCHEMA}.ip_pools.tenant_id", f"{SCHEMA}.ip_pools.id"],
            ondelete="RESTRICT",
            name="fk_ip_pool_util_snap_pool",
        ),
        Index("ix_ip_pool_util_snap_pool_time", "tenant_id", "pool_id", "captured_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(_tenant_fk(), nullable=False)
    pool_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IpamEvent(Base):
    """Append-only evidence for every command (kept from 0.1, bytes unchanged)."""

    __tablename__ = "ipam_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ipam_events_tenant_id_id"),
        Index("ix_ipam_events_aggregate", "tenant_id", "aggregate_ref", "occurred_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(_tenant_fk(), nullable=False)
    aggregate_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


ALL_MODELS = (
    IpPool,
    IpBlock,
    IPv4Address,
    IPv6Address,
    IPAssignment,
    SubscriberAdditionalRoute,
    Ipv6DelegatedPrefix,
    IpPoolUtilizationSnapshot,
    IpamEvent,
)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "IPAssignment",
    "IPv4Address",
    "IPv6Address",
    "IpBlock",
    "IpPool",
    "IpPoolUtilizationSnapshot",
    "IpamEvent",
    "Ipv6DelegatedPrefix",
    "SCHEMA",
    "SubscriberAdditionalRoute",
    "TENANT_TABLES",
]
