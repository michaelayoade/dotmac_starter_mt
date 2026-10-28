"""Pool, block, reservation, expiry sweep and utilization commands."""

from __future__ import annotations

import ipaddress
from typing import Any, cast
from uuid import UUID

from dotmac_kernel.transactions import conflict_savepoint
from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_ipam import _common as c
from dotmac_ipam.contracts import (
    AddBlock,
    AddressSnapshot,
    BlockSnapshot,
    CreatePool,
    PoolPurpose,
    PoolSnapshot,
    PruneUtilization,
    RecordUtilization,
    ReservationKind,
    ReserveAddress,
    RetireBlock,
    SweepExpired,
    UpdatePool,
    UtilizationSnapshot,
)
from dotmac_ipam.errors import IpamConflict, IpamError, IpamNotFound
from dotmac_ipam.guard import command
from dotmac_ipam.models import (
    IPAssignment,
    IpBlock,
    IpPool,
    IpPoolUtilizationSnapshot,
    IPv4Address,
    IPv6Address,
)

#: Integer column ceiling; IPv6 pools have astronomically large capacity.
TOTAL_CAP = 2_000_000_000


def _network(
    cidr: str, family: str, label: str
) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    try:
        network = ipaddress.ip_network(cidr.strip(), strict=False)
    except ValueError as exc:
        raise IpamError(f"{label} is not a valid CIDR") from exc
    if f"ipv{network.version}" != family:
        raise IpamError(f"{label} does not match the pool address family")
    return network


def _validate_site(purpose: str, site_ref: object) -> None:
    if purpose == PoolPurpose.CUSTOMER.value and site_ref is None:
        raise IpamError(
            "a customer pool requires site_ref (one site per customer pool)",
            code="ipam.site_required",
        )


def _active_assignment_count(db: Session, tenant_id: UUID, pool_id: UUID) -> int:
    v4 = select(IPv4Address.id).where(
        IPv4Address.tenant_id == tenant_id, IPv4Address.pool_id == pool_id
    )
    v6 = select(IPv6Address.id).where(
        IPv6Address.tenant_id == tenant_id, IPv6Address.pool_id == pool_id
    )
    return (
        db.scalar(
            select(func.count())
            .select_from(IPAssignment)
            .where(
                IPAssignment.tenant_id == tenant_id,
                IPAssignment.is_active.is_(True),
                IPAssignment.ipv4_address_id.in_(v4)
                | IPAssignment.ipv6_address_id.in_(v6),
            )
        )
        or 0
    )


@command
def create_pool(db: Session, *, tenant_id: UUID, command: CreatePool) -> PoolSnapshot:
    family = command.ip_version.value
    network = _network(command.cidr, family, "pool cidr")
    _validate_site(command.purpose.value, command.site_ref)
    if command.delegation_prefix_length is not None and not (
        network.prefixlen <= command.delegation_prefix_length <= network.max_prefixlen
    ):
        raise IpamError("delegation prefix length is outside the pool prefix")
    for other_cidr in db.scalars(
        select(IpPool.cidr).where(
            IpPool.tenant_id == tenant_id,
            IpPool.is_active.is_(True),
            IpPool.ip_version == family,
        )
    ):
        other = ipaddress.ip_network(other_cidr, strict=False)
        if network.overlaps(other):
            raise IpamConflict(
                "pool prefix overlaps an existing active pool",
                code="ipam.pool_overlap",
            )
    row = IpPool(
        tenant_id=tenant_id,
        name=c.clean(command.name, "pool name"),
        ip_version=family,
        cidr=str(network),
        gateway=command.gateway,
        dns_primary=command.dns_primary,
        dns_secondary=command.dns_secondary,
        is_active=True,
        notes=command.notes,
        delegation_prefix_length=command.delegation_prefix_length,
        site_ref=command.site_ref,
        purpose=command.purpose.value,
        allow_network_broadcast=command.allow_network_broadcast,
    )
    if command.pool_id is not None:
        row.id = command.pool_id
    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            c.event(
                db,
                tenant_id=tenant_id,
                aggregate_ref=f"pool:{row.id}",
                event_type="pool_created",
                payload={"cidr": row.cidr, "purpose": row.purpose},
            )
            db.flush()
    except IntegrityError as exc:
        raise IpamConflict("pool name already exists") from exc
    return c.pool_snapshot(row)


@command
def update_pool(db: Session, *, tenant_id: UUID, command: UpdatePool) -> PoolSnapshot:
    row = c.get_pool(db, tenant_id, command.pool_id, lock=True)
    purpose = command.purpose.value if command.purpose is not None else row.purpose
    site_ref = command.site_ref if command.site_ref is not None else row.site_ref
    if command.site_ref is not None and command.site_ref != row.site_ref:
        if _active_assignment_count(db, tenant_id, row.id):
            raise IpamConflict(
                "site_ref is immutable while the pool has active assignments; "
                "create a new pool and move the assignments",
                code="ipam.site_immutable",
            )
    _validate_site(purpose, site_ref)
    try:
        with conflict_savepoint(db):
            for attr in (
                "name",
                "gateway",
                "dns_primary",
                "dns_secondary",
                "is_active",
                "allow_network_broadcast",
                "delegation_prefix_length",
                "notes",
            ):
                value = getattr(command, attr)
                if value is not None:
                    setattr(row, attr, value)
            row.purpose = purpose
            row.site_ref = site_ref
            db.flush()
            c.event(
                db,
                tenant_id=tenant_id,
                aggregate_ref=f"pool:{row.id}",
                event_type="pool_updated",
                payload={"purpose": row.purpose},
            )
            db.flush()
    except IntegrityError as exc:
        raise IpamConflict("pool name already exists") from exc
    return c.pool_snapshot(row)


@command
def add_block(db: Session, *, tenant_id: UUID, command: AddBlock) -> BlockSnapshot:
    pool = c.get_pool(db, tenant_id, command.pool_id, lock=True)
    network = _network(command.cidr, pool.ip_version, "block cidr")
    rows = db.execute(
        select(IpBlock.cidr, IpBlock.pool_id).where(
            IpBlock.tenant_id == tenant_id, IpBlock.is_active.is_(True)
        )
    ).all()
    for other_cidr, _other_pool in rows:
        other = ipaddress.ip_network(other_cidr, strict=False)
        if other.version == network.version and network.overlaps(other):
            raise IpamConflict(
                "block overlaps an existing active block", code="ipam.block_overlap"
            )
    block = IpBlock(
        tenant_id=tenant_id,
        pool_id=pool.id,
        cidr=str(network),
        is_active=True,
        notes=command.notes,
    )
    try:
        with conflict_savepoint(db):
            db.add(block)
            db.flush()
            c.event(
                db,
                tenant_id=tenant_id,
                aggregate_ref=f"pool:{pool.id}",
                event_type="block_added",
                payload={"cidr": block.cidr},
            )
            db.flush()
    except IntegrityError as exc:
        raise IpamConflict("block already exists in this pool") from exc
    return c.block_snapshot(block)


@command
def retire_block(
    db: Session, *, tenant_id: UUID, command: RetireBlock
) -> BlockSnapshot:
    block = db.scalar(
        select(IpBlock)
        .where(IpBlock.tenant_id == tenant_id, IpBlock.id == command.block_id)
        .with_for_update()
    )
    if block is None:
        raise IpamNotFound("block not found", code="ipam.block_not_found")
    network = ipaddress.ip_network(block.cidr, strict=False)
    held = db.scalars(
        select(IPv4Address.address)
        .join(
            IPAssignment,
            (IPAssignment.tenant_id == IPv4Address.tenant_id)
            & (IPAssignment.ipv4_address_id == IPv4Address.id),
        )
        .where(
            IPv4Address.tenant_id == tenant_id,
            IPv4Address.pool_id == block.pool_id,
            IPAssignment.is_active.is_(True),
        )
    ).all()
    for value in held:
        if network.version == 4 and ipaddress.IPv4Address(value) in network:
            raise IpamConflict(
                "block still contains an actively assigned address",
                code="ipam.block_in_use",
            )
    block.is_active = False
    db.flush()
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"pool:{block.pool_id}",
        event_type="block_retired",
        payload={"cidr": block.cidr},
    )
    db.flush()
    return c.block_snapshot(block)


@command
def reserve_address(
    db: Session, *, tenant_id: UUID, command: ReserveAddress
) -> AddressSnapshot:
    """Reserve (or device-/management-hold) an IPv4 address inside a pool.

    ``kind='device'`` replaces the Sub device-host table query: a device
    management IP becomes DATA the allocator never hands out."""
    pool = c.get_pool(db, tenant_id, command.pool_id, lock=True)
    if pool.ip_version != c.IPV4:
        raise IpamError("reservations are IPv4 only in this release")
    value = str(c.parse_v4(command.address))
    if not c.pool_contains(db, tenant_id, pool, value):
        raise IpamError("address is outside the pool", code="ipam.address_outside_pool")
    if command.kind is ReservationKind.DEVICE and command.device_ref is None:
        raise IpamError("a device reservation requires device_ref")
    row = db.scalar(
        select(IPv4Address)
        .where(IPv4Address.tenant_id == tenant_id, IPv4Address.address == value)
        .with_for_update()
    )
    if row is None:
        row = IPv4Address(tenant_id=tenant_id, address=value, pool_id=pool.id)
        db.add(row)
    else:
        if row.pool_id not in (None, pool.id):
            raise IpamConflict("address belongs to another pool")
        if db.scalar(
            select(IPAssignment.id).where(
                IPAssignment.tenant_id == tenant_id,
                IPAssignment.ipv4_address_id == row.id,
                IPAssignment.is_active.is_(True),
            )
        ):
            raise IpamConflict(
                "address is actively assigned", code="ipam.address_assigned"
            )
    row.pool_id = pool.id
    row.is_reserved = True
    row.notes = command.notes
    row.reserved_until = command.expires_at
    row.device_ref = command.device_ref
    row.allocation_type = {
        ReservationKind.DEVICE: "device",
        ReservationKind.MANAGEMENT: "management",
        ReservationKind.RESERVED: None,
    }[command.kind]
    db.flush()
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"address:{row.id}",
        event_type="address_reserved",
        payload={"address": value, "kind": command.kind.value},
    )
    db.flush()
    return c.address_snapshot(row, assigned=False)


@command
def sweep_expired(db: Session, *, tenant_id: UUID, command: SweepExpired) -> int:
    """Expire timed reservations (``reserved_until``). Cool-down needs no sweep
    (it is evaluated from ``released_at``). *When* a disabled/hidden service
    loses its IP stays product policy, which calls ``release_assignment``."""
    cutoff = c.aware(command.now)
    expired = 0
    reserved: list[IPv4Address | IPv6Address] = [
        *db.scalars(
            select(IPv4Address)
            .where(
                IPv4Address.tenant_id == tenant_id,
                IPv4Address.is_reserved.is_(True),
                IPv4Address.reserved_until.is_not(None),
            )
            .with_for_update()
        ),
        *db.scalars(
            select(IPv6Address)
            .where(
                IPv6Address.tenant_id == tenant_id,
                IPv6Address.is_reserved.is_(True),
                IPv6Address.reserved_until.is_not(None),
            )
            .with_for_update()
        ),
    ]
    for row in reserved:
        if row.reserved_until is None or c.aware(row.reserved_until) > cutoff:
            continue
        row.is_reserved = False
        row.reserved_until = None
        if isinstance(row, IPv4Address):
            row.device_ref = None
            row.allocation_type = None
        c.event(
            db,
            tenant_id=tenant_id,
            aggregate_ref=f"address:{row.id}",
            event_type="reservation_expired",
            payload={"address": row.address},
            occurred_at=command.now,
        )
        expired += 1
    db.flush()
    return expired


# --- utilization ----------------------------------------------------------


def capacity_from_cidr(cidr: str) -> int:
    try:
        network = ipaddress.ip_network(str(cidr or ""), strict=False)
    except ValueError:
        return 0
    total = network.num_addresses
    if network.version == 4 and network.prefixlen < 31:
        total = max(total - 2, 0)
    return min(total, TOTAL_CAP)


def _pool_counts(db: Session, tenant_id: UUID, pool: IpPool) -> tuple[int, int]:
    model = IPv6Address if pool.ip_version == c.IPV6 else IPv4Address
    fk = (
        IPAssignment.ipv6_address_id
        if pool.ip_version == c.IPV6
        else IPAssignment.ipv4_address_id
    )
    used = (
        db.scalar(
            select(func.count(model.id))
            .select_from(model)
            .join(
                IPAssignment,
                (IPAssignment.tenant_id == model.tenant_id) & (fk == model.id),
            )
            .where(
                model.tenant_id == tenant_id,
                model.pool_id == pool.id,
                IPAssignment.is_active.is_(True),
            )
        )
        or 0
    )
    reserved = (
        db.scalar(
            select(func.count(model.id)).where(
                model.tenant_id == tenant_id,
                model.pool_id == pool.id,
                model.is_reserved.is_(True),
            )
        )
        or 0
    )
    return used, reserved


@command
def record_utilization(
    db: Session, *, tenant_id: UUID, command: RecordUtilization
) -> tuple[UtilizationSnapshot, ...]:
    statement = select(IpPool).where(
        IpPool.tenant_id == tenant_id, IpPool.is_active.is_(True)
    )
    if command.pool_ids is not None:
        statement = statement.where(IpPool.id.in_(command.pool_ids))
    out: list[UtilizationSnapshot] = []
    for pool in db.scalars(statement.order_by(IpPool.name)):
        used, reserved = _pool_counts(db, tenant_id, pool)
        total = capacity_from_cidr(pool.cidr)
        available = max(total - used - reserved, 0)
        percent = round(used / total * 100) if total else 0
        db.add(
            IpPoolUtilizationSnapshot(
                tenant_id=tenant_id,
                pool_id=pool.id,
                captured_at=command.captured_at,
                total=total,
                used=used,
                reserved=reserved,
                available=available,
                percent=percent,
            )
        )
        out.append(
            UtilizationSnapshot(
                pool_id=pool.id,
                captured_at=command.captured_at,
                total=total,
                used=used,
                reserved=reserved,
                available=available,
                percent=percent,
            )
        )
    db.flush()
    return tuple(out)


@command
def prune_utilization(
    db: Session, *, tenant_id: UUID, command: PruneUtilization
) -> int:
    """Delete snapshots older than ``cutoff`` (Sub retention is 400 days)."""
    result = db.execute(
        delete(IpPoolUtilizationSnapshot)
        .where(
            IpPoolUtilizationSnapshot.tenant_id == tenant_id,
            IpPoolUtilizationSnapshot.captured_at < command.cutoff,
        )
        .execution_options(synchronize_session=False)
    )
    return int(cast(CursorResult[Any], result).rowcount or 0)


def utilization(
    db: Session, *, tenant_id: UUID, pool_id: UUID, limit: int = 365
) -> tuple[UtilizationSnapshot, ...]:
    """Most recent snapshots for a pool, oldest first (for charting)."""
    rows = db.scalars(
        select(IpPoolUtilizationSnapshot)
        .where(
            IpPoolUtilizationSnapshot.tenant_id == tenant_id,
            IpPoolUtilizationSnapshot.pool_id == pool_id,
        )
        .order_by(IpPoolUtilizationSnapshot.captured_at.desc())
        .limit(limit)
    ).all()
    return tuple(
        UtilizationSnapshot(
            pool_id=row.pool_id,
            captured_at=row.captured_at,
            total=row.total,
            used=row.used,
            reserved=row.reserved,
            available=row.available,
            percent=row.percent,
        )
        for row in reversed(rows)
    )


def live_pool_counts(db: Session, *, tenant_id: UUID, pool_id: UUID) -> tuple[int, int]:
    """Live ``(used, total)`` as counted address rows (admin report grain)."""
    pool = c.get_pool(db, tenant_id, pool_id)
    model = IPv6Address if pool.ip_version == c.IPV6 else IPv4Address
    fk = (
        IPAssignment.ipv6_address_id
        if pool.ip_version == c.IPV6
        else IPAssignment.ipv4_address_id
    )
    total = (
        db.scalar(
            select(func.count(model.id)).where(
                model.tenant_id == tenant_id, model.pool_id == pool.id
            )
        )
        or 0
    )
    used = (
        db.scalar(
            select(func.count(IPAssignment.id))
            .join(model, (IPAssignment.tenant_id == model.tenant_id) & (fk == model.id))
            .where(
                IPAssignment.tenant_id == tenant_id,
                model.pool_id == pool.id,
                IPAssignment.is_active.is_(True),
            )
        )
        or 0
    )
    return used, total


__all__ = [
    "add_block",
    "capacity_from_cidr",
    "create_pool",
    "live_pool_counts",
    "prune_utilization",
    "record_utilization",
    "reserve_address",
    "retire_block",
    "sweep_expired",
    "update_pool",
    "utilization",
]
