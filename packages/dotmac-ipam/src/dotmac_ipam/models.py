"""Tenant-only IPAM persistence in the allocated ``mod_ipam`` schema."""

from __future__ import annotations

import uuid
from datetime import datetime

from dotmac_kernel.models import Base, Tenant
from dotmac_kernel.namespaces import module_schema
from sqlalchemy import (
    JSON,
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

SCHEMA = module_schema("ipam")


class AddressSpace(Base):
    __tablename__ = "address_spaces"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ipam_spaces_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_ipam_spaces_tenant_code"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    family: Mapped[str] = mapped_column(String(8), nullable=False)
    prefix: Mapped[str] = mapped_column(String(64), nullable=False)
    routing_domain_ref: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Pool(Base):
    __tablename__ = "pools"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ipam_pools_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_ipam_pools_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "address_space_id"],
            [f"{SCHEMA}.address_spaces.tenant_id", f"{SCHEMA}.address_spaces.id"],
            ondelete="RESTRICT",
            name="fk_ipam_pools_space",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    address_space_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    prefix: Mapped[str] = mapped_column(String(64), nullable=False)
    allocation_prefix_length: Mapped[int] = mapped_column(Integer, nullable=False)
    purpose: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Address(Base):
    __tablename__ = "addresses"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ipam_addresses_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "pool_id", "address", name="uq_ipam_addresses_pool_address"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            [f"{SCHEMA}.pools.tenant_id", f"{SCHEMA}.pools.id"],
            ondelete="RESTRICT",
            name="fk_ipam_addresses_pool",
        ),
        Index("ix_ipam_addresses_available", "tenant_id", "pool_id", "state"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    reservation_purpose: Mapped[str | None] = mapped_column(String(120))
    reservation_ref: Mapped[str | None] = mapped_column(String(200))
    reserved_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Assignment(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ipam_assignments_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "address_id"],
            [f"{SCHEMA}.addresses.tenant_id", f"{SCHEMA}.addresses.id"],
            ondelete="RESTRICT",
            name="fk_ipam_assignments_address",
        ),
        Index(
            "uq_ipam_assignments_active_address",
            "tenant_id",
            "address_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
            sqlite_where=text("state = 'active'"),
        ),
        Index(
            "ix_ipam_assignments_subject",
            "tenant_id",
            "subject_ref",
            "state",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    address_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    assignment_kind: Mapped[str] = mapped_column(String(80), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(240))


class UtilizationSnapshot(Base):
    __tablename__ = "utilization_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ipam_utilization_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "pool_id",
            "source_ref",
            "observed_at",
            name="uq_ipam_utilization_observation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "pool_id"],
            [f"{SCHEMA}.pools.tenant_id", f"{SCHEMA}.pools.id"],
            ondelete="RESTRICT",
            name="fk_ipam_utilization_pool",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    pool_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    available: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False)
    assigned: Mapped[int] = mapped_column(Integer, nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)


class IpamEvent(Base):
    __tablename__ = "ipam_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_ipam_events_tenant_id_id"),
        Index("ix_ipam_events_aggregate", "tenant_id", "aggregate_ref", "occurred_at"),
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


ALL_MODELS = (AddressSpace, Pool, Address, Assignment, UtilizationSnapshot, IpamEvent)
TENANT_TABLES = tuple(model.__tablename__ for model in ALL_MODELS)

__all__ = [
    "ALL_MODELS",
    "Address",
    "AddressSpace",
    "Assignment",
    "IpamEvent",
    "Pool",
    "SCHEMA",
    "TENANT_TABLES",
    "UtilizationSnapshot",
]
