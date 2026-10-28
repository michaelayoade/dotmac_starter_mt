"""Pools/blocks/reservations, PD, routed prefixes, utilization, queries, audit
(moved from Sub's ``test_ipv6_pd_allocator``, utilization snapshot, and the
IPAM-internal part of ``test_ip_consistency_audit``). Synthetic data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_ipam import (
    AddBlock,
    AddressFamily,
    AllocateDelegatedPrefix,
    AmbiguousPrimary,
    AssignAddress,
    AssignmentLookup,
    CreatePool,
    IpamConflict,
    IpamError,
    MarkPrimary,
    PoolExhausted,
    PoolPurpose,
    PruneUtilization,
    RecordUtilization,
    ReleaseAssignment,
    ReleaseDelegatedPrefix,
    ReservationKind,
    ReserveAddress,
    RetireBlock,
    RetireRoutedPrefix,
    SweepExpired,
    UpdatePool,
    UpsertRoutedPrefix,
    add_block,
    allocate_delegated_prefix,
    assign_address,
    audit_consistency,
    check_address_available,
    command_scope,
    create_pool,
    delegated_prefix_for,
    events,
    live_pool_counts,
    lookup_address,
    lookup_assignments,
    mark_primary,
    pools_for_site,
    primary_ipv4_for,
    prune_utilization,
    record_utilization,
    release_assignment,
    release_delegated_prefix,
    reserve_address,
    retire_block,
    retire_routed_prefix,
    sweep_expired,
    update_pool,
    upsert_routed_prefix,
    utilization,
)
from dotmac_ipam.models import (
    IPAssignment,
    IpPool,
    IPv4Address,
    SubscriberAdditionalRoute,
)
from sqlalchemy import select

from tests.unit.ipam_support import SITE, make_session, seed_pool, seed_tenant, seed_v4

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def env():
    db = make_session()
    tenant = seed_tenant(db)
    try:
        yield db, tenant
    finally:
        db.close()


# --- pools / blocks --------------------------------------------------------


def test_customer_pool_requires_a_site(env) -> None:
    db, tenant = env
    with pytest.raises(IpamError, match="site_ref"):
        create_pool(
            db,
            tenant_id=tenant,
            command=CreatePool(
                name="p", cidr="192.0.2.0/24", purpose=PoolPurpose.CUSTOMER
            ),
        )
    infra = create_pool(
        db,
        tenant_id=tenant,
        command=CreatePool(
            name="i", cidr="198.51.100.0/24", purpose=PoolPurpose.INFRASTRUCTURE
        ),
    )
    assert infra.site_ref is None


def test_overlapping_active_pools_and_duplicate_names_are_refused(env) -> None:
    db, tenant = env
    seed_pool(db, tenant, cidr="192.0.2.0/24", name="one")
    with pytest.raises(IpamConflict, match="overlaps"):
        seed_pool(db, tenant, cidr="192.0.2.128/25", name="two")
    with pytest.raises(IpamConflict, match="name already exists"):
        seed_pool(db, tenant, cidr="198.51.100.0/24", name="one")
    # families never overlap each other
    seed_pool(db, tenant, cidr="2001:db8::/64", family=AddressFamily.IPV6, name="six")


def test_family_and_cidr_validation(env) -> None:
    db, tenant = env
    with pytest.raises(IpamError, match="family"):
        create_pool(
            db,
            tenant_id=tenant,
            command=CreatePool(
                name="x", cidr="2001:db8::/64", purpose=PoolPurpose.MANAGEMENT
            ),
        )
    with pytest.raises(IpamError, match="valid CIDR"):
        create_pool(
            db,
            tenant_id=tenant,
            command=CreatePool(
                name="y", cidr="not-a-cidr", purpose=PoolPurpose.MANAGEMENT
            ),
        )


def test_site_ref_is_immutable_while_assignments_are_active(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    update_pool(
        db, tenant_id=tenant, command=UpdatePool(pool_id=pool, site_ref=uuid4())
    )
    assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.10", uuid4(), None, pool_id=pool),
    )
    with pytest.raises(IpamConflict, match="immutable"):
        update_pool(
            db, tenant_id=tenant, command=UpdatePool(pool_id=pool, site_ref=uuid4())
        )
    # other fields still change, and a customer pool cannot lose its site
    out = update_pool(
        db, tenant_id=tenant, command=UpdatePool(pool_id=pool, gateway="192.0.2.1")
    )
    assert out.gateway == "192.0.2.1" and out.site_ref is not None


def test_blocks_overlap_and_retirement_guard(env) -> None:
    db, tenant = env
    one = seed_pool(db, tenant, cidr="192.0.2.0/24")
    block = add_block(db, tenant_id=tenant, command=AddBlock(one, "192.0.2.0/28"))
    with pytest.raises(IpamConflict, match="overlaps"):
        add_block(db, tenant_id=tenant, command=AddBlock(one, "192.0.2.8/29"))
    with pytest.raises(IpamError, match="family"):
        add_block(db, tenant_id=tenant, command=AddBlock(one, "2001:db8::/64"))
    assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.5", uuid4(), None, pool_id=one),
    )
    with pytest.raises(IpamConflict, match="actively assigned"):
        retire_block(db, tenant_id=tenant, command=RetireBlock(block.id))
    free = add_block(db, tenant_id=tenant, command=AddBlock(one, "192.0.2.64/28"))
    assert not retire_block(
        db, tenant_id=tenant, command=RetireBlock(free.id)
    ).is_active


def test_reservation_rules_and_expiry_sweep(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    snap = reserve_address(
        db,
        tenant_id=tenant,
        command=ReserveAddress(pool, "192.0.2.9", expires_at=T0 + timedelta(hours=1)),
    )
    assert snap.is_reserved
    with pytest.raises(IpamError, match="device_ref"):
        reserve_address(
            db,
            tenant_id=tenant,
            command=ReserveAddress(pool, "192.0.2.10", kind=ReservationKind.DEVICE),
        )
    with pytest.raises(IpamError, match="outside the pool"):
        reserve_address(
            db, tenant_id=tenant, command=ReserveAddress(pool, "198.51.100.1")
        )
    assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.20", uuid4(), None, pool_id=pool),
    )
    with pytest.raises(IpamConflict, match="actively assigned"):
        reserve_address(
            db, tenant_id=tenant, command=ReserveAddress(pool, "192.0.2.20")
        )
    assert sweep_expired(db, tenant_id=tenant, command=SweepExpired(T0)) == 0
    assert (
        sweep_expired(
            db, tenant_id=tenant, command=SweepExpired(T0 + timedelta(hours=2))
        )
        == 1
    )
    row = db.scalar(select(IPv4Address).where(IPv4Address.address == "192.0.2.9"))
    assert not row.is_reserved and row.reserved_until is None


# --- prefix delegation ---------------------------------------------------------


@pytest.fixture
def pd(env):
    db, tenant = env
    pool = seed_pool(
        db,
        tenant,
        cidr="2001:db8::/62",
        family=AddressFamily.IPV6,
        delegation=64,
        name="pd",
    )
    return db, tenant, pool


def test_pd_carves_aligned_prefixes_in_order_and_is_idempotent(pd) -> None:
    db, tenant, pool = pd
    s1, s2 = uuid4(), uuid4()
    a = allocate_delegated_prefix(
        db, tenant_id=tenant, command=AllocateDelegatedPrefix(pool, uuid4(), s1)
    )
    b = allocate_delegated_prefix(
        db, tenant_id=tenant, command=AllocateDelegatedPrefix(pool, uuid4(), s2)
    )
    again = allocate_delegated_prefix(
        db, tenant_id=tenant, command=AllocateDelegatedPrefix(pool, uuid4(), s1)
    )
    assert (a.prefix, b.prefix) == ("2001:db8::", "2001:db8:0:1::")
    assert again.id == a.id and a.state == "assigned" and a.prefix_length == 64


def test_pd_exhausts_then_reuses_released_prefixes(pd) -> None:
    db, tenant, pool = pd
    subs = [uuid4() for _ in range(4)]
    for s in subs:
        allocate_delegated_prefix(
            db, tenant_id=tenant, command=AllocateDelegatedPrefix(pool, uuid4(), s)
        )
    with pytest.raises(PoolExhausted):
        allocate_delegated_prefix(
            db,
            tenant_id=tenant,
            command=AllocateDelegatedPrefix(pool, uuid4(), uuid4()),
        )
    assert (
        release_delegated_prefix(
            db,
            tenant_id=tenant,
            command=ReleaseDelegatedPrefix(subscription_id=subs[1]),
        )
        == 1
    )
    again = allocate_delegated_prefix(
        db, tenant_id=tenant, command=AllocateDelegatedPrefix(pool, uuid4(), uuid4())
    )
    assert again.prefix == "2001:db8:0:1::"


def test_pd_lookup_prefers_the_exact_service_and_refuses_ambiguous_legacy(pd) -> None:
    db, tenant, pool = pd
    sub, subscriber = uuid4(), uuid4()
    allocate_delegated_prefix(
        db, tenant_id=tenant, command=AllocateDelegatedPrefix(pool, subscriber, sub)
    )
    assert (
        delegated_prefix_for(db, tenant_id=tenant, subscription_id=sub)
        == "2001:db8::/64"
    )
    assert delegated_prefix_for(db, tenant_id=tenant, subscription_id=uuid4()) is None
    legacy = uuid4()
    allocate_delegated_prefix(
        db, tenant_id=tenant, command=AllocateDelegatedPrefix(pool, legacy)
    )
    assert (
        delegated_prefix_for(db, tenant_id=tenant, subscriber_id=legacy)
        == "2001:db8:0:1::/64"
    )
    allocate_delegated_prefix(
        db, tenant_id=tenant, command=AllocateDelegatedPrefix(pool, uuid4(), None)
    )  # unrelated
    with pytest.raises(IpamError):
        release_delegated_prefix(db, tenant_id=tenant, command=ReleaseDelegatedPrefix())


def test_pd_needs_an_active_customer_ipv6_pool(env) -> None:
    db, tenant = env
    v4 = seed_pool(db, tenant, cidr="192.0.2.0/24")
    with pytest.raises(IpamError, match="IPv6"):
        allocate_delegated_prefix(
            db, tenant_id=tenant, command=AllocateDelegatedPrefix(v4, uuid4())
        )


# --- routed prefixes ---------------------------------------------------------------


def test_routed_prefix_upsert_retire_and_host_conflict(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    subscriber = uuid4()
    one = upsert_routed_prefix(
        db,
        tenant_id=tenant,
        command=UpsertRoutedPrefix(
            subscriber, "198.51.100.8/29", metric=5, source="manual"
        ),
    )
    again = upsert_routed_prefix(
        db,
        tenant_id=tenant,
        command=UpsertRoutedPrefix(subscriber, "198.51.100.8/29", metric=7),
    )
    assert again.id == one.id and again.metric == 7
    retired = retire_routed_prefix(
        db, tenant_id=tenant, command=RetireRoutedPrefix(subscriber, "198.51.100.8/29")
    )
    assert not retired.is_active
    assert upsert_routed_prefix(
        db, tenant_id=tenant, command=UpsertRoutedPrefix(subscriber, "198.51.100.8/29")
    ).is_active
    assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.77", uuid4(), None, pool_id=pool),
    )
    with pytest.raises(IpamConflict, match="actively assigned"):
        upsert_routed_prefix(
            db,
            tenant_id=tenant,
            command=UpsertRoutedPrefix(subscriber, "192.0.2.64/26"),
        )
    with pytest.raises(IpamError, match="IPv4 only"):
        upsert_routed_prefix(
            db,
            tenant_id=tenant,
            command=UpsertRoutedPrefix(subscriber, "2001:db8::/64"),
        )


# --- utilization -------------------------------------------------------------------


def test_utilization_counts_prune_and_history(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/29")  # capacity 6
    assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.1", uuid4(), None, pool_id=pool),
    )
    reserve_address(db, tenant_id=tenant, command=ReserveAddress(pool, "192.0.2.2"))
    (snap,) = record_utilization(db, tenant_id=tenant, command=RecordUtilization(T0))
    assert (snap.total, snap.used, snap.reserved, snap.available, snap.percent) == (
        6,
        1,
        1,
        4,
        17,
    )
    record_utilization(
        db, tenant_id=tenant, command=RecordUtilization(T0 + timedelta(days=1))
    )
    history = utilization(db, tenant_id=tenant, pool_id=pool)
    assert [h.captured_at.day for h in history] == [28, 29]
    assert (
        prune_utilization(
            db, tenant_id=tenant, command=PruneUtilization(T0 + timedelta(hours=1))
        )
        == 1
    )
    assert len(utilization(db, tenant_id=tenant, pool_id=pool)) == 1
    assert live_pool_counts(db, tenant_id=tenant, pool_id=pool) == (1, 2)


def test_ipv6_capacity_is_capped_and_inactive_pools_skipped(env) -> None:
    db, tenant = env
    six = seed_pool(
        db, tenant, cidr="2001:db8::/32", family=AddressFamily.IPV6, name="six"
    )
    off = seed_pool(db, tenant, cidr="192.0.2.0/24", name="off")
    update_pool(db, tenant_id=tenant, command=UpdatePool(pool_id=off, is_active=False))
    snaps = record_utilization(db, tenant_id=tenant, command=RecordUtilization(T0))
    assert [s.pool_id for s in snaps] == [six] and snaps[0].total == 2_000_000_000


# --- queries ------------------------------------------------------


def test_lookup_and_availability(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24", blocks=["192.0.2.0/25"])
    sub, subscriber = uuid4(), uuid4()
    a = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.5", sub, subscriber, pool_id=pool),
    )
    assert [
        r.id
        for r in lookup_assignments(
            db, tenant_id=tenant, query=AssignmentLookup(subscription_ids=(sub,))
        )
    ] == [a.id]
    assert (
        lookup_assignments(
            db, tenant_id=tenant, query=AssignmentLookup(addresses=("192.0.2.5/32",))
        )[0].id
        == a.id
    )
    assert (
        lookup_assignments(
            db, tenant_id=tenant, query=AssignmentLookup(subscriber_ids=(uuid4(),))
        )
        == ()
    )
    with pytest.raises(IpamError):
        lookup_assignments(db, tenant_id=tenant, query=AssignmentLookup())
    assert lookup_address(db, tenant_id=tenant, address="192.0.2.5").assigned
    assert lookup_address(db, tenant_id=tenant, address="192.0.2.6") is None
    assert (
        check_address_available(db, tenant_id=tenant, address="192.0.2.5").reason
        == "assigned"
    )
    assert check_address_available(db, tenant_id=tenant, address="192.0.2.6").available
    outside = check_address_available(db, tenant_id=tenant, address="203.0.113.1")
    assert outside.available and not outside.in_managed_block
    reserve_address(db, tenant_id=tenant, command=ReserveAddress(pool, "192.0.2.7"))
    assert (
        check_address_available(db, tenant_id=tenant, address="192.0.2.7").reason
        == "reserved"
    )


def test_released_rows_are_found_only_when_not_active_only(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    sub = uuid4()
    a = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.5", sub, None, pool_id=pool),
    )
    release_assignment(
        db, tenant_id=tenant, command=ReleaseAssignment(a.id, "x", now=T0)
    )
    q = AssignmentLookup(subscription_ids=(sub,))
    assert lookup_assignments(db, tenant_id=tenant, query=q) == ()
    assert (
        len(
            lookup_assignments(
                db,
                tenant_id=tenant,
                query=AssignmentLookup(subscription_ids=(sub,), active_only=False),
            )
        )
        == 1
    )


def test_primary_ipv4_for_returns_the_marked_sole_or_ambiguous(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    marked, sole, ambiguous, none = uuid4(), uuid4(), uuid4(), uuid4()
    assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.1", marked, None, pool_id=pool),
    )
    assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress(
            "192.0.2.2", marked, None, pool_id=pool, mark_primary=False
        ),
    )
    assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress(
            "192.0.2.3", sole, None, pool_id=pool, mark_primary=False
        ),
    )
    for address in ("192.0.2.4", "192.0.2.5"):
        assign_address(
            db,
            tenant_id=tenant,
            command=AssignAddress(
                address, ambiguous, None, pool_id=pool, mark_primary=False
            ),
        )
    out = primary_ipv4_for(
        db, tenant_id=tenant, subscription_ids=(marked, sole, ambiguous, none)
    )
    assert out[marked] == "192.0.2.1"
    assert out[sole] == "192.0.2.3"
    assert out[ambiguous] == AmbiguousPrimary(ambiguous, ("192.0.2.4", "192.0.2.5"))
    assert none not in out


def test_pools_for_site(env) -> None:
    db, tenant = env
    other_site = uuid4()
    mine = seed_pool(db, tenant, cidr="192.0.2.0/24", name="b")
    seed_pool(db, tenant, cidr="198.51.100.0/24", name="a", site=other_site)
    off = seed_pool(db, tenant, cidr="203.0.113.0/24", name="c")
    update_pool(db, tenant_id=tenant, command=UpdatePool(pool_id=off, is_active=False))
    assert [p.id for p in pools_for_site(db, tenant_id=tenant, site_ref=SITE)] == [mine]
    assert (
        len(pools_for_site(db, tenant_id=tenant, site_ref=SITE, active_only=False)) == 2
    )


# --- consistency audit ---------------------------------------------


def test_audit_is_clean_on_a_healthy_estate(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.5", uuid4(), None, pool_id=pool),
    )
    assert audit_consistency(db, tenant_id=tenant).ok


def test_audit_reports_ipam_internal_drift(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24", name="p")
    sub = uuid4()
    # unbound active assignment, ambiguous primary, address outside its pool,
    # reserved holder, routed overlap -- planted below the command surface
    (a, b, c, d) = seed_v4(
        db, tenant, pool, "192.0.2.5", "192.0.2.6", "198.51.100.7", "192.0.2.8"
    )
    with command_scope():
        for address_id, subscription in ((a, None), (b, sub), (c, sub), (d, uuid4())):
            db.add(
                IPAssignment(
                    tenant_id=tenant,
                    subscription_id=subscription,
                    ip_version="ipv4",
                    ipv4_address_id=address_id,
                    is_active=True,
                )
            )
        row = db.get(IPv4Address, d)
        row.is_reserved = True
        db.flush()
    report = audit_consistency(db, tenant_id=tenant)
    assert report.count("active_unbound_assignment") == 1
    assert report.count("assignment_address_unpooled") == 1
    assert report.count("assignment_on_reserved_address") == 1
    assert report.count("primary_ambiguous") == 1
    assert not report.ok


def test_audit_flags_pool_overlap_and_routed_blocks(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24", name="p")
    (a,) = seed_v4(db, tenant, pool, "192.0.2.5")
    with command_scope():
        db.add(
            IPAssignment(
                tenant_id=tenant,
                subscription_id=uuid4(),
                ip_version="ipv4",
                ipv4_address_id=a,
                is_active=True,
            )
        )
        db.add(
            SubscriberAdditionalRoute(
                tenant_id=tenant,
                subscriber_id=uuid4(),
                cidr="192.0.2.0/29",
                prefix_length=29,
                is_active=True,
            )
        )
        db.add(
            IpPool(
                tenant_id=tenant,
                name="dup",
                ip_version="ipv4",
                cidr="192.0.2.128/25",
                is_active=True,
                purpose="infrastructure",
            )
        )
        db.flush()
    report = audit_consistency(db, tenant_id=tenant)
    assert report.count("assignment_in_routed_block") == 1
    assert report.count("pool_overlap") == 1


def test_events_are_ordered_evidence(env) -> None:
    db, tenant = env
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    sub = uuid4()
    a = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.5", sub, None, pool_id=pool),
    )
    assert mark_primary(db, tenant_id=tenant, command=MarkPrimary(sub, a.address_id))
    got = events(db, tenant_id=tenant, aggregate_ref=f"subscription:{sub}")
    # assign promoted the primary (PrimaryChanged); re-marking it changes nothing
    assert sorted(e.event_type for e in got) == ["AddressAssigned", "PrimaryChanged"]
    assert all(e.payload for e in got)
