"""Shared helpers: events, address normalisation, snapshots, tenant lookups."""

from __future__ import annotations

import dataclasses
import ipaddress
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_ipam.contracts import (
    EVENT_SCHEMAS,
    AddressSnapshot,
    AssignmentSnapshot,
    BlockSnapshot,
    DelegatedPrefixSnapshot,
    OccupiedAddress,
    PoolSnapshot,
    RoutedPrefixSnapshot,
)
from dotmac_ipam.errors import IpamError, IpamNotFound
from dotmac_ipam.models import (
    IpamEvent,
    IPAssignment,
    IpBlock,
    IpPool,
    IPv4Address,
    IPv6Address,
    Ipv6DelegatedPrefix,
    SubscriberAdditionalRoute,
)

IPV4 = "ipv4"
IPV6 = "ipv6"
#: Allocation types that mark an address as infrastructure, never a service IP.
NON_SERVICE_ALLOCATION_TYPES = frozenset({"management", "device"})


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def event(
    db: Session,
    *,
    tenant_id: UUID,
    aggregate_ref: str,
    event_type: str,
    payload: dict[str, Any],
    occurred_at: datetime | None = None,
) -> None:
    schema = EVENT_SCHEMAS.get(event_type)
    if schema is not None:
        declared = {field.name for field in dataclasses.fields(schema)}
        unknown = set(payload) - declared
        if unknown:
            raise IpamError(
                f"{event_type} payload has undeclared keys: {sorted(unknown)}",
                code="ipam.event_schema",
            )
    db.add(
        IpamEvent(
            tenant_id=tenant_id,
            aggregate_ref=aggregate_ref,
            event_type=event_type,
            payload=payload,
            occurred_at=occurred_at or now(),
        )
    )


def clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise IpamError(f"{label} must not be blank")
    return cleaned


def bare(value: str | None) -> str:
    """Strip whitespace and a stored ``/32`` (or ``/128``) host suffix."""
    text = str(value or "").strip()
    if "/" in text:
        host, _, length = text.partition("/")
        if length in {"32", "128"}:
            return host
    return text


def address_candidates(addresses: set[str]) -> set[str]:
    return addresses | {f"{value}/32" for value in addresses}


def parse_v4(value: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(bare(value))
    except ValueError as exc:
        raise IpamError(f"invalid IPv4 address: {value!r}") from exc


def parse_v6(value: str) -> ipaddress.IPv6Address:
    try:
        return ipaddress.IPv6Address(bare(value))
    except ValueError as exc:
        raise IpamError(f"invalid IPv6 address: {value!r}") from exc


def v4_key(value: str) -> int:
    return int(ipaddress.IPv4Address(value))


def occupied_by_others(
    occupied: frozenset[OccupiedAddress], holder_ref: str
) -> set[str]:
    return {bare(item.address) for item in occupied if item.holder_ref != holder_ref}


# --- row lookups (always tenant-scoped) -----------------------------------


def get_pool(
    db: Session, tenant_id: UUID, pool_id: UUID, *, lock: bool = False
) -> IpPool:
    statement = select(IpPool).where(
        IpPool.tenant_id == tenant_id, IpPool.id == pool_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise IpamNotFound("pool not found", code="ipam.pool_not_found")
    return row


def get_pool_or_none(db: Session, tenant_id: UUID, pool_id: UUID) -> IpPool | None:
    return db.scalar(
        select(IpPool).where(IpPool.tenant_id == tenant_id, IpPool.id == pool_id)
    )


def pool_networks(
    db: Session, tenant_id: UUID, pool: IpPool
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Active blocks first; the pool CIDR only when it defines none."""
    cidrs = [
        str(cidr)
        for cidr in db.scalars(
            select(IpBlock.cidr).where(
                IpBlock.tenant_id == tenant_id,
                IpBlock.pool_id == pool.id,
                IpBlock.is_active.is_(True),
            )
        )
    ] or [str(pool.cidr)]
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in cidrs:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return networks


def routed_networks(db: Session, tenant_id: UUID) -> list[ipaddress.IPv4Network]:
    """Active routed prefixes: hosts belong to a customer via Framed-Route."""
    nets: list[ipaddress.IPv4Network] = []
    for cidr in db.scalars(
        select(SubscriberAdditionalRoute.cidr).where(
            SubscriberAdditionalRoute.tenant_id == tenant_id,
            SubscriberAdditionalRoute.is_active.is_(True),
        )
    ):
        try:
            net = ipaddress.ip_network(str(cidr), strict=False)
        except ValueError:
            continue
        if isinstance(net, ipaddress.IPv4Network):
            nets.append(net)
    return nets


def in_routed_block(db: Session, tenant_id: UUID, address: str) -> bool:
    try:
        parsed = ipaddress.IPv4Address(bare(address))
    except ValueError:
        return True  # unparseable never counts as safe
    return any(parsed in net for net in routed_networks(db, tenant_id))


def pool_contains(db: Session, tenant_id: UUID, pool: IpPool, address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(bare(address))
    except ValueError:
        return False
    return any(
        parsed.version == net.version and parsed in net
        for net in pool_networks(db, tenant_id, pool)
    )


def is_network_or_broadcast(
    db: Session, tenant_id: UUID, pool: IpPool, address: str
) -> bool:
    try:
        parsed = ipaddress.IPv4Address(bare(address))
    except ValueError:
        return False
    for net in pool_networks(db, tenant_id, pool):
        if (
            isinstance(net, ipaddress.IPv4Network)
            and net.prefixlen < 31
            and parsed in (net.network_address, net.broadcast_address)
        ):
            return True
    return False


def prefix_length_of(pool: IpPool | None) -> int | None:
    if pool is None:
        return None
    try:
        return ipaddress.ip_network(pool.cidr, strict=False).prefixlen
    except ValueError:
        return None


def pool_is_service_pool(pool: IpPool | None, family: str) -> bool:
    return (
        pool is not None
        and pool.is_active
        and pool.ip_version == family
        and pool.purpose == "customer"
    )


def v4_is_serviceable(address: IPv4Address, pool: IpPool | None) -> bool:
    if address.is_reserved or address.device_ref is not None:
        return False
    if (
        str(address.allocation_type or "").strip().lower()
        in NON_SERVICE_ALLOCATION_TYPES
    ):
        return False
    return pool_is_service_pool(pool, IPV4)


def v6_is_serviceable(address: IPv6Address, pool: IpPool | None) -> bool:
    if address.is_reserved:
        return False
    return pool_is_service_pool(pool, IPV6)


# --- snapshots ------------------------------------------------------------


def pool_snapshot(row: IpPool) -> PoolSnapshot:
    return PoolSnapshot(
        id=row.id,
        name=row.name,
        ip_version=row.ip_version,
        cidr=row.cidr,
        purpose=row.purpose,
        site_ref=row.site_ref,
        gateway=row.gateway,
        is_active=row.is_active,
        allow_network_broadcast=row.allow_network_broadcast,
        delegation_prefix_length=row.delegation_prefix_length,
    )


def block_snapshot(row: IpBlock) -> BlockSnapshot:
    return BlockSnapshot(
        id=row.id, pool_id=row.pool_id, cidr=row.cidr, is_active=row.is_active
    )


def address_snapshot(
    row: IPv4Address | IPv6Address, *, assigned: bool
) -> AddressSnapshot:
    return AddressSnapshot(
        id=row.id,
        address=row.address,
        family=IPV4 if isinstance(row, IPv4Address) else IPV6,
        pool_id=row.pool_id,
        is_reserved=row.is_reserved,
        allocation_type=getattr(row, "allocation_type", None),
        device_ref=getattr(row, "device_ref", None),
        assigned=assigned,
    )


def assignment_snapshot(
    row: IPAssignment, address: IPv4Address | IPv6Address
) -> AssignmentSnapshot:
    return AssignmentSnapshot(
        id=row.id,
        subscriber_id=row.subscriber_id,
        subscription_id=row.subscription_id,
        ip_version=row.ip_version,
        address_id=address.id,
        address=address.address,
        pool_id=address.pool_id,
        is_primary=row.is_primary,
        is_active=row.is_active,
        prefix_length=row.prefix_length,
        gateway=row.gateway,
        dns_primary=row.dns_primary,
        dns_secondary=row.dns_secondary,
        released_at=row.released_at,
        release_reason=row.release_reason,
    )


def prefix_snapshot(row: Ipv6DelegatedPrefix) -> DelegatedPrefixSnapshot:
    return DelegatedPrefixSnapshot(
        id=row.id,
        pool_id=row.pool_id,
        prefix=row.prefix,
        prefix_length=row.prefix_length,
        state=row.state,
        subscriber_id=row.subscriber_id,
        subscription_id=row.subscription_id,
    )


def routed_snapshot(row: SubscriberAdditionalRoute) -> RoutedPrefixSnapshot:
    return RoutedPrefixSnapshot(
        id=row.id,
        subscriber_id=row.subscriber_id,
        subscription_id=row.subscription_id,
        cidr=row.cidr,
        metric=row.metric,
        is_active=row.is_active,
    )
