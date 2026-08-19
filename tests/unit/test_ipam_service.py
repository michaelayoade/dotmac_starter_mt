"""Core IPAM lifecycle parity independent of product identity."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from ipaddress import ip_address, ip_network
from uuid import UUID, uuid4

import pytest
from dotmac_ipam import (
    AddressFamily,
    AddressState,
    AssignAddress,
    AssignmentState,
    CreateAddressSpace,
    CreatePool,
    IpamConflict,
    IpamError,
    ReleaseAssignment,
    RepairAssignment,
    ReserveAddress,
    assign_address,
    create_address_space,
    create_pool,
    release_assignment,
    repair_assignment,
    reserve_address,
)
from dotmac_ipam.models import (
    Address,
    AddressSpace,
    Assignment,
    IpamEvent,
    Pool,
    UtilizationSnapshot,
)
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_ipam": None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            AddressSpace.__table__,
            Pool.__table__,
            Address.__table__,
            Assignment.__table__,
            UtilizationSnapshot.__table__,
            IpamEvent.__table__,
        ],
    )
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _tenant(db: Session) -> UUID:
    row = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Tenant")
    db.add(row)
    db.flush()
    return row.id


def _pool(db: Session, tenant_id: UUID):
    space = create_address_space(
        db,
        tenant_id=tenant_id,
        command=CreateAddressSpace(
            code="public-v4",
            name="Public IPv4",
            family=AddressFamily.IPV4,
            prefix=ip_network("203.0.113.0/29"),
        ),
    )
    return create_pool(
        db,
        tenant_id=tenant_id,
        command=CreatePool(
            address_space_id=space.id,
            code="served",
            name="Served addresses",
            prefix=ip_network("203.0.113.0/29"),
            allocation_prefix_length=32,
            purpose="subscriber-wan",
        ),
    )


def test_reserve_assign_release_and_reassign_one_address(db: Session) -> None:
    tenant_id = _tenant(db)
    pool = _pool(db, tenant_id)
    reserved = reserve_address(
        db,
        tenant_id=tenant_id,
        command=ReserveAddress(
            pool_id=pool.id,
            address=ip_address("203.0.113.2"),
            purpose="activation",
            reservation_ref="order:100",
        ),
    )
    assert reserved.state is AddressState.RESERVED

    assigned = assign_address(
        db,
        tenant_id=tenant_id,
        command=AssignAddress(
            pool_id=pool.id,
            reservation_id=reserved.id,
            reservation_ref="order:100",
            address=None,
            subject_ref="service:opaque-1",
            assignment_kind="served-ipv4",
            source_ref="activation:100",
        ),
    )
    assert assigned.state is AssignmentState.ACTIVE
    assert assigned.address_id == reserved.id

    released = release_assignment(
        db,
        tenant_id=tenant_id,
        command=ReleaseAssignment(
            assignment_id=assigned.id,
            expected=AssignmentState.ACTIVE,
            reason="service-ended",
            source_ref="termination:100",
        ),
    )
    assert released.state is AssignmentState.RELEASED

    replacement = assign_address(
        db,
        tenant_id=tenant_id,
        command=AssignAddress(
            pool_id=pool.id,
            address=ip_address("203.0.113.2"),
            subject_ref="service:opaque-2",
            assignment_kind="served-ipv4",
            source_ref="activation:101",
        ),
    )
    assert replacement.address_id == reserved.id
    assert replacement.subject_ref == "service:opaque-2"


def test_collision_refuses_second_active_writer(db: Session) -> None:
    tenant_id = _tenant(db)
    pool = _pool(db, tenant_id)
    command = AssignAddress(
        pool_id=pool.id,
        address=ip_address("203.0.113.3"),
        subject_ref="service:opaque-1",
        assignment_kind="served-ipv4",
        source_ref="activation:100",
    )
    assign_address(db, tenant_id=tenant_id, command=command)
    with pytest.raises(IpamConflict):
        assign_address(
            db,
            tenant_id=tenant_id,
            command=AssignAddress(
                pool_id=pool.id,
                address=command.address,
                subject_ref="service:opaque-2",
                assignment_kind="served-ipv4",
                source_ref="activation:101",
            ),
        )


def test_repair_never_repoints_assignment_identity(db: Session) -> None:
    tenant_id = _tenant(db)
    pool = _pool(db, tenant_id)
    assignment = assign_address(
        db,
        tenant_id=tenant_id,
        command=AssignAddress(
            pool_id=pool.id,
            address=None,
            subject_ref="service:opaque-1",
            assignment_kind="served-ipv4",
            source_ref="activation:100",
        ),
    )
    with pytest.raises(IpamConflict, match="never repoints"):
        repair_assignment(
            db,
            tenant_id=tenant_id,
            command=RepairAssignment(
                assignment_id=assignment.id,
                expected_address_id=uuid4(),
                expected_subject_ref=assignment.subject_ref,
                reason="projection-check",
                source_ref="repair:1",
            ),
        )


def test_contract_results_are_frozen_and_pool_must_be_contained(db: Session) -> None:
    tenant_id = _tenant(db)
    pool = _pool(db, tenant_id)
    with pytest.raises(FrozenInstanceError):
        pool.code = "changed"  # type: ignore[misc]

    with pytest.raises(IpamError, match="contained"):
        create_pool(
            db,
            tenant_id=tenant_id,
            command=CreatePool(
                address_space_id=pool.address_space_id,
                code="outside",
                name="Outside",
                prefix=ip_network("198.51.100.0/24"),
                allocation_prefix_length=32,
                purpose="invalid",
            ),
        )
