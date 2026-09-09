"""Flush-only IPAM owner; transaction boundaries remain in the caller."""

from __future__ import annotations

from datetime import UTC, datetime
from ipaddress import IPv4Network, ip_address, ip_network
from itertools import islice
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_ipam.contracts import (
    AddressFamily,
    AddressLookup,
    AddressSnapshot,
    AddressSpaceSnapshot,
    AddressState,
    AssignAddress,
    AssignmentLookup,
    AssignmentSnapshot,
    AssignmentState,
    CreateAddressSpace,
    CreatePool,
    IPAddress,
    PoolSnapshot,
    RecordUtilization,
    ReleaseAssignment,
    RepairAssignment,
    RepairReport,
    ReserveAddress,
    UtilizationQuery,
)
from dotmac_ipam.contracts import (
    UtilizationSnapshot as UtilizationResult,
)
from dotmac_ipam.models import (
    Address,
    AddressSpace,
    Assignment,
    IpamEvent,
    Pool,
    UtilizationSnapshot,
)


class IpamError(ValueError):
    """Base error for a refused IPAM decision."""


class IpamNotFound(IpamError):
    """A tenant-local IPAM entity was not found."""


class IpamConflict(IpamError):
    """An identity, reservation, assignment, or expected state conflicts."""


class PoolExhausted(IpamError):
    """No allocatable address remains in the selected pool."""


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise IpamError(f"{label} must not be blank")
    return cleaned


def _event(
    db: Session,
    *,
    tenant_id: UUID,
    aggregate_ref: str,
    event_type: str,
    payload: dict[str, str],
    occurred_at: datetime | None = None,
) -> None:
    db.add(
        IpamEvent(
            tenant_id=tenant_id,
            aggregate_ref=aggregate_ref,
            event_type=event_type,
            payload=payload,
            occurred_at=occurred_at or datetime.now(UTC),
        )
    )


def _space(
    db: Session, tenant_id: UUID, space_id: UUID, *, lock: bool = False
) -> AddressSpace:
    statement = select(AddressSpace).where(
        AddressSpace.tenant_id == tenant_id, AddressSpace.id == space_id
    )
    if lock:
        statement = statement.with_for_update()
    row = db.scalar(statement)
    if row is None:
        raise IpamNotFound("address space not found")
    return row


def _pool(db: Session, tenant_id: UUID, pool_id: UUID) -> Pool:
    row = db.scalar(
        select(Pool)
        .where(Pool.tenant_id == tenant_id, Pool.id == pool_id)
        .with_for_update()
    )
    if row is None:
        raise IpamNotFound("pool not found")
    return row


def _assignment(db: Session, tenant_id: UUID, assignment_id: UUID) -> Assignment:
    row = db.scalar(
        select(Assignment)
        .where(Assignment.tenant_id == tenant_id, Assignment.id == assignment_id)
        .with_for_update()
    )
    if row is None:
        raise IpamNotFound("assignment not found")
    return row


def _address(db: Session, tenant_id: UUID, address_id: UUID) -> Address:
    row = db.scalar(
        select(Address)
        .where(Address.tenant_id == tenant_id, Address.id == address_id)
        .with_for_update()
    )
    if row is None:
        raise IpamNotFound("address not found")
    return row


def _space_snapshot(row: AddressSpace) -> AddressSpaceSnapshot:
    return AddressSpaceSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        name=row.name,
        family=AddressFamily(row.family),
        prefix=ip_network(row.prefix),
        routing_domain_ref=row.routing_domain_ref,
        created_at=row.created_at,
    )


def _pool_snapshot(row: Pool) -> PoolSnapshot:
    return PoolSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        address_space_id=row.address_space_id,
        code=row.code,
        name=row.name,
        prefix=ip_network(row.prefix),
        allocation_prefix_length=row.allocation_prefix_length,
        purpose=row.purpose,
        created_at=row.created_at,
    )


def _address_snapshot(row: Address) -> AddressSnapshot:
    return AddressSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        pool_id=row.pool_id,
        address=ip_address(row.address),
        state=AddressState(row.state),
        reservation_purpose=row.reservation_purpose,
        reservation_ref=row.reservation_ref,
        reserved_until=row.reserved_until,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _assignment_snapshot(row: Assignment) -> AssignmentSnapshot:
    return AssignmentSnapshot(
        id=row.id,
        tenant_id=row.tenant_id,
        address_id=row.address_id,
        subject_ref=row.subject_ref,
        assignment_kind=row.assignment_kind,
        source_ref=row.source_ref,
        state=AssignmentState(row.state),
        assigned_at=row.assigned_at,
        valid_until=row.valid_until,
        released_at=row.released_at,
        release_reason=row.release_reason,
    )


def _utilization_snapshot(row: UtilizationSnapshot) -> UtilizationResult:
    return UtilizationResult(
        id=row.id,
        tenant_id=row.tenant_id,
        pool_id=row.pool_id,
        observed_at=row.observed_at,
        total=row.total,
        available=row.available,
        reserved=row.reserved,
        assigned=row.assigned,
        source_ref=row.source_ref,
    )


def create_address_space(
    db: Session, *, tenant_id: UUID, command: CreateAddressSpace
) -> AddressSpaceSnapshot:
    prefix = ip_network(str(command.prefix), strict=True)
    family = AddressFamily.IPV4 if prefix.version == 4 else AddressFamily.IPV6
    if command.family is not family:
        raise IpamError("address family does not match prefix")
    row = AddressSpace(
        tenant_id=tenant_id,
        code=_clean(command.code, "space code"),
        name=_clean(command.name, "space name"),
        family=family.value,
        prefix=str(prefix),
        routing_domain_ref=(
            _clean(command.routing_domain_ref, "routing domain reference")
            if command.routing_domain_ref is not None
            else None
        ),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id=tenant_id,
                aggregate_ref=f"address-space:{row.id}",
                event_type="address_space_created",
                payload={"prefix": row.prefix},
            )
            db.flush()
    except IntegrityError as exc:
        raise IpamConflict("address space code already exists") from exc
    return _space_snapshot(row)


def create_pool(db: Session, *, tenant_id: UUID, command: CreatePool) -> PoolSnapshot:
    space = _space(db, tenant_id, command.address_space_id, lock=True)
    prefix = ip_network(str(command.prefix), strict=True)
    space_prefix = ip_network(space.prefix)
    contained = (
        isinstance(space_prefix, IPv4Network) and prefix.subnet_of(space_prefix)
        if isinstance(prefix, IPv4Network)
        else not isinstance(space_prefix, IPv4Network)
        and prefix.subnet_of(space_prefix)
    )
    if not contained:
        raise IpamError("pool prefix must be contained by its address space")
    existing_prefixes = tuple(
        ip_network(value)
        for value in db.scalars(
            select(Pool.prefix).where(
                Pool.tenant_id == tenant_id,
                Pool.address_space_id == space.id,
            )
        )
    )
    if any(prefix.overlaps(existing) for existing in existing_prefixes):
        raise IpamConflict("pool prefix overlaps an existing pool")
    if not prefix.prefixlen <= command.allocation_prefix_length <= prefix.max_prefixlen:
        raise IpamError("allocation prefix length is outside the pool prefix")
    row = Pool(
        tenant_id=tenant_id,
        address_space_id=space.id,
        code=_clean(command.code, "pool code"),
        name=_clean(command.name, "pool name"),
        prefix=str(prefix),
        allocation_prefix_length=command.allocation_prefix_length,
        purpose=_clean(command.purpose, "pool purpose"),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
            _event(
                db,
                tenant_id=tenant_id,
                aggregate_ref=f"pool:{row.id}",
                event_type="pool_created",
                payload={"prefix": row.prefix},
            )
            db.flush()
    except IntegrityError as exc:
        raise IpamConflict("pool code already exists") from exc
    return _pool_snapshot(row)


def _address_for_allocation(
    db: Session,
    *,
    tenant_id: UUID,
    pool: Pool,
    requested: IPAddress | None,
) -> Address:
    network = ip_network(pool.prefix)
    if requested is not None:
        candidate = ip_address(str(requested))
        if candidate not in network:
            raise IpamError("address is outside the pool prefix")
        row = db.scalar(
            select(Address)
            .where(
                Address.tenant_id == tenant_id,
                Address.pool_id == pool.id,
                Address.address == str(candidate),
            )
            .with_for_update()
        )
        if row is None:
            row = Address(
                tenant_id=tenant_id,
                pool_id=pool.id,
                address=str(candidate),
                state=AddressState.AVAILABLE.value,
            )
            db.add(row)
            db.flush()
        return row

    used = set(
        db.scalars(
            select(Address.address).where(
                Address.tenant_id == tenant_id, Address.pool_id == pool.id
            )
        )
    )
    # At most len(used) + 1 candidates are needed to find a gap. This keeps a
    # sparse IPv6 pool bounded by materialized rows, never by address-space size.
    for next_candidate in islice(network.hosts(), len(used) + 1):
        if str(next_candidate) not in used:
            row = Address(
                tenant_id=tenant_id,
                pool_id=pool.id,
                address=str(next_candidate),
                state=AddressState.AVAILABLE.value,
            )
            db.add(row)
            db.flush()
            return row
    raise PoolExhausted("pool contains no available address")


def reserve_address(
    db: Session, *, tenant_id: UUID, command: ReserveAddress
) -> AddressSnapshot:
    pool = _pool(db, tenant_id, command.pool_id)
    row = _address_for_allocation(
        db, tenant_id=tenant_id, pool=pool, requested=command.address
    )
    if AddressState(row.state) is not AddressState.AVAILABLE:
        raise IpamConflict("address is not available")
    row.state = AddressState.RESERVED.value
    row.reservation_purpose = _clean(command.purpose, "reservation purpose")
    row.reservation_ref = _clean(command.reservation_ref, "reservation reference")
    row.reserved_until = command.expires_at
    row.updated_at = datetime.now(UTC)
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"address:{row.id}",
        event_type="address_reserved",
        payload={"reservation_ref": row.reservation_ref},
    )
    db.flush()
    return _address_snapshot(row)


def assign_address(
    db: Session, *, tenant_id: UUID, command: AssignAddress
) -> AssignmentSnapshot:
    pool = _pool(db, tenant_id, command.pool_id)
    if command.reservation_id is not None:
        address_row = _address(db, tenant_id, command.reservation_id)
        if address_row.pool_id != pool.id:
            raise IpamConflict("reservation does not belong to the selected pool")
    else:
        address_row = _address_for_allocation(
            db, tenant_id=tenant_id, pool=pool, requested=command.address
        )
    if AddressState(address_row.state) not in {
        AddressState.AVAILABLE,
        AddressState.RESERVED,
    }:
        raise IpamConflict("address is already assigned or unavailable")
    if AddressState(address_row.state) is AddressState.RESERVED:
        if command.reservation_ref is None:
            raise IpamConflict("reserved address requires its reservation reference")
        if address_row.reservation_ref != command.reservation_ref:
            raise IpamConflict("reservation reference does not own this address")
        if (
            address_row.reserved_until is not None
            and address_row.reserved_until <= datetime.now(UTC)
        ):
            raise IpamConflict("reservation has expired")
    elif command.reservation_ref is not None:
        raise IpamConflict("reservation reference supplied for an available address")
    now = datetime.now(UTC)
    assignment = Assignment(
        tenant_id=tenant_id,
        address_id=address_row.id,
        subject_ref=_clean(command.subject_ref, "subject reference"),
        assignment_kind=_clean(command.assignment_kind, "assignment kind"),
        source_ref=_clean(command.source_ref, "source reference"),
        state=AssignmentState.ACTIVE.value,
        assigned_at=now,
        valid_until=command.valid_until,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            address_row.state = AddressState.ASSIGNED.value
            address_row.reservation_purpose = None
            address_row.reservation_ref = None
            address_row.reserved_until = None
            address_row.updated_at = now
            db.add(assignment)
            db.flush()
            _event(
                db,
                tenant_id=tenant_id,
                aggregate_ref=f"assignment:{assignment.id}",
                event_type="address_assigned",
                payload={
                    "address_id": str(address_row.id),
                    "subject_ref": assignment.subject_ref,
                },
                occurred_at=now,
            )
            db.flush()
    except IntegrityError as exc:
        raise IpamConflict("address already has an active assignment") from exc
    return _assignment_snapshot(assignment)


def release_assignment(
    db: Session, *, tenant_id: UUID, command: ReleaseAssignment
) -> AssignmentSnapshot:
    row = _assignment(db, tenant_id, command.assignment_id)
    current = AssignmentState(row.state)
    if current is not command.expected or current is not AssignmentState.ACTIVE:
        raise IpamConflict("assignment state changed")
    address_row = _address(db, tenant_id, row.address_id)
    now = datetime.now(UTC)
    row.state = AssignmentState.RELEASED.value
    row.released_at = now
    row.release_reason = _clean(command.reason, "release reason")
    source_ref = _clean(command.source_ref, "source reference")
    address_row.state = AddressState.AVAILABLE.value
    address_row.updated_at = now
    _event(
        db,
        tenant_id=tenant_id,
        aggregate_ref=f"assignment:{row.id}",
        event_type="assignment_released",
        payload={"reason": row.release_reason, "source_ref": source_ref},
        occurred_at=now,
    )
    db.flush()
    return _assignment_snapshot(row)


def record_utilization(
    db: Session, *, tenant_id: UUID, command: RecordUtilization
) -> UtilizationResult:
    _pool(db, tenant_id, command.pool_id)
    values = (command.total, command.available, command.reserved, command.assigned)
    if any(value < 0 for value in values):
        raise IpamError("utilization counts cannot be negative")
    if command.available + command.reserved + command.assigned != command.total:
        raise IpamError("utilization components must equal total")
    row = UtilizationSnapshot(
        tenant_id=tenant_id,
        pool_id=command.pool_id,
        observed_at=command.observed_at,
        total=command.total,
        available=command.available,
        reserved=command.reserved,
        assigned=command.assigned,
        source_ref=_clean(command.source_ref, "source reference"),
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise IpamConflict("utilization observation already exists") from exc
    return _utilization_snapshot(row)


def repair_assignment(
    db: Session, *, tenant_id: UUID, command: RepairAssignment
) -> RepairReport:
    source_ref = _clean(command.source_ref, "source reference")
    row = _assignment(db, tenant_id, command.assignment_id)
    if row.address_id != command.expected_address_id:
        raise IpamConflict("repair never repoints an assignment")
    if row.subject_ref != command.expected_subject_ref:
        raise IpamConflict("assignment subject differs from repair expectation")
    address_row = _address(db, tenant_id, row.address_id)
    changed = False
    if (
        AssignmentState(row.state) is AssignmentState.ACTIVE
        and AddressState(address_row.state) is not AddressState.ASSIGNED
    ):
        address_row.state = AddressState.ASSIGNED.value
        address_row.updated_at = datetime.now(UTC)
        changed = True
    if changed:
        _event(
            db,
            tenant_id=tenant_id,
            aggregate_ref=f"assignment:{row.id}",
            event_type="assignment_repaired",
            payload={
                "reason": _clean(command.reason, "repair reason"),
                "source_ref": source_ref,
            },
        )
        db.flush()
    return RepairReport(
        assignment=_assignment_snapshot(row),
        changed=changed,
        previous_address_id=None,
        reason=_clean(command.reason, "repair reason"),
    )


def lookup_address(
    db: Session, *, tenant_id: UUID, query: AddressLookup
) -> AddressSnapshot | None:
    row = db.scalar(
        select(Address)
        .join(
            Pool, (Pool.tenant_id == Address.tenant_id) & (Pool.id == Address.pool_id)
        )
        .where(
            Address.tenant_id == tenant_id,
            Pool.address_space_id == query.address_space_id,
            Address.address == str(query.address),
        )
    )
    return _address_snapshot(row) if row is not None else None


def lookup_assignments(
    db: Session, *, tenant_id: UUID, query: AssignmentLookup
) -> tuple[AssignmentSnapshot, ...]:
    statement = select(Assignment).where(Assignment.tenant_id == tenant_id)
    if query.assignment_id is not None:
        statement = statement.where(Assignment.id == query.assignment_id)
    if query.subject_ref is not None:
        statement = statement.where(Assignment.subject_ref == query.subject_ref)
    if query.active_only:
        statement = statement.where(Assignment.state == AssignmentState.ACTIVE.value)
    return tuple(_assignment_snapshot(row) for row in db.scalars(statement))


def lookup_utilization(
    db: Session, *, tenant_id: UUID, query: UtilizationQuery
) -> UtilizationResult | None:
    statement = select(UtilizationSnapshot).where(
        UtilizationSnapshot.tenant_id == tenant_id,
        UtilizationSnapshot.pool_id == query.pool_id,
    )
    if query.as_of is not None:
        statement = statement.where(UtilizationSnapshot.observed_at <= query.as_of)
    row = db.scalar(statement.order_by(UtilizationSnapshot.observed_at.desc()).limit(1))
    return _utilization_snapshot(row) if row is not None else None


__all__ = [
    "IpamConflict",
    "IpamError",
    "IpamNotFound",
    "PoolExhausted",
    "assign_address",
    "create_address_space",
    "create_pool",
    "lookup_address",
    "lookup_assignments",
    "lookup_utilization",
    "record_utilization",
    "release_assignment",
    "repair_assignment",
    "reserve_address",
]
