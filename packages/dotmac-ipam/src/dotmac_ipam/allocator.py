"""Allocation, assignment, release and move commands (flush-only).

Extracted from Sub's ``provisioning_helpers`` allocator, ``ip_lifecycle``
terminal release and ``ont_management_ipam`` core. Every ``db.commit()`` of the
source is removed: the caller owns the transaction. Selection is by exact
``subscription_id`` and never by a subscriber ``.first()`` (SPEC I-8).
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from dotmac_kernel.transactions import conflict_savepoint
from sqlalchemy import and_, cast, or_, select
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_ipam import _common as c
from dotmac_ipam.contracts import (
    EVENT_ADDRESS_ASSIGNED,
    EVENT_ASSIGNMENT_MOVED,
    EVENT_ASSIGNMENT_RELEASED,
    EVENT_PRIMARY_CHANGED,
    AddressSnapshot,
    AllocateAddress,
    AllocateManagementAddress,
    AssignAddress,
    AssignmentSnapshot,
    Cooldown,
    MarkPrimary,
    MoveAssignment,
    OccupiedAddress,
    ReleaseAssignment,
    ReleaseForSubject,
)
from dotmac_ipam.errors import IpamConflict, IpamError, IpamNotFound, PoolExhausted
from dotmac_ipam.guard import command
from dotmac_ipam.models import IPAssignment, IpPool, IPv4Address, IPv6Address

#: Hosts materialised per CIDR on demand (a wide fallback CIDR cannot flood memory).
ON_DEMAND_HOST_SCAN_CAP = 4096
#: Candidate rows read per pool before numeric ordering.
CANDIDATE_ROW_CAP = 5000
#: Allocation markers cleared when a service release frees the address.
SERVICE_ALLOCATION_TYPES = frozenset({"wan", "static"})


# --- primary marker (the single writer of IPAssignment.is_primary) ----------


def _mark_primary(
    db: Session, tenant_id: UUID, subscription_id: UUID, ipv4_address_id: UUID | None
) -> bool:
    """Move the served-address marker to one active exact-service assignment.

    Demotes siblings first and flushes: the partial unique index permits only
    one active primary per service, so promoting first would violate it."""
    if ipv4_address_id is None:
        return False
    active = db.scalars(
        select(IPAssignment).where(
            IPAssignment.tenant_id == tenant_id,
            IPAssignment.subscription_id == subscription_id,
            IPAssignment.is_active.is_(True),
            IPAssignment.ipv4_address_id.is_not(None),
        )
    ).all()
    target = next((a for a in active if a.ipv4_address_id == ipv4_address_id), None)
    if target is None:
        return False
    demoted = False
    for item in active:
        if item.id != target.id and item.is_primary:
            item.is_primary = False
            demoted = True
    if demoted:
        db.flush()
    changed = demoted
    if not target.is_primary:
        target.is_primary = True
        changed = True
        db.flush()
    if changed:
        c.event(
            db,
            tenant_id=tenant_id,
            aggregate_ref=f"subscription:{subscription_id}",
            event_type=EVENT_PRIMARY_CHANGED,
            payload={"assignment_id": str(target.id)},
        )
    return True


@command
def mark_primary(db: Session, *, tenant_id: UUID, command: MarkPrimary) -> bool:
    return _mark_primary(
        db, tenant_id, command.subscription_id, command.ipv4_address_id
    )


def _has_active_primary(db: Session, tenant_id: UUID, subscription_id: UUID) -> bool:
    return (
        db.scalar(
            select(IPAssignment.id).where(
                IPAssignment.tenant_id == tenant_id,
                IPAssignment.subscription_id == subscription_id,
                IPAssignment.is_active.is_(True),
                IPAssignment.is_primary.is_(True),
                IPAssignment.ipv4_address_id.is_not(None),
            )
        )
        is not None
    )


# --- candidate selection -----------------------------------------------------


def _cooled_address_ids(
    db: Session,
    tenant_id: UUID,
    cooldown: Cooldown | None,
    subscription_id: UUID,
    family: str,
) -> set[UUID]:
    """Addresses released inside the cool-down window by someone else (I-1)."""
    if cooldown is None or cooldown.window_seconds <= 0:
        return set()
    cutoff = c.aware(cooldown.now) - timedelta(seconds=cooldown.window_seconds)
    column = (
        IPAssignment.ipv6_address_id
        if family == c.IPV6
        else IPAssignment.ipv4_address_id
    )
    return set(
        db.scalars(
            select(column).where(
                IPAssignment.tenant_id == tenant_id,
                IPAssignment.is_active.is_(False),
                IPAssignment.released_at.is_not(None),
                IPAssignment.released_at >= cutoff,
                column.is_not(None),
                or_(
                    IPAssignment.subscription_id.is_(None),
                    IPAssignment.subscription_id != subscription_id,
                ),
            )
        )
    )


def _excluded_literal(
    db: Session,
    tenant_id: UUID,
    pool: IpPool,
    address: str,
    *,
    routes: list[ipaddress.IPv4Network],
    excluded: set[str],
) -> bool:
    if address in excluded:
        return True
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError:
        return True
    if pool.gateway and c.bare(pool.gateway) == address:
        return True  # I-2: never hand out the pool gateway
    if any(parsed in net for net in routes):
        return True  # routed block belongs to a customer via Framed-Route
    if not pool.allow_network_broadcast and c.is_network_or_broadcast(
        db, tenant_id, pool, address
    ):
        return True
    return False


def _row_is_service_free(row: IPv4Address) -> bool:
    return (
        not row.is_reserved
        and row.device_ref is None
        and str(row.allocation_type or "").lower() not in c.NON_SERVICE_ALLOCATION_TYPES
    )


def _active_exists(db: Session, tenant_id: UUID, column: Any, address_id: UUID) -> bool:
    return (
        db.scalar(
            select(IPAssignment.id).where(
                IPAssignment.tenant_id == tenant_id,
                column == address_id,
                IPAssignment.is_active.is_(True),
            )
        )
        is not None
    )


def _numeric_order(db: Session) -> Any:
    """Numeric (inet) ordering in SQL on PostgreSQL, so the candidate window is
    the numerically lowest rows. SQLite (unit fixtures) has no inet and falls
    back to string order; the Python sort below makes the final pick numeric."""
    if db.get_bind().dialect.name == "postgresql":
        return cast(IPv4Address.address, INET)
    return IPv4Address.address


def _pick_materialised_v4(
    db: Session,
    tenant_id: UUID,
    pool: IpPool,
    *,
    excluded: set[str],
    cooled: set[UUID],
    routes: list[ipaddress.IPv4Network],
) -> IPv4Address | None:
    rows = db.scalars(
        select(IPv4Address)
        .outerjoin(
            IPAssignment,
            and_(
                IPAssignment.tenant_id == IPv4Address.tenant_id,
                IPAssignment.ipv4_address_id == IPv4Address.id,
                IPAssignment.is_active.is_(True),
            ),
        )
        .where(
            IPv4Address.tenant_id == tenant_id,
            IPv4Address.pool_id == pool.id,
            IPAssignment.id.is_(None),
        )
        .order_by(_numeric_order(db))
        .limit(CANDIDATE_ROW_CAP)
    ).all()
    candidates = [
        row
        for row in rows
        if _row_is_service_free(row)
        and row.id not in cooled
        and not _excluded_literal(
            db, tenant_id, pool, row.address, routes=routes, excluded=excluded
        )
    ]
    candidates.sort(key=lambda row: c.v4_key(row.address))
    for candidate in candidates:
        # SKIP LOCKED: concurrent allocations pick different rows (no-op on SQLite).
        locked = db.scalar(
            select(IPv4Address)
            .where(IPv4Address.tenant_id == tenant_id, IPv4Address.id == candidate.id)
            .with_for_update(skip_locked=True)
        )
        if locked is None:
            continue
        if _active_exists(db, tenant_id, IPAssignment.ipv4_address_id, locked.id):
            continue
        return locked
    return None


def _allocate_v4_on_demand(
    db: Session,
    tenant_id: UUID,
    pool: IpPool,
    *,
    excluded: set[str],
    cooled: set[UUID],
    routes: list[ipaddress.IPv4Network],
) -> IPv4Address | None:
    """Materialise the lowest safe free host of the pool's blocks/CIDR."""
    for network in c.pool_networks(db, tenant_id, pool):
        if not isinstance(network, ipaddress.IPv4Network):
            continue
        hosts: list[str] = []
        scanned = 0
        iterator = network if pool.allow_network_broadcast else network.hosts()
        for host in iterator:
            scanned += 1
            if scanned > ON_DEMAND_HOST_SCAN_CAP * 2:
                break
            text = str(host)
            if _excluded_literal(
                db, tenant_id, pool, text, routes=routes, excluded=excluded
            ):
                continue
            hosts.append(text)
            if len(hosts) >= ON_DEMAND_HOST_SCAN_CAP:
                break
        if not hosts:
            continue
        existing = {
            row.address: row
            for row in db.scalars(
                select(IPv4Address).where(
                    IPv4Address.tenant_id == tenant_id,
                    IPv4Address.address.in_(hosts),
                )
            )
        }
        assigned_ids = (
            set(
                db.scalars(
                    select(IPAssignment.ipv4_address_id).where(
                        IPAssignment.tenant_id == tenant_id,
                        IPAssignment.is_active.is_(True),
                        IPAssignment.ipv4_address_id.in_(
                            [row.id for row in existing.values()]
                        ),
                    )
                )
            )
            if existing
            else set()
        )
        for text in hosts:  # ascending numeric order
            row = existing.get(text)
            if row is not None:
                if (
                    not _row_is_service_free(row)
                    or row.pool_id not in (None, pool.id)
                    or row.id in assigned_ids
                    or row.id in cooled
                ):
                    continue
                if row.pool_id is None:
                    row.pool_id = pool.id
                    db.flush()
                return row
            candidate = IPv4Address(tenant_id=tenant_id, address=text, pool_id=pool.id)
            try:
                with conflict_savepoint(db):
                    db.add(candidate)
                    db.flush()
            except IntegrityError:
                continue  # concurrent create of this IP: try the next host
            return candidate
    return None


def _pick_v6(
    db: Session, tenant_id: UUID, pool: IpPool, *, cooled: set[UUID], excluded: set[str]
) -> IPv6Address | None:
    rows = db.scalars(
        select(IPv6Address)
        .outerjoin(
            IPAssignment,
            and_(
                IPAssignment.tenant_id == IPv6Address.tenant_id,
                IPAssignment.ipv6_address_id == IPv6Address.id,
                IPAssignment.is_active.is_(True),
            ),
        )
        .where(
            IPv6Address.tenant_id == tenant_id,
            IPv6Address.pool_id == pool.id,
            IPv6Address.is_reserved.is_(False),
            IPAssignment.id.is_(None),
        )
        .order_by(IPv6Address.address)
        .limit(CANDIDATE_ROW_CAP)
    ).all()
    for row in rows:
        if row.id in cooled or c.bare(row.address) in excluded:
            continue
        locked = db.scalar(
            select(IPv6Address)
            .where(IPv6Address.tenant_id == tenant_id, IPv6Address.id == row.id)
            .with_for_update(skip_locked=True)
        )
        if locked is not None:
            return locked
    return None


# --- assignment rows ---------------------------------------------------------


def _new_assignment(
    db: Session,
    tenant_id: UUID,
    *,
    family: str,
    address: IPv4Address | IPv6Address,
    pool: IpPool | None,
    subscription_id: UUID,
    subscriber_id: UUID | None,
) -> IPAssignment:
    row = IPAssignment(
        tenant_id=tenant_id,
        subscriber_id=subscriber_id,
        subscription_id=subscription_id,
        ip_version=family,
        ipv4_address_id=address.id if family == c.IPV4 else None,
        ipv6_address_id=address.id if family == c.IPV6 else None,
        prefix_length=c.prefix_length_of(pool),
        gateway=pool.gateway if pool is not None else None,
        dns_primary=pool.dns_primary if pool is not None else None,
        dns_secondary=pool.dns_secondary if pool is not None else None,
        is_active=True,
        is_primary=False,
    )
    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise IpamConflict(
            "address already has an active assignment", code="ipam.address_assigned"
        ) from exc
    return row


def _address_of(
    db: Session, tenant_id: UUID, row: IPAssignment
) -> IPv4Address | IPv6Address:
    address: IPv4Address | IPv6Address | None
    if row.ip_version == c.IPV6:
        address = db.scalar(
            select(IPv6Address).where(
                IPv6Address.tenant_id == tenant_id,
                IPv6Address.id == row.ipv6_address_id,
            )
        )
    else:
        address = db.scalar(
            select(IPv4Address).where(
                IPv4Address.tenant_id == tenant_id,
                IPv4Address.id == row.ipv4_address_id,
            )
        )
    if address is None:
        raise IpamNotFound("assignment address not found")
    return address


def _snapshot(db: Session, tenant_id: UUID, row: IPAssignment) -> AssignmentSnapshot:
    return c.assignment_snapshot(row, _address_of(db, tenant_id, row))


def _assigned_event(
    db: Session,
    tenant_id: UUID,
    row: IPAssignment,
    address: IPv4Address | IPv6Address,
    **extra: object,
) -> None:
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"subscription:{row.subscription_id}",
        event_type=EVENT_ADDRESS_ASSIGNED,
        payload={
            "assignment_id": str(row.id),
            "address": address.address,
            "ip_version": row.ip_version,
            **extra,
        },
    )


def _reactivation_is_valid(
    db: Session,
    tenant_id: UUID,
    address: IPv4Address | IPv6Address | None,
    *,
    other_occupied: set[str],
) -> bool:
    """A previously released address is reusable only while it keeps its role."""
    if address is None:
        return False
    if isinstance(address, IPv6Address):
        return not address.is_reserved and not _active_exists(
            db, tenant_id, IPAssignment.ipv6_address_id, address.id
        )
    if not _row_is_service_free(address):
        return False
    if _active_exists(db, tenant_id, IPAssignment.ipv4_address_id, address.id):
        return False
    if c.bare(address.address) in other_occupied:
        return False
    return not c.in_routed_block(db, tenant_id, address.address)


@command
def allocate_address(
    db: Session, *, tenant_id: UUID, command: AllocateAddress
) -> AssignmentSnapshot:
    family = command.family.value
    sub_id = command.subscription_id
    if not command.pool_ids:
        raise IpamError("at least one pool is required", code="ipam.pool_required")

    # Lock every pool in sorted id order (no lock-order deadlock between
    # concurrent callers with overlapping pool lists), THEN look for the
    # service's existing assignment, so a concurrent allocation for the same
    # service that committed while we waited is seen rather than duplicated.
    by_id: dict[UUID, IpPool] = {}
    for pool_id in sorted(set(command.pool_ids), key=str):
        pool = c.get_pool(db, tenant_id, pool_id, lock=True)
        if not c.pool_is_service_pool(pool, family):
            raise IpamError(
                f"pool {pool.name!r} is not an active customer {family} pool",
                code="ipam.pool_not_allocatable",
            )
        by_id[pool_id] = pool
    pools = [by_id[pool_id] for pool_id in command.pool_ids]

    # Idempotent: an exact-service active assignment of this family is the answer.
    existing = db.scalar(
        select(IPAssignment)
        .where(
            IPAssignment.tenant_id == tenant_id,
            IPAssignment.subscription_id == sub_id,
            IPAssignment.ip_version == family,
            IPAssignment.is_active.is_(True),
        )
        .order_by(IPAssignment.is_primary.desc(), IPAssignment.id)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        return _snapshot(db, tenant_id, existing)

    holder = str(sub_id)
    other_occupied = c.occupied_by_others(command.occupied, holder)
    routes = c.routed_networks(db, tenant_id) if family == c.IPV4 else []
    cooled = _cooled_address_ids(db, tenant_id, command.cooldown, sub_id, family)

    # Reactivate the service's own released address (IP stability across
    # suspend/resume). The cool-down never blocks the same subject (I-1).
    inactive = db.scalar(
        select(IPAssignment)
        .where(
            IPAssignment.tenant_id == tenant_id,
            IPAssignment.subscription_id == sub_id,
            IPAssignment.ip_version == family,
            IPAssignment.is_active.is_(False),
        )
        .order_by(IPAssignment.updated_at.desc(), IPAssignment.id)
        .limit(1)
    )
    if inactive is not None:
        old = _address_of(db, tenant_id, inactive)
        if old.pool_id in {p.id for p in pools} and _reactivation_is_valid(
            db, tenant_id, old, other_occupied=other_occupied
        ):
            inactive.is_active = True
            inactive.released_at = None
            inactive.release_reason = None
            inactive.subscriber_id = command.subscriber_id or inactive.subscriber_id
            db.flush()
            if family == c.IPV4 and (
                command.mark_primary and not _has_active_primary(db, tenant_id, sub_id)
            ):
                _mark_primary(db, tenant_id, sub_id, inactive.ipv4_address_id)
            _assigned_event(db, tenant_id, inactive, old, reactivated=True)
            db.flush()
            return c.assignment_snapshot(inactive, old)

    picked: IPv4Address | IPv6Address | None = None
    chosen: IpPool | None = None
    for pool in pools:
        if family == c.IPV4:
            picked = _pick_materialised_v4(
                db,
                tenant_id,
                pool,
                excluded=other_occupied,
                cooled=cooled,
                routes=routes,
            ) or _allocate_v4_on_demand(
                db,
                tenant_id,
                pool,
                excluded=other_occupied,
                cooled=cooled,
                routes=routes,
            )
        else:
            picked = _pick_v6(
                db, tenant_id, pool, cooled=cooled, excluded=other_occupied
            )
        if picked is not None:
            chosen = pool
            break
    if picked is None or chosen is None:
        raise PoolExhausted(
            "no available addresses in the selected pools", code="ipam.pool_exhausted"
        )
    row = _new_assignment(
        db,
        tenant_id,
        family=family,
        address=picked,
        pool=chosen,
        subscription_id=sub_id,
        subscriber_id=command.subscriber_id,
    )
    if (
        family == c.IPV4
        and command.mark_primary
        and not _has_active_primary(db, tenant_id, sub_id)
    ):
        _mark_primary(db, tenant_id, sub_id, picked.id)
    _assigned_event(db, tenant_id, row, picked)
    db.flush()
    return c.assignment_snapshot(row, picked)


def _prepare_explicit(
    db: Session,
    tenant_id: UUID,
    *,
    address_text: str,
    family: str,
    pool_id: UUID | None,
    subscription_id: UUID,
    occupied: frozenset[OccupiedAddress],
) -> tuple[IPv4Address | IPv6Address, IpPool, IPAssignment | None]:
    """Validate an explicit target; returns (address row, pool, own active row)."""
    parsed = c.parse_v4(address_text) if family == c.IPV4 else c.parse_v6(address_text)
    value = str(parsed)
    model: Any = IPv4Address if family == c.IPV4 else IPv6Address
    column = (
        IPAssignment.ipv4_address_id
        if family == c.IPV4
        else IPAssignment.ipv6_address_id
    )
    row = db.scalar(
        select(model)
        .where(model.tenant_id == tenant_id, model.address == value)
        .with_for_update()
    )
    effective_pool_id = pool_id or (row.pool_id if row is not None else None)
    if effective_pool_id is None:
        raise IpamError("a pool is required for an address outside inventory")
    pool = c.get_pool(db, tenant_id, effective_pool_id, lock=True)
    if row is not None and row.pool_id not in (None, pool.id):
        raise IpamConflict("address belongs to another pool", code="ipam.wrong_pool")
    if not c.pool_is_service_pool(pool, family):
        raise IpamError(
            "target pool is not an active customer pool",
            code="ipam.pool_not_allocatable",
        )
    if not c.pool_contains(db, tenant_id, pool, value):
        raise IpamError("address is outside the pool", code="ipam.address_outside_pool")
    if row is not None and family == c.IPV4 and not _row_is_service_free(row):
        raise IpamConflict(
            "address is reserved or held by a device",
            code="ipam.address_not_serviceable",
        )
    if row is not None and family == c.IPV6 and row.is_reserved:
        raise IpamConflict("address is reserved", code="ipam.address_not_serviceable")
    if family == c.IPV4:
        if value in c.occupied_by_others(occupied, str(subscription_id)):
            raise IpamConflict(
                "address is held by another live service", code="ipam.address_occupied"
            )
        if c.in_routed_block(db, tenant_id, value):
            raise IpamConflict(
                "address is inside an active routed block", code="ipam.in_routed_block"
            )
        if not pool.allow_network_broadcast and c.is_network_or_broadcast(
            db, tenant_id, pool, value
        ):
            raise IpamConflict(
                "network/broadcast addresses are not assignable",
                code="ipam.address_not_serviceable",
            )
        if pool.gateway and c.bare(pool.gateway) == value:
            raise IpamConflict(
                "the pool gateway is not assignable",
                code="ipam.address_not_serviceable",
            )
    own: IPAssignment | None = None
    if row is not None:
        active = db.scalar(
            select(IPAssignment).where(
                IPAssignment.tenant_id == tenant_id,
                column == row.id,
                IPAssignment.is_active.is_(True),
            )
        )
        if active is not None:
            if active.subscription_id != subscription_id:
                raise IpamConflict(
                    "address is already assigned", code="ipam.address_assigned"
                )
            own = active
    else:
        row = model(tenant_id=tenant_id, address=value, pool_id=pool.id)
        db.add(row)
        db.flush()
    if row.pool_id is None:
        row.pool_id = pool.id
        db.flush()
    return row, pool, own


@command
def assign_address(
    db: Session, *, tenant_id: UUID, command: AssignAddress
) -> AssignmentSnapshot:
    family = command.family.value
    address, pool, own = _prepare_explicit(
        db,
        tenant_id,
        address_text=command.address,
        family=family,
        pool_id=command.pool_id,
        subscription_id=command.subscription_id,
        occupied=command.occupied,
    )
    if own is not None:
        return c.assignment_snapshot(own, address)
    row = _new_assignment(
        db,
        tenant_id,
        family=family,
        address=address,
        pool=pool,
        subscription_id=command.subscription_id,
        subscriber_id=command.subscriber_id,
    )
    if family == c.IPV4 and command.mark_primary:
        _mark_primary(db, tenant_id, command.subscription_id, address.id)
    _assigned_event(db, tenant_id, row, address)
    db.flush()
    return c.assignment_snapshot(row, address)


# --- release ------------------------------------------------------------


def _clear_service_marker(db: Session, tenant_id: UUID, address: IPv4Address) -> None:
    allocation = str(address.allocation_type or "").strip().lower()
    if allocation not in SERVICE_ALLOCATION_TYPES:
        return
    if address.is_reserved or address.device_ref is not None:
        return
    if _active_exists(db, tenant_id, IPAssignment.ipv4_address_id, address.id):
        return
    address.allocation_type = None


def _release_row(
    db: Session,
    tenant_id: UUID,
    row: IPAssignment,
    *,
    reason: str,
    at: datetime,
    **extra: object,
) -> None:
    address = _address_of(db, tenant_id, row)
    row.is_active = False
    row.is_primary = False
    row.released_at = at
    row.release_reason = reason
    db.flush()
    if isinstance(address, IPv4Address):
        _clear_service_marker(db, tenant_id, address)
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"subscription:{row.subscription_id}",
        event_type=EVENT_ASSIGNMENT_RELEASED,
        payload={
            "assignment_id": str(row.id),
            "address": address.address,
            "reason": reason,
            **extra,
        },
        occurred_at=at,
    )


@command
def release_assignment(
    db: Session, *, tenant_id: UUID, command: ReleaseAssignment
) -> AssignmentSnapshot:
    row = db.scalar(
        select(IPAssignment)
        .where(
            IPAssignment.tenant_id == tenant_id,
            IPAssignment.id == command.assignment_id,
        )
        .with_for_update()
    )
    if row is None:
        raise IpamNotFound("assignment not found", code="ipam.assignment_not_found")
    if command.expected_active and not row.is_active:
        raise IpamConflict(
            "assignment is not active (expected state mismatch)",
            code="ipam.expected_state",
        )
    if row.is_active:
        _release_row(
            db,
            tenant_id,
            row,
            reason=c.clean(command.reason, "release reason"),
            at=command.now or c.now(),
        )
        db.flush()
    return _snapshot(db, tenant_id, row)


@command
def release_for_subject(
    db: Session, *, tenant_id: UUID, command: ReleaseForSubject
) -> tuple[AssignmentSnapshot, ...]:
    """Release a subject's service addresses; management/device rows are skipped."""
    if (command.subscription_id is None) == (command.subscriber_id is None):
        raise IpamError("exactly one of subscription_id / subscriber_id is required")
    statement = select(IPAssignment).where(
        IPAssignment.tenant_id == tenant_id, IPAssignment.is_active.is_(True)
    )
    if command.subscription_id is not None:
        statement = statement.where(
            IPAssignment.subscription_id == command.subscription_id
        )
    else:
        statement = statement.where(IPAssignment.subscriber_id == command.subscriber_id)
    rows = db.scalars(statement.order_by(IPAssignment.id).with_for_update()).all()
    at = command.now or c.now()
    reason = c.clean(command.reason, "release reason")
    released: list[AssignmentSnapshot] = []
    for row in rows:
        address = _address_of(db, tenant_id, row)
        if isinstance(address, IPv4Address) and not _row_is_service_free_for_release(
            address
        ):
            continue
        _release_row(db, tenant_id, row, reason=reason, at=at)
        released.append(c.assignment_snapshot(row, address))
    db.flush()
    return tuple(released)


def _row_is_service_free_for_release(address: IPv4Address) -> bool:
    """Management/device-held rows are never released as a service IP."""
    return (
        address.device_ref is None
        and str(address.allocation_type or "").lower()
        not in c.NON_SERVICE_ALLOCATION_TYPES
    )


@command
def move_assignment(
    db: Session, *, tenant_id: UUID, command: MoveAssignment
) -> AssignmentSnapshot:
    """Atomic IPv4 move: validate target, release old, assign new, one TX."""
    source_text = str(c.parse_v4(command.from_address))
    target_text = str(c.parse_v4(command.to_address))
    if source_text == target_text:
        raise IpamError("a same-address move is refused", code="ipam.same_address_move")
    old_address = db.scalar(
        select(IPv4Address).where(
            IPv4Address.tenant_id == tenant_id, IPv4Address.address == source_text
        )
    )
    old = (
        db.scalar(
            select(IPAssignment)
            .where(
                IPAssignment.tenant_id == tenant_id,
                IPAssignment.subscription_id == command.subscription_id,
                IPAssignment.ipv4_address_id == old_address.id,
                IPAssignment.is_active.is_(True),
            )
            .with_for_update()
        )
        if old_address is not None
        else None
    )
    if old is None or old_address is None:
        raise IpamConflict(
            "the service does not actively hold the from-address (expected state)",
            code="ipam.expected_state",
        )
    address, pool, own = _prepare_explicit(
        db,
        tenant_id,
        address_text=target_text,
        family=c.IPV4,
        pool_id=command.to_pool_id or old_address.pool_id,
        subscription_id=command.subscription_id,
        occupied=command.occupied,
    )
    if own is not None:
        raise IpamConflict("the service already holds the to-address")
    was_primary = old.is_primary
    at = command.now or c.now()
    subscriber_id = old.subscriber_id
    _release_row(db, tenant_id, old, reason=command.reason, at=at, moved=True)
    row = _new_assignment(
        db,
        tenant_id,
        family=c.IPV4,
        address=address,
        pool=pool,
        subscription_id=command.subscription_id,
        subscriber_id=subscriber_id,
    )
    if was_primary or not _has_active_primary(db, tenant_id, command.subscription_id):
        _mark_primary(db, tenant_id, command.subscription_id, address.id)
    _assigned_event(db, tenant_id, row, address, moved=True)
    c.event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"subscription:{command.subscription_id}",
        event_type=EVENT_ASSIGNMENT_MOVED,
        payload={
            "from_address": source_text,
            "to_address": target_text,
            "reason": command.reason,
        },
        occurred_at=at,
    )
    db.flush()
    return c.assignment_snapshot(row, address)


# --- management allocation (purpose=management, holder=device_ref) ------------


@command
def allocate_management_address(
    db: Session, *, tenant_id: UUID, command: AllocateManagementAddress
) -> AddressSnapshot:
    """Lowest free management host for a device; idempotent per ``device_ref``."""
    pool = c.get_pool(db, tenant_id, command.pool_id, lock=True)
    if pool.ip_version != c.IPV4 or pool.purpose != "management" or not pool.is_active:
        raise IpamError(
            "management allocation needs an active IPv4 management pool",
            code="ipam.pool_not_allocatable",
        )
    held = db.scalar(
        select(IPv4Address).where(
            IPv4Address.tenant_id == tenant_id,
            IPv4Address.pool_id == pool.id,
            IPv4Address.device_ref == command.device_ref,
        )
    )
    if held is not None:
        return c.address_snapshot(held, assigned=False)
    used = {
        row.address
        for row in db.scalars(
            select(IPv4Address).where(
                IPv4Address.tenant_id == tenant_id, IPv4Address.pool_id == pool.id
            )
        )
        if row.is_reserved or row.device_ref is not None
    }
    used |= set(
        db.scalars(
            select(IPv4Address.address)
            .join(
                IPAssignment,
                and_(
                    IPAssignment.tenant_id == IPv4Address.tenant_id,
                    IPAssignment.ipv4_address_id == IPv4Address.id,
                ),
            )
            .where(
                IPv4Address.tenant_id == tenant_id,
                IPv4Address.pool_id == pool.id,
                IPAssignment.is_active.is_(True),
            )
        )
    )
    if pool.gateway:
        used.add(c.bare(pool.gateway))
    for network in c.pool_networks(db, tenant_id, pool):
        if not isinstance(network, ipaddress.IPv4Network):
            continue
        for host in network.hosts():
            text = str(host)
            if text in used:
                continue
            row = db.scalar(
                select(IPv4Address).where(
                    IPv4Address.tenant_id == tenant_id, IPv4Address.address == text
                )
            )
            if row is None:
                row = IPv4Address(tenant_id=tenant_id, address=text, pool_id=pool.id)
                db.add(row)
            elif row.pool_id not in (None, pool.id):
                continue
            row.pool_id = pool.id
            row.is_reserved = True
            row.device_ref = command.device_ref
            row.allocation_type = "management"
            row.notes = command.notes
            db.flush()
            c.event(
                db,
                tenant_id=tenant_id,
                aggregate_ref=f"address:{row.id}",
                event_type="address_reserved",
                payload={"address": text, "kind": "management"},
            )
            db.flush()
            return c.address_snapshot(row, assigned=False)
    raise PoolExhausted("no available management addresses", code="ipam.pool_exhausted")


__all__ = [
    "ON_DEMAND_HOST_SCAN_CAP",
    "allocate_address",
    "allocate_management_address",
    "assign_address",
    "mark_primary",
    "move_assignment",
    "release_assignment",
    "release_for_subject",
]
