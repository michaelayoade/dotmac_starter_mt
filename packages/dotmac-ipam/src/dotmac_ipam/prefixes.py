"""IPv6 prefix delegation (PD) and routed (Framed-Route) prefixes.

Extracted from Sub's ``ipv6_pd`` allocator. The feature flag, RADIUS-profile
pool resolution and ``provision_pd_for_subscription`` are product policy and
stay in Sub; the composition passes an explicit ``pool_id``.
"""

from __future__ import annotations

import ipaddress
import itertools
from collections.abc import Iterator
from uuid import UUID

from dotmac_kernel.transactions import conflict_savepoint
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_ipam import _common as c
from dotmac_ipam.contracts import (
    EVENT_PREFIX_DELEGATED,
    EVENT_ROUTED_PREFIX_CHANGED,
    AllocateDelegatedPrefix,
    DelegatedPrefixSnapshot,
    PrefixState,
    ReleaseDelegatedPrefix,
    RetireRoutedPrefix,
    RoutedPrefixSnapshot,
    UpsertRoutedPrefix,
)
from dotmac_ipam.errors import IpamConflict, IpamError, IpamNotFound, PoolExhausted
from dotmac_ipam.guard import command
from dotmac_ipam.models import (
    IPAssignment,
    IpPool,
    IPv4Address,
    Ipv6DelegatedPrefix,
    SubscriberAdditionalRoute,
)

DEFAULT_DELEGATION_PREFIX_LENGTH = 64
#: Bound the scan for the next free prefix (a /32 carved into /64s is huge).
MATERIALIZE_SCAN_CAP = 8192
#: Namespace for the per-pool advisory lock (key1); key2 is hashtext(pool_id).
PD_LOCK_NAMESPACE = 778_010


def delegation_length(pool: IpPool) -> int:
    try:
        length = (
            int(pool.delegation_prefix_length)
            if pool.delegation_prefix_length
            else DEFAULT_DELEGATION_PREFIX_LENGTH
        )
    except (TypeError, ValueError):
        length = DEFAULT_DELEGATION_PREFIX_LENGTH
    return length


def _parent(pool: IpPool) -> ipaddress.IPv6Network | None:
    try:
        net = ipaddress.ip_network(str(pool.cidr), strict=False)
    except ValueError:
        return None
    return net if isinstance(net, ipaddress.IPv6Network) else None


def iter_candidate_prefixes(
    pool: IpPool, *, cap: int = MATERIALIZE_SCAN_CAP
) -> Iterator[ipaddress.IPv6Network]:
    """Aligned, never-overlapping child networks at the delegation size."""
    parent = _parent(pool)
    if parent is None:
        return
    length = delegation_length(pool)
    if length < parent.prefixlen or length > 128:
        return
    yield from itertools.islice(parent.subnets(new_prefix=length), cap)


def _advisory_lock(db: Session, pool_id: UUID) -> None:
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        db.execute(
            select(
                func.pg_advisory_xact_lock(
                    PD_LOCK_NAMESPACE, func.hashtext(str(pool_id))
                )
            )
        )


@command
def allocate_delegated_prefix(
    db: Session, *, tenant_id: UUID, command: AllocateDelegatedPrefix
) -> DelegatedPrefixSnapshot:
    """Delegate a prefix; idempotent per service (legacy: per subscriber)."""
    pool = c.get_pool(db, tenant_id, command.pool_id, lock=True)
    if not c.pool_is_service_pool(pool, c.IPV6) or _parent(pool) is None:
        raise IpamError(
            "prefix delegation needs an active customer IPv6 pool",
            code="ipam.pool_not_allocatable",
        )
    length = delegation_length(pool)
    owner = (
        Ipv6DelegatedPrefix.subscription_id == command.subscription_id
        if command.subscription_id is not None
        else Ipv6DelegatedPrefix.subscriber_id == command.subscriber_id
    )
    existing = db.scalar(
        select(Ipv6DelegatedPrefix)
        .where(
            Ipv6DelegatedPrefix.tenant_id == tenant_id,
            Ipv6DelegatedPrefix.pool_id == pool.id,
            owner,
            Ipv6DelegatedPrefix.state == PrefixState.ASSIGNED.value,
        )
        .limit(1)
    )
    if existing is not None:
        return c.prefix_snapshot(existing)

    _advisory_lock(db, pool.id)
    free = db.scalar(
        select(Ipv6DelegatedPrefix)
        .where(
            Ipv6DelegatedPrefix.tenant_id == tenant_id,
            Ipv6DelegatedPrefix.pool_id == pool.id,
            Ipv6DelegatedPrefix.state == PrefixState.AVAILABLE.value,
        )
        .order_by(Ipv6DelegatedPrefix.prefix)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    row: Ipv6DelegatedPrefix | None = None
    if free is not None:
        free.state = PrefixState.ASSIGNED.value
        free.subscriber_id = command.subscriber_id
        free.subscription_id = command.subscription_id
        row = free
    else:
        taken = set(
            db.scalars(
                select(Ipv6DelegatedPrefix.prefix).where(
                    Ipv6DelegatedPrefix.tenant_id == tenant_id,
                    Ipv6DelegatedPrefix.pool_id == pool.id,
                )
            )
        )
        for candidate in iter_candidate_prefixes(pool):
            net_str = str(candidate.network_address)
            if net_str in taken:
                continue
            attempt = Ipv6DelegatedPrefix(
                tenant_id=tenant_id,
                pool_id=pool.id,
                prefix=net_str,
                prefix_length=length,
                state=PrefixState.ASSIGNED.value,
                subscriber_id=command.subscriber_id,
                subscription_id=command.subscription_id,
            )
            try:
                with conflict_savepoint(db):
                    db.add(attempt)
                    db.flush()
            except IntegrityError:
                continue  # concurrent create of this prefix: try the next
            row = attempt
            break
    if row is None:
        raise PoolExhausted(
            "delegated prefix pool is exhausted", code="ipam.pool_exhausted"
        )
    db.flush()
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"prefix:{row.id}",
        event_type=EVENT_PREFIX_DELEGATED,
        payload={
            "prefix": f"{row.prefix}/{row.prefix_length}",
            "state": row.state,
            "subscription_id": str(command.subscription_id)
            if command.subscription_id
            else None,
        },
    )
    db.flush()
    return c.prefix_snapshot(row)


@command
def release_delegated_prefix(
    db: Session, *, tenant_id: UUID, command: ReleaseDelegatedPrefix
) -> int:
    """Return delegated prefixes to the pool (available). Rows are kept."""
    chosen = [
        v
        for v in (command.prefix_id, command.subscription_id, command.subscriber_id)
        if v is not None
    ]
    if len(chosen) != 1:
        raise IpamError("exactly one of prefix_id / subscription_id / subscriber_id")
    statement = select(Ipv6DelegatedPrefix).where(
        Ipv6DelegatedPrefix.tenant_id == tenant_id,
        Ipv6DelegatedPrefix.state == PrefixState.ASSIGNED.value,
    )
    if command.prefix_id is not None:
        statement = statement.where(Ipv6DelegatedPrefix.id == command.prefix_id)
    elif command.subscription_id is not None:
        statement = statement.where(
            Ipv6DelegatedPrefix.subscription_id == command.subscription_id
        )
    else:
        statement = statement.where(
            Ipv6DelegatedPrefix.subscriber_id == command.subscriber_id
        )
    rows = db.scalars(statement.with_for_update()).all()
    for row in rows:
        row.state = PrefixState.AVAILABLE.value
        row.subscriber_id = None
        row.subscription_id = None
        c.event(
            db,
            tenant_id=tenant_id,
            aggregate_ref=f"prefix:{row.id}",
            event_type=EVENT_PREFIX_DELEGATED,
            payload={
                "prefix": f"{row.prefix}/{row.prefix_length}",
                "state": "available",
            },
        )
    db.flush()
    return len(rows)


def delegated_prefix_for(
    db: Session,
    *,
    tenant_id: UUID,
    subscription_id: UUID | None = None,
    subscriber_id: UUID | None = None,
) -> str | None:
    """The assigned PD as CIDR (``Delegated-IPv6-Prefix``), exact service first.

    Legacy fallback: a subscriber-level PD only when it is unambiguous."""
    base = select(Ipv6DelegatedPrefix.prefix, Ipv6DelegatedPrefix.prefix_length).where(
        Ipv6DelegatedPrefix.tenant_id == tenant_id,
        Ipv6DelegatedPrefix.state == PrefixState.ASSIGNED.value,
    )
    if subscription_id is not None:
        row = db.execute(
            base.where(Ipv6DelegatedPrefix.subscription_id == subscription_id)
            .order_by(Ipv6DelegatedPrefix.prefix)
            .limit(1)
        ).first()
        if row:
            return f"{row[0]}/{row[1]}"
    if subscriber_id is None:
        return None
    legacy = db.execute(
        base.where(
            Ipv6DelegatedPrefix.subscriber_id == subscriber_id,
            Ipv6DelegatedPrefix.subscription_id.is_(None),
        )
        .order_by(Ipv6DelegatedPrefix.prefix)
        .limit(2)
    ).all()
    return f"{legacy[0][0]}/{legacy[0][1]}" if len(legacy) == 1 else None


# --- routed prefixes ---------------------------------------------------------


def _routed_cidr(value: str) -> ipaddress.IPv4Network:
    try:
        net = ipaddress.ip_network(value.strip(), strict=False)
    except ValueError as exc:
        raise IpamError("routed prefix is not a valid CIDR") from exc
    if not isinstance(net, ipaddress.IPv4Network):
        raise IpamError("routed prefixes are IPv4 only")
    return net


@command
def upsert_routed_prefix(
    db: Session, *, tenant_id: UUID, command: UpsertRoutedPrefix
) -> RoutedPrefixSnapshot:
    net = _routed_cidr(command.cidr)
    cidr = str(net)
    # A routed block must not swallow an address another service holds as a host.
    held = db.execute(
        select(IPv4Address.address)
        .join(
            IPAssignment,
            (IPAssignment.tenant_id == IPv4Address.tenant_id)
            & (IPAssignment.ipv4_address_id == IPv4Address.id),
        )
        .where(IPv4Address.tenant_id == tenant_id, IPAssignment.is_active.is_(True))
    ).scalars()
    for address in held:
        if ipaddress.IPv4Address(address) in net:
            raise IpamConflict(
                "routed prefix contains an actively assigned host address",
                code="ipam.routed_prefix_in_use",
            )
    row = db.scalar(
        select(SubscriberAdditionalRoute)
        .where(
            SubscriberAdditionalRoute.tenant_id == tenant_id,
            SubscriberAdditionalRoute.subscriber_id == command.subscriber_id,
            SubscriberAdditionalRoute.cidr == cidr,
        )
        .with_for_update()
    )
    if row is None:
        row = SubscriberAdditionalRoute(
            tenant_id=tenant_id,
            subscriber_id=command.subscriber_id,
            cidr=cidr,
            prefix_length=net.prefixlen,
        )
        db.add(row)
    row.subscription_id = command.subscription_id
    row.metric = command.metric
    row.source = command.source
    row.is_active = True
    db.flush()
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"routed:{row.id}",
        event_type=EVENT_ROUTED_PREFIX_CHANGED,
        payload={"cidr": cidr, "active": True},
    )
    db.flush()
    return c.routed_snapshot(row)


@command
def retire_routed_prefix(
    db: Session, *, tenant_id: UUID, command: RetireRoutedPrefix
) -> RoutedPrefixSnapshot:
    cidr = str(_routed_cidr(command.cidr))
    row = db.scalar(
        select(SubscriberAdditionalRoute)
        .where(
            SubscriberAdditionalRoute.tenant_id == tenant_id,
            SubscriberAdditionalRoute.subscriber_id == command.subscriber_id,
            SubscriberAdditionalRoute.cidr == cidr,
        )
        .with_for_update()
    )
    if row is None:
        raise IpamNotFound("routed prefix not found", code="ipam.routed_not_found")
    row.is_active = False
    db.flush()
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"routed:{row.id}",
        event_type=EVENT_ROUTED_PREFIX_CHANGED,
        payload={"cidr": cidr, "active": False},
    )
    db.flush()
    return c.routed_snapshot(row)


__all__ = [
    "allocate_delegated_prefix",
    "delegated_prefix_for",
    "release_delegated_prefix",
    "retire_routed_prefix",
    "upsert_routed_prefix",
]
