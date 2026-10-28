"""Allocator, assignment, release and move parity (synthetic data only).

Moved/adapted from Sub's ``test_ipv4_on_demand_allocator``,
``test_ipv4_allocator_collision_guards``, ``test_ipam_collision_prevention``,
``test_ipv4_primary_assignment_marker`` and ``test_ip_lifecycle`` release
parts, onto opaque UUID subjects and the module command surface.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_ipam import (
    AddressFamily,
    AllocateAddress,
    AllocateManagementAddress,
    AssignAddress,
    Cooldown,
    IpamConflict,
    IpamError,
    MarkPrimary,
    MoveAssignment,
    OccupiedAddress,
    PoolExhausted,
    PoolPurpose,
    ReleaseAssignment,
    ReleaseForSubject,
    ReservationKind,
    ReserveAddress,
    UpdatePool,
    UpsertRoutedPrefix,
    allocate_address,
    allocate_management_address,
    assign_address,
    command_scope,
    mark_primary,
    move_assignment,
    release_assignment,
    release_for_subject,
    reserve_address,
    update_pool,
    upsert_routed_prefix,
)
from dotmac_ipam.models import IPAssignment, IPv4Address
from sqlalchemy import select

from tests.unit.ipam_support import (
    make_session,
    seed_pool,
    seed_tenant,
    seed_v4,
    seed_v6,
)

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def env():
    db = make_session()
    tenant = seed_tenant(db)
    try:
        yield db, tenant
    finally:
        db.close()


def alloc(db, tenant, pool_ids, sub=None, subscriber=None, **kw):
    return allocate_address(
        db,
        tenant_id=tenant,
        command=AllocateAddress(
            pool_ids=tuple(pool_ids),
            subscription_id=sub or uuid4(),
            subscriber_id=subscriber or uuid4(),
            **kw,
        ),
    )


def active(db, **where):
    stmt = select(IPAssignment).where(IPAssignment.is_active.is_(True))
    return db.scalars(stmt).all()


# --- selection ------------------------------------------------------------


def test_lowest_free_is_numeric_not_string_order(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    seed_v4(db, tenant, pool, "192.0.2.10", "192.0.2.2", "192.0.2.9")
    assert alloc(db, tenant, [pool]).address == "192.0.2.2"


def test_on_demand_materialises_the_lowest_host_skipping_network(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/29")
    first = alloc(db, tenant, [pool])
    second = alloc(db, tenant, [pool])
    assert (first.address, second.address) == ("192.0.2.1", "192.0.2.2")
    assert db.scalar(select(IPv4Address).where(IPv4Address.address == "192.0.2.1"))


def test_gateway_is_never_allocated(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/29", gateway="192.0.2.1")
    assert alloc(db, tenant, [pool]).address == "192.0.2.2"
    seed_v4(db, tenant, pool, "192.0.2.1")  # even as a materialised free row
    assert alloc(db, tenant, [pool]).address == "192.0.2.3"


def test_network_and_broadcast_only_when_the_pool_opts_in(env) -> None:
    db, tenant = env
    closed = seed_pool(db, tenant, cidr="198.51.100.0/30")
    assert {
        alloc(db, tenant, [closed]).address,
        alloc(db, tenant, [closed]).address,
    } == {
        "198.51.100.1",
        "198.51.100.2",
    }
    with pytest.raises(PoolExhausted):
        alloc(db, tenant, [closed])
    opened = seed_pool(db, tenant, cidr="203.0.113.0/30", allow_network_broadcast=True)
    assert alloc(db, tenant, [opened]).address == "203.0.113.0"


def test_reserved_device_and_management_rows_are_never_allocated(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    reserve_address(
        db, tenant_id=tenant, command=ReserveAddress(pool_id=pool, address="192.0.2.1")
    )
    reserve_address(
        db,
        tenant_id=tenant,
        command=ReserveAddress(
            pool_id=pool,
            address="192.0.2.2",
            kind=ReservationKind.DEVICE,
            device_ref=uuid4(),
        ),
    )
    reserve_address(
        db,
        tenant_id=tenant,
        command=ReserveAddress(
            pool_id=pool, address="192.0.2.3", kind=ReservationKind.MANAGEMENT
        ),
    )
    assert alloc(db, tenant, [pool]).address == "192.0.2.4"


def test_routed_block_hosts_are_excluded(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/27")
    upsert_routed_prefix(
        db,
        tenant_id=tenant,
        command=UpsertRoutedPrefix(subscriber_id=uuid4(), cidr="192.0.2.0/30"),
    )
    assert alloc(db, tenant, [pool]).address == "192.0.2.4"


def test_blocks_define_the_candidate_ranges_when_present(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24", blocks=["192.0.2.128/30"])
    assert alloc(db, tenant, [pool]).address == "192.0.2.129"


def test_two_services_of_one_subscriber_get_separate_addresses(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    subscriber = uuid4()
    one = alloc(db, tenant, [pool], subscriber=subscriber)
    two = alloc(db, tenant, [pool], subscriber=subscriber)
    assert one.address != two.address
    assert one.subscription_id != two.subscription_id
    assert one.is_primary and two.is_primary  # one primary PER SERVICE


def test_allocation_is_idempotent_per_service(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    sub = uuid4()
    first = alloc(db, tenant, [pool], sub=sub)
    again = alloc(db, tenant, [pool], sub=sub)
    assert again.id == first.id
    assert len(active(db)) == 1


def test_pools_are_tried_in_order_and_exhaustion_raises(env) -> None:
    db, tenant = env
    small = seed_pool(db, tenant, cidr="192.0.2.0/30")  # hosts .1 .2
    big = seed_pool(db, tenant, cidr="198.51.100.0/29")
    got = [alloc(db, tenant, [small, big]).address for _ in range(3)]
    assert got[:2] == ["192.0.2.1", "192.0.2.2"]
    assert got[2] == "198.51.100.1"
    tiny = seed_pool(db, tenant, cidr="203.0.113.0/30")
    for _ in range(2):
        alloc(db, tenant, [tiny])
    with pytest.raises(PoolExhausted):
        alloc(db, tenant, [tiny])


def test_non_customer_inactive_or_wrong_family_pools_are_refused(env) -> None:
    db, tenant = env
    infra = seed_pool(
        db, tenant, cidr="192.0.2.0/28", purpose=PoolPurpose.INFRASTRUCTURE
    )
    with pytest.raises(IpamError, match="customer"):
        alloc(db, tenant, [infra])
    pool = seed_pool(db, tenant, cidr="198.51.100.0/28")
    update_pool(db, tenant_id=tenant, command=UpdatePool(pool_id=pool, is_active=False))
    with pytest.raises(IpamError):
        alloc(db, tenant, [pool])
    v6 = seed_pool(
        db, tenant, cidr="2001:db8::/64", family=AddressFamily.IPV6, name="v6"
    )
    with pytest.raises(IpamError):
        alloc(db, tenant, [v6], family=AddressFamily.IPV4)
    with pytest.raises(IpamError, match="at least one pool"):
        alloc(db, tenant, [])


def test_ipv6_allocates_from_materialised_rows_only(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="2001:db8::/64", family=AddressFamily.IPV6)
    with pytest.raises(PoolExhausted):
        alloc(db, tenant, [pool], family=AddressFamily.IPV6)
    seed_v6(db, tenant, pool, "2001:db8::10")
    got = alloc(db, tenant, [pool], family=AddressFamily.IPV6)
    assert got.address == "2001:db8::10" and not got.is_primary


# --- occupancy / cool-down / reactivation -------------------------------------


def test_occupied_addresses_held_by_others_are_skipped(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    sub = uuid4()
    occupied = frozenset(
        {
            OccupiedAddress("192.0.2.1", "someone-else", "session"),
            OccupiedAddress("192.0.2.2/32", "other-service", "served-column"),
            OccupiedAddress("192.0.2.3", str(sub), "session"),  # own: not blocking
        }
    )
    assert alloc(db, tenant, [pool], sub=sub, occupied=occupied).address == "192.0.2.3"


def test_release_then_reallocate_restores_the_same_address(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    sub = uuid4()
    first = alloc(db, tenant, [pool], sub=sub)
    release_assignment(
        db,
        tenant_id=tenant,
        command=ReleaseAssignment(first.id, reason="suspend", now=T0),
    )
    cooldown = Cooldown(window_seconds=3600, now=T0 + timedelta(seconds=5))
    back = alloc(db, tenant, [pool], sub=sub, cooldown=cooldown)
    assert back.id == first.id and back.address == first.address
    assert back.is_active and back.released_at is None and back.is_primary


def test_cooldown_keeps_a_just_released_address_from_other_subjects(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    first = alloc(db, tenant, [pool])
    release_assignment(
        db,
        tenant_id=tenant,
        command=ReleaseAssignment(first.id, reason="cancel", now=T0),
    )
    inside = Cooldown(3600, T0 + timedelta(seconds=60))
    assert alloc(db, tenant, [pool], cooldown=inside).address != first.address
    outside = Cooldown(3600, T0 + timedelta(hours=2))
    assert alloc(db, tenant, [pool], cooldown=outside).address == first.address


def test_without_a_policy_a_released_address_is_immediately_reusable(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    first = alloc(db, tenant, [pool])
    release_assignment(
        db,
        tenant_id=tenant,
        command=ReleaseAssignment(first.id, reason="cancel", now=T0),
    )
    assert alloc(db, tenant, [pool]).address == first.address


def test_reactivation_is_refused_when_the_address_changed_role(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    sub = uuid4()
    first = alloc(db, tenant, [pool], sub=sub)
    release_assignment(
        db, tenant_id=tenant, command=ReleaseAssignment(first.id, reason="x", now=T0)
    )
    reserve_address(
        db,
        tenant_id=tenant,
        command=ReserveAddress(pool_id=pool, address=first.address),
    )
    fresh = alloc(db, tenant, [pool], sub=sub)
    assert fresh.address != first.address and fresh.id != first.id


# --- explicit assign ----------------------------------------------------


def test_explicit_assign_creates_inventory_and_is_idempotent(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    sub = uuid4()
    cmd = AssignAddress(
        address="192.0.2.77", subscription_id=sub, subscriber_id=uuid4(), pool_id=pool
    )
    one = assign_address(db, tenant_id=tenant, command=cmd)
    two = assign_address(db, tenant_id=tenant, command=cmd)
    assert one.id == two.id and one.is_primary and one.prefix_length == 24


@pytest.mark.parametrize(
    "setup,address,message",
    [
        ("none", "198.51.100.5", "outside the pool"),
        ("routed", "192.0.2.2", "routed"),
        ("reserved", "192.0.2.5", "reserved"),
        ("gateway", "192.0.2.254", "gateway"),
        ("broadcast", "192.0.2.255", "network/broadcast"),
        ("occupied", "192.0.2.9", "another live service"),
    ],
)
def test_explicit_assign_refusals(env, setup, address, message) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24", gateway="192.0.2.254")
    occupied = frozenset()
    if setup == "routed":
        upsert_routed_prefix(
            db,
            tenant_id=tenant,
            command=UpsertRoutedPrefix(subscriber_id=uuid4(), cidr="192.0.2.0/28"),
        )
    if setup == "reserved":
        reserve_address(
            db, tenant_id=tenant, command=ReserveAddress(pool_id=pool, address=address)
        )
    if setup == "occupied":
        occupied = frozenset({OccupiedAddress(address, "other", "session")})
    with pytest.raises(IpamError, match=message):
        assign_address(
            db,
            tenant_id=tenant,
            command=AssignAddress(
                address=address,
                subscription_id=uuid4(),
                subscriber_id=None,
                pool_id=pool,
                occupied=occupied,
            ),
        )


def test_an_address_held_by_another_service_cannot_be_assigned(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    held = alloc(db, tenant, [pool])
    with pytest.raises(IpamConflict, match="already assigned"):
        assign_address(
            db,
            tenant_id=tenant,
            command=AssignAddress(
                address=held.address, subscription_id=uuid4(), subscriber_id=None
            ),
        )


# --- primary marker ----------------------------------------------------------


def test_exactly_one_active_primary_per_service(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    sub, subscriber = uuid4(), uuid4()
    a = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.10", sub, subscriber, pool_id=pool),
    )
    b = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.11", sub, subscriber, pool_id=pool),
    )
    rows = db.scalars(
        select(IPAssignment).where(IPAssignment.subscription_id == sub)
    ).all()
    assert {r.id: r.is_primary for r in rows} == {a.id: False, b.id: True}
    assert mark_primary(
        db, tenant_id=tenant, command=MarkPrimary(sub, rows[0].ipv4_address_id)
    )
    db.refresh(rows[0]), db.refresh(rows[1])
    assert sum(r.is_primary for r in rows) == 1


def test_mark_primary_refuses_an_address_the_service_does_not_hold(env) -> None:
    db, tenant = env
    assert not mark_primary(db, tenant_id=tenant, command=MarkPrimary(uuid4(), uuid4()))
    assert not mark_primary(db, tenant_id=tenant, command=MarkPrimary(uuid4(), None))


def test_database_refuses_two_active_primaries_for_one_service(env) -> None:
    from sqlalchemy.exc import IntegrityError

    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    ids = seed_v4(db, tenant, pool, "192.0.2.20", "192.0.2.21")
    sub = uuid4()
    with command_scope():
        for address_id in ids:
            db.add(
                IPAssignment(
                    tenant_id=tenant,
                    subscription_id=sub,
                    ip_version="ipv4",
                    ipv4_address_id=address_id,
                    is_active=True,
                    is_primary=True,
                )
            )
        with pytest.raises(IntegrityError):
            db.flush()
    db.rollback()


def test_database_refuses_two_active_rows_on_one_address(env) -> None:
    from sqlalchemy.exc import IntegrityError

    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    (address_id,) = seed_v4(db, tenant, pool, "192.0.2.30")
    with command_scope():
        for _ in range(2):
            db.add(
                IPAssignment(
                    tenant_id=tenant,
                    subscription_id=uuid4(),
                    ip_version="ipv4",
                    ipv4_address_id=address_id,
                    is_active=True,
                )
            )
        with pytest.raises(IntegrityError):
            db.flush()
    db.rollback()


# --- release ----------------------------------------------------------------


def test_release_is_expected_state_checked_and_records_why(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    got = alloc(db, tenant, [pool])
    released = release_assignment(
        db, tenant_id=tenant, command=ReleaseAssignment(got.id, reason="cancel", now=T0)
    )
    assert not released.is_active and not released.is_primary
    assert released.release_reason == "cancel"
    with pytest.raises(IpamConflict, match="expected state"):
        release_assignment(
            db, tenant_id=tenant, command=ReleaseAssignment(got.id, reason="again")
        )
    again = release_assignment(
        db,
        tenant_id=tenant,
        command=ReleaseAssignment(got.id, reason="again", expected_active=False),
    )
    assert not again.is_active
    with pytest.raises(IpamError, match="blank"):
        release_assignment(
            db,
            tenant_id=tenant,
            command=ReleaseAssignment(alloc(db, tenant, [pool]).id, reason="  "),
        )


def test_release_clears_a_stale_service_allocation_marker(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    got = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.40", uuid4(), None, pool_id=pool),
    )
    row = db.scalar(select(IPv4Address).where(IPv4Address.address == "192.0.2.40"))
    with command_scope():
        row.allocation_type = "static"
        db.flush()
    release_assignment(
        db, tenant_id=tenant, command=ReleaseAssignment(got.id, reason="x", now=T0)
    )
    db.refresh(row)
    assert row.allocation_type is None


def test_release_for_subject_skips_management_and_device_rows(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    sub, subscriber = uuid4(), uuid4()
    svc = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.50", sub, subscriber, pool_id=pool),
    )
    mgmt = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.51", sub, subscriber, pool_id=pool),
    )
    row = db.scalar(select(IPv4Address).where(IPv4Address.address == "192.0.2.51"))
    with command_scope():
        row.allocation_type = "management"
        db.flush()
    out = release_for_subject(
        db,
        tenant_id=tenant,
        command=ReleaseForSubject(reason="terminal", subscription_id=sub, now=T0),
    )
    assert [r.id for r in out] == [svc.id]
    assert db.get(IPAssignment, mgmt.id).is_active
    # by subscriber grain too
    assert (
        release_for_subject(
            db,
            tenant_id=tenant,
            command=ReleaseForSubject(reason="gone", subscriber_id=subscriber),
        )
        == ()
    )
    with pytest.raises(IpamError):
        release_for_subject(db, tenant_id=tenant, command=ReleaseForSubject(reason="x"))


# --- move ------------------------------------------------------------------


def test_move_is_atomic_and_keeps_the_primary(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    sub = uuid4()
    old = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.60", sub, uuid4(), pool_id=pool),
    )
    new = move_assignment(
        db,
        tenant_id=tenant,
        command=MoveAssignment(sub, "192.0.2.60", "192.0.2.61", "replace", now=T0),
    )
    assert new.address == "192.0.2.61" and new.is_primary and new.is_active
    assert not db.get(IPAssignment, old.id).is_active
    from dotmac_ipam import events

    types = [
        e.event_type
        for e in events(db, tenant_id=tenant, aggregate_ref=f"subscription:{sub}")
    ]
    assert types.count("AssignmentMoved") == 1
    assert "AssignmentReleased" in types and types.count("AddressAssigned") == 2


def test_move_refuses_same_address_unheld_source_and_bad_target_without_side_effects(
    env,
) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    sub = uuid4()
    old = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.70", sub, uuid4(), pool_id=pool),
    )
    with pytest.raises(IpamError, match="same-address"):
        move_assignment(
            db,
            tenant_id=tenant,
            command=MoveAssignment(sub, "192.0.2.70", "192.0.2.70", "x"),
        )
    with pytest.raises(IpamConflict, match="expected state"):
        move_assignment(
            db,
            tenant_id=tenant,
            command=MoveAssignment(sub, "192.0.2.99", "192.0.2.71", "x"),
        )
    with pytest.raises(IpamError, match="outside the pool"):
        move_assignment(
            db,
            tenant_id=tenant,
            command=MoveAssignment(sub, "192.0.2.70", "198.51.100.1", "x"),
        )
    other = alloc(db, tenant, [pool])
    with pytest.raises(IpamConflict, match="already assigned"):
        move_assignment(
            db,
            tenant_id=tenant,
            command=MoveAssignment(sub, "192.0.2.70", other.address, "x"),
        )
    assert db.get(IPAssignment, old.id).is_active  # untouched by every refusal


# --- management allocation -----------------------------------------------------


def test_management_allocation_is_idempotent_per_device_and_skips_gateway(env) -> None:
    db, tenant = env
    pool = seed_pool(
        db,
        tenant,
        cidr="192.0.2.0/29",
        purpose=PoolPurpose.MANAGEMENT,
        gateway="192.0.2.1",
    )
    dev_a, dev_b = uuid4(), uuid4()
    a = allocate_management_address(
        db, tenant_id=tenant, command=AllocateManagementAddress(pool, dev_a)
    )
    b = allocate_management_address(
        db, tenant_id=tenant, command=AllocateManagementAddress(pool, dev_b)
    )
    again = allocate_management_address(
        db, tenant_id=tenant, command=AllocateManagementAddress(pool, dev_a)
    )
    assert (a.address, b.address) == ("192.0.2.2", "192.0.2.3")
    assert again.id == a.id and a.allocation_type == "management" and a.is_reserved


def test_management_allocation_needs_a_management_pool_and_can_exhaust(env) -> None:
    db, tenant = env
    customer = seed_pool(db, tenant, cidr="198.51.100.0/28")
    with pytest.raises(IpamError, match="management pool"):
        allocate_management_address(
            db, tenant_id=tenant, command=AllocateManagementAddress(customer, uuid4())
        )
    tiny = seed_pool(db, tenant, cidr="203.0.113.0/30", purpose=PoolPurpose.MANAGEMENT)
    for _ in range(2):
        allocate_management_address(
            db, tenant_id=tenant, command=AllocateManagementAddress(tiny, uuid4())
        )
    with pytest.raises(PoolExhausted):
        allocate_management_address(
            db, tenant_id=tenant, command=AllocateManagementAddress(tiny, uuid4())
        )


def test_a_management_address_is_never_handed_to_a_customer(env) -> None:
    db, tenant = env
    mgmt = seed_pool(db, tenant, cidr="192.0.2.0/29", purpose=PoolPurpose.MANAGEMENT)
    allocate_management_address(
        db, tenant_id=tenant, command=AllocateManagementAddress(mgmt, uuid4())
    )
    with pytest.raises(IpamError, match="customer"):
        alloc(db, tenant, [mgmt])


# --- tenancy -------------------------------------------------------------------


def test_commands_never_cross_tenants(env) -> None:
    db, tenant = env
    other = seed_tenant(db)
    pool = seed_pool(db, other, cidr="192.0.2.0/28")
    with pytest.raises(IpamError, match="pool not found"):
        alloc(db, tenant, [pool])
