"""Read-only IPAM queries returning immutable snapshots. Tenant-scoped always."""

from __future__ import annotations

import ipaddress
from collections import defaultdict
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from dotmac_ipam import _common as c
from dotmac_ipam.contracts import (
    AddressSnapshot,
    AmbiguousPrimary,
    AssignmentLookup,
    AssignmentSnapshot,
    AvailabilityResult,
    ConsistencyFinding,
    ConsistencyReport,
    EventRecord,
    PoolSnapshot,
)
from dotmac_ipam.errors import IpamError
from dotmac_ipam.models import (
    IpamEvent,
    IPAssignment,
    IpBlock,
    IpPool,
    IPv4Address,
    IPv6Address,
)


def lookup_assignments(
    db: Session, *, tenant_id: UUID, query: AssignmentLookup
) -> tuple[AssignmentSnapshot, ...]:
    """Assignments by subscription / subscriber / address (any combination)."""
    if not (query.subscription_ids or query.subscriber_ids or query.addresses):
        raise IpamError("a lookup needs at least one selector")
    statement = select(IPAssignment).where(IPAssignment.tenant_id == tenant_id)
    selectors: list[ColumnElement[bool]] = []
    if query.subscription_ids:
        selectors.append(IPAssignment.subscription_id.in_(query.subscription_ids))
    if query.subscriber_ids:
        selectors.append(IPAssignment.subscriber_id.in_(query.subscriber_ids))
    if query.addresses:
        values = {c.bare(v) for v in query.addresses}
        v4 = select(IPv4Address.id).where(
            IPv4Address.tenant_id == tenant_id, IPv4Address.address.in_(values)
        )
        v6 = select(IPv6Address.id).where(
            IPv6Address.tenant_id == tenant_id, IPv6Address.address.in_(values)
        )
        selectors.append(
            or_(
                IPAssignment.ipv4_address_id.in_(v4),
                IPAssignment.ipv6_address_id.in_(v6),
            )
        )
    statement = statement.where(or_(*selectors))
    if query.active_only:
        statement = statement.where(IPAssignment.is_active.is_(True))
    rows = db.scalars(
        statement.order_by(IPAssignment.created_at, IPAssignment.id)
    ).all()
    out: list[AssignmentSnapshot] = []
    for row in rows:
        model: Any = IPv6Address if row.ip_version == c.IPV6 else IPv4Address
        address_id = (
            row.ipv6_address_id if row.ip_version == c.IPV6 else row.ipv4_address_id
        )
        address = db.scalar(
            select(model).where(model.tenant_id == tenant_id, model.id == address_id)
        )
        if address is not None:
            out.append(c.assignment_snapshot(row, address))
    return tuple(out)


def primary_ipv4_for(
    db: Session, *, tenant_id: UUID, subscription_ids: tuple[UUID, ...]
) -> dict[UUID, str | AmbiguousPrimary]:
    """Served IPv4 per subscription: the marked primary, else the sole active
    address, else :class:`AmbiguousPrimary`. Subscriptions with none are absent."""
    rows = db.execute(
        select(
            IPAssignment.subscription_id, IPAssignment.is_primary, IPv4Address.address
        )
        .join(
            IPv4Address,
            (IPv4Address.tenant_id == IPAssignment.tenant_id)
            & (IPv4Address.id == IPAssignment.ipv4_address_id),
        )
        .where(
            IPAssignment.tenant_id == tenant_id,
            IPAssignment.subscription_id.in_(subscription_ids),
            IPAssignment.is_active.is_(True),
            IPAssignment.ip_version == c.IPV4,
        )
        .order_by(IPv4Address.address)
    ).all()
    grouped: dict[UUID, list[tuple[bool, str]]] = defaultdict(list)
    for sub_id, primary, address in rows:
        grouped[sub_id].append((bool(primary), c.bare(address)))
    out: dict[UUID, str | AmbiguousPrimary] = {}
    for sub_id, items in grouped.items():
        primaries = [a for p, a in items if p]
        if len(primaries) == 1:
            out[sub_id] = primaries[0]
        elif not primaries and len(items) == 1:
            out[sub_id] = items[0][1]
        else:
            out[sub_id] = AmbiguousPrimary(
                subscription_id=sub_id, addresses=tuple(sorted(a for _, a in items))
            )
    return out


def pools_for_site(
    db: Session, *, tenant_id: UUID, site_ref: UUID, active_only: bool = True
) -> tuple[PoolSnapshot, ...]:
    statement = select(IpPool).where(
        IpPool.tenant_id == tenant_id, IpPool.site_ref == site_ref
    )
    if active_only:
        statement = statement.where(IpPool.is_active.is_(True))
    return tuple(
        c.pool_snapshot(p) for p in db.scalars(statement.order_by(IpPool.name))
    )


def lookup_address(
    db: Session, *, tenant_id: UUID, address: str
) -> AddressSnapshot | None:
    value = c.bare(address)
    try:
        parsed = ipaddress.ip_address(value)
    except ValueError as exc:
        raise IpamError(f"invalid address: {address!r}") from exc
    model: Any = IPv4Address if parsed.version == 4 else IPv6Address
    column = (
        IPAssignment.ipv4_address_id
        if parsed.version == 4
        else IPAssignment.ipv6_address_id
    )
    row = db.scalar(
        select(model).where(model.tenant_id == tenant_id, model.address == str(parsed))
    )
    if row is None:
        return None
    assigned = (
        db.scalar(
            select(IPAssignment.id).where(
                IPAssignment.tenant_id == tenant_id,
                column == row.id,
                IPAssignment.is_active.is_(True),
            )
        )
        is not None
    )
    return c.address_snapshot(row, assigned=assigned)


def check_address_available(
    db: Session, *, tenant_id: UUID, address: str
) -> AvailabilityResult:
    """Static-address availability. Outside every managed block the address
    stays a manual value (available, ``in_managed_block=False``)."""
    parsed = c.parse_v4(address)
    value = str(parsed)
    row = db.scalar(
        select(IPv4Address).where(
            IPv4Address.tenant_id == tenant_id, IPv4Address.address == value
        )
    )
    in_block = any(
        isinstance(
            net := ipaddress.ip_network(cidr, strict=False), ipaddress.IPv4Network
        )
        and parsed in net
        for cidr in db.scalars(
            select(IpBlock.cidr).where(
                IpBlock.tenant_id == tenant_id, IpBlock.is_active.is_(True)
            )
        )
    )
    if row is None and not in_block:
        return AvailabilityResult(value, True, "outside_managed_blocks", False)
    if row is not None:
        if db.scalar(
            select(IPAssignment.id).where(
                IPAssignment.tenant_id == tenant_id,
                IPAssignment.ipv4_address_id == row.id,
                IPAssignment.is_active.is_(True),
            )
        ):
            return AvailabilityResult(value, False, "assigned", True)
        if row.is_reserved or row.device_ref is not None:
            return AvailabilityResult(value, False, "reserved", True)
    return AvailabilityResult(value, True, "free", True)


def events(
    db: Session, *, tenant_id: UUID, aggregate_ref: str
) -> tuple[EventRecord, ...]:
    rows = db.scalars(
        select(IpamEvent)
        .where(
            IpamEvent.tenant_id == tenant_id, IpamEvent.aggregate_ref == aggregate_ref
        )
        .order_by(IpamEvent.occurred_at, IpamEvent.id)
    )
    return tuple(
        EventRecord(r.aggregate_ref, r.event_type, dict(r.payload), r.occurred_at)
        for r in rows
    )


def audit_consistency(db: Session, *, tenant_id: UUID) -> ConsistencyReport:
    """IPAM-internal drift report (read-only). Anything needing sessions, NAS or
    subscriptions is the composition root's audit, not the module's."""
    findings: list[ConsistencyFinding] = []

    # active assignment without an owning service
    for row in db.scalars(
        select(IPAssignment).where(
            IPAssignment.tenant_id == tenant_id,
            IPAssignment.is_active.is_(True),
            IPAssignment.subscription_id.is_(None),
        )
    ):
        findings.append(ConsistencyFinding("active_unbound_assignment", str(row.id)))

    # active assignments whose address is unpooled or outside its pool
    pools = {
        p.id: p for p in db.scalars(select(IpPool).where(IpPool.tenant_id == tenant_id))
    }
    v4_rows = db.execute(
        select(IPAssignment, IPv4Address)
        .join(
            IPv4Address,
            (IPv4Address.tenant_id == IPAssignment.tenant_id)
            & (IPv4Address.id == IPAssignment.ipv4_address_id),
        )
        .where(IPAssignment.tenant_id == tenant_id, IPAssignment.is_active.is_(True))
    ).all()
    routes = c.routed_networks(db, tenant_id)
    per_sub: dict[UUID, list[bool]] = defaultdict(list)
    for assignment, address in v4_rows:
        pool = pools.get(address.pool_id) if address.pool_id else None
        if pool is None or not c.pool_contains(db, tenant_id, pool, address.address):
            findings.append(
                ConsistencyFinding(
                    "assignment_address_unpooled", str(assignment.id), address.address
                )
            )
        if address.is_reserved or address.device_ref is not None:
            findings.append(
                ConsistencyFinding(
                    "assignment_on_reserved_address",
                    str(assignment.id),
                    address.address,
                )
            )
        if any(ipaddress.IPv4Address(address.address) in net for net in routes):
            findings.append(
                ConsistencyFinding(
                    "assignment_in_routed_block", str(assignment.id), address.address
                )
            )
        if assignment.subscription_id is not None:
            per_sub[assignment.subscription_id].append(bool(assignment.is_primary))
    for sub_id, flags in per_sub.items():
        if sum(flags) > 1 or (len(flags) > 1 and not any(flags)):
            findings.append(ConsistencyFinding("primary_ambiguous", str(sub_id)))

    # customer pools must name a site; active pools of one family must not overlap
    active = [p for p in pools.values() if p.is_active]
    for pool in active:
        if pool.purpose == "customer" and pool.site_ref is None:
            findings.append(
                ConsistencyFinding("customer_pool_missing_site", str(pool.id))
            )
    for index, left in enumerate(active):
        for right in active[index + 1 :]:
            if left.ip_version != right.ip_version:
                continue
            a = ipaddress.ip_network(left.cidr, strict=False)
            b = ipaddress.ip_network(right.cidr, strict=False)
            if a.overlaps(b):
                findings.append(
                    ConsistencyFinding("pool_overlap", f"{left.id}|{right.id}")
                )

    # duplicate active rows on one address (defensive: the unique index forbids it)
    dup = db.execute(
        select(IPAssignment.ipv4_address_id)
        .where(
            IPAssignment.tenant_id == tenant_id,
            IPAssignment.is_active.is_(True),
            IPAssignment.ipv4_address_id.is_not(None),
        )
        .group_by(IPAssignment.ipv4_address_id)
        .having(func.count() > 1)
    ).scalars()
    for address_id in dup:
        findings.append(ConsistencyFinding("address_multiple_active", str(address_id)))
    return ConsistencyReport(tuple(findings))


__all__ = [
    "audit_consistency",
    "check_address_available",
    "events",
    "lookup_address",
    "lookup_assignments",
    "pools_for_site",
    "primary_ipv4_for",
]
