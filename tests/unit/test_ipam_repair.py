"""Fingerprinted repair, IPv6 link and ownership unlink (moved from Sub's
``test_ip_assignment_repair`` / ``test_ip_assignment_lifecycle`` /
``test_ip_assignment_ipv6_link_unlink``), with the subscription truth passed in
as caller facts. Synthetic data only."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_ipam import (
    AddressFamily,
    ApplyIPv6Link,
    ApplyRepair,
    ApplyUnlink,
    AssignAddress,
    IpamConflict,
    IpamError,
    RepairDecision,
    RepairFacts,
    ReserveAddress,
    StalePreview,
    UnsafeRepair,
    UpsertRoutedPrefix,
    apply_ipv6_link,
    apply_repair,
    apply_unlink,
    assign_address,
    events,
    preview_ipv6_link,
    preview_repair,
    preview_unlink,
    reserve_address,
    upsert_routed_prefix,
)
from dotmac_ipam.guard import _command_scope as command_scope
from dotmac_ipam.models import IPAssignment, IPv4Address, IPv6Address
from sqlalchemy import select

from tests.unit.ipam_support import (
    make_session,
    seed_pool,
    seed_tenant,
    seed_v4,
    seed_v6,
)

D = RepairDecision
CUTOFF = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture
def env():
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    try:
        yield db, tenant, pool, uuid4(), uuid4()  # subscriber, subscription
    finally:
        db.close()


def legacy_row(
    db, tenant, pool, address, subscriber, *, subscription=None, family="ipv4"
):
    """An active assignment predating exact-service ownership (NULL subscription)."""
    with command_scope():
        if family == "ipv4":
            addr = IPv4Address(tenant_id=tenant, address=address, pool_id=pool)
        else:
            addr = IPv6Address(tenant_id=tenant, address=address, pool_id=pool)
        db.add(addr)
        db.flush()
        row = IPAssignment(
            tenant_id=tenant,
            subscriber_id=subscriber,
            subscription_id=subscription,
            ip_version=family,
            ipv4_address_id=addr.id if family == "ipv4" else None,
            ipv6_address_id=addr.id if family == "ipv6" else None,
            is_active=True,
        )
        db.add(row)
        db.flush()
    return addr, row


def prev(db, tenant, sub, subscriber, desired=None, facts=None, ids=(), cutoff=None):
    return preview_repair(
        db,
        tenant_id=tenant,
        subscription_id=sub,
        subscriber_id=subscriber,
        desired_address_id=desired,
        facts=facts or RepairFacts(),
        deactivate_assignment_ids=tuple(ids),
        retention_cutoff=cutoff,
    )


def apply(db, tenant, preview, sub, subscriber, facts=None, key="k-1", **kw):
    return apply_repair(
        db,
        tenant_id=tenant,
        command=ApplyRepair(
            subscription_id=sub,
            subscriber_id=subscriber,
            desired_address_id=preview.desired_address_id,
            deactivate_assignment_ids=preview.deactivate_assignment_ids,
            preview_fingerprint=preview.fingerprint,
            idempotency_key=key,
            facts=facts or RepairFacts(),
            **kw,
        ),
    )


# --- preview decisions -------------------------------------------------------


def test_missing_subscription_or_subscriber(env) -> None:
    db, tenant, pool, subscriber, sub = env
    (addr_id,) = seed_v4(db, tenant, pool, "192.0.2.10")
    assert (
        prev(
            db, tenant, sub, subscriber, addr_id, RepairFacts(subscription_exists=False)
        ).decision
        is D.SUBSCRIPTION_NOT_FOUND
    )
    assert prev(db, tenant, sub, None, addr_id).decision is D.SUBSCRIBER_NOT_FOUND
    assert (
        prev(
            db, tenant, sub, subscriber, addr_id, RepairFacts(subscriber_exists=False)
        ).decision
        is D.SUBSCRIBER_NOT_FOUND
    )


def test_ready_create_for_an_unassigned_target_and_apply_creates_it(env) -> None:
    db, tenant, pool, subscriber, sub = env
    (addr_id,) = seed_v4(db, tenant, pool, "192.0.2.10")
    preview = prev(db, tenant, sub, subscriber, addr_id)
    assert preview.decision is D.READY_CREATE and preview.applicable
    out = apply(db, tenant, preview, sub, subscriber)
    assert (out.created_count, out.linked_count, out.deactivated_count) == (1, 0, 0)
    row = db.get(IPAssignment, out.desired_assignment_id)
    assert row.is_active and row.is_primary and row.subscription_id == sub
    assert row.prefix_length == 24
    assert prev(db, tenant, sub, subscriber, addr_id).decision is D.NOOP


def test_ready_link_adopts_a_legacy_unbound_assignment(env) -> None:
    db, tenant, pool, subscriber, sub = env
    addr, row = legacy_row(db, tenant, pool, "192.0.2.11", subscriber)
    preview = prev(db, tenant, sub, subscriber, addr.id)
    assert preview.decision is D.READY_LINK
    out = apply(db, tenant, preview, sub, subscriber)
    assert (out.linked_count, out.created_count) == (1, 0)
    db.refresh(row)
    assert row.subscription_id == sub and row.is_primary


def test_target_owned_by_another_service_or_subscriber_is_refused(env) -> None:
    db, tenant, pool, subscriber, sub = env
    other_sub = uuid4()
    addr, _ = legacy_row(
        db, tenant, pool, "192.0.2.12", subscriber, subscription=other_sub
    )
    assert (
        prev(db, tenant, sub, subscriber, addr.id).decision
        is D.TARGET_OWNED_BY_OTHER_SERVICE
    )
    addr2, _ = legacy_row(db, tenant, pool, "192.0.2.13", uuid4())
    assert (
        prev(db, tenant, sub, subscriber, addr2.id).decision
        is D.TARGET_OWNED_BY_OTHER_SERVICE
    )


def test_target_must_be_serviceable_and_outside_routed_blocks(env) -> None:
    db, tenant, pool, subscriber, sub = env
    reserve_address(
        db, tenant_id=tenant, command=ReserveAddress(pool_id=pool, address="192.0.2.20")
    )
    reserved = db.scalar(select(IPv4Address).where(IPv4Address.address == "192.0.2.20"))
    assert (
        prev(db, tenant, sub, subscriber, reserved.id).decision
        is D.TARGET_ADDRESS_NOT_SERVICEABLE
    )
    (routed,) = seed_v4(db, tenant, pool, "192.0.2.33")
    upsert_routed_prefix(
        db,
        tenant_id=tenant,
        command=UpsertRoutedPrefix(subscriber_id=uuid4(), cidr="192.0.2.32/29"),
    )
    assert (
        prev(db, tenant, sub, subscriber, routed).decision
        is D.TARGET_ADDRESS_IN_ROUTED_BLOCK
    )
    assert (
        prev(db, tenant, sub, subscriber, uuid4()).decision
        is D.TARGET_ADDRESS_NOT_FOUND
    )
    (ok,) = seed_v4(db, tenant, pool, "192.0.2.40")
    assert (
        prev(db, tenant, sub, subscriber, ok, RepairFacts(serviceable=False)).decision
        is D.SUBSCRIPTION_NOT_SERVICEABLE
    )


def test_swap_requires_the_complete_deactivation_set(env) -> None:
    db, tenant, pool, subscriber, sub = env
    old = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.50", sub, subscriber, pool_id=pool),
    )
    (new_id,) = seed_v4(db, tenant, pool, "192.0.2.51")
    incomplete = prev(db, tenant, sub, subscriber, new_id)
    assert incomplete.decision is D.INCOMPLETE_DEACTIVATION_SET
    complete = prev(db, tenant, sub, subscriber, new_id, ids=[old.id])
    assert complete.decision is D.READY_CREATE
    out = apply(db, tenant, complete, sub, subscriber)
    assert out.created_count == 1 and out.deactivated_count == 1
    assert not db.get(IPAssignment, old.id).is_active
    assert db.get(IPAssignment, old.id).release_reason == "repair"
    rows = db.scalars(
        select(IPAssignment).where(IPAssignment.is_active.is_(True))
    ).all()
    assert [r.is_primary for r in rows] == [True]


def test_deactivation_cohort_must_exist_belong_to_the_service_and_not_be_the_target(
    env,
) -> None:
    db, tenant, pool, subscriber, sub = env
    mine = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.60", sub, subscriber, pool_id=pool),
    )
    theirs = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.61", uuid4(), subscriber, pool_id=pool),
    )
    (new_id,) = seed_v4(db, tenant, pool, "192.0.2.62")
    assert (
        prev(db, tenant, sub, subscriber, new_id, ids=[uuid4()]).decision
        is D.DEACTIVATION_ASSIGNMENT_NOT_FOUND
    )
    assert (
        prev(db, tenant, sub, subscriber, new_id, ids=[theirs.id]).decision
        is D.DEACTIVATION_NOT_EXACT_SERVICE
    )
    assert (
        prev(db, tenant, sub, subscriber, mine.address_id, ids=[mine.id]).decision
        is D.DESIRED_ASSIGNMENT_SELECTED_FOR_DEACTIVATION
    )


def test_ready_keep_prunes_extras_around_the_desired_assignment(env) -> None:
    db, tenant, pool, subscriber, sub = env
    keep = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.70", sub, subscriber, pool_id=pool),
    )
    extra = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress(
            "192.0.2.71", sub, subscriber, pool_id=pool, mark_primary=False
        ),
    )
    preview = prev(db, tenant, sub, subscriber, keep.address_id, ids=[extra.id])
    assert preview.decision is D.READY_KEEP
    out = apply(db, tenant, preview, sub, subscriber)
    assert out.deactivated_count == 1 and out.created_count == 0
    assert db.get(IPAssignment, keep.id).is_active


# --- release ------------------------------------------------------------


def test_terminal_release_needs_terminal_facts_and_the_full_set(env) -> None:
    db, tenant, pool, subscriber, sub = env
    a = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.80", sub, subscriber, pool_id=pool),
    )
    b = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.81", sub, subscriber, pool_id=pool),
    )
    terminal = RepairFacts(terminal=True)
    assert (
        prev(
            db, tenant, sub, subscriber, None, RepairFacts(), ids=[a.id, b.id]
        ).decision
        is D.SUBSCRIPTION_NOT_TERMINAL
    )
    assert (
        prev(db, tenant, sub, subscriber, None, terminal, ids=[a.id]).decision
        is D.INCOMPLETE_DEACTIVATION_SET
    )
    ready = prev(db, tenant, sub, subscriber, None, terminal, ids=[a.id, b.id])
    assert ready.decision is D.READY_RELEASE
    out = apply(db, tenant, ready, sub, subscriber, terminal)
    assert out.deactivated_count == 2
    assert prev(db, tenant, sub, subscriber, None, terminal).decision is D.NOOP


def test_retention_release_gates_age_role_and_live_use(env) -> None:
    db, tenant, pool, subscriber, sub = env
    a = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.90", sub, subscriber, pool_id=pool),
    )
    base = {"retention_release": True}
    young = RepairFacts(**base, retention_expired=False)
    assert (
        prev(db, tenant, sub, subscriber, None, young, [a.id], CUTOFF).decision
        is D.RETENTION_NOT_EXPIRED
    )
    live = RepairFacts(
        **base, retention_expired=True, live_use_addresses=frozenset({"192.0.2.90/32"})
    )
    assert (
        prev(db, tenant, sub, subscriber, None, live, [a.id], CUTOFF).decision
        is D.ADDRESS_IN_LIVE_USE
    )
    ok = RepairFacts(**base, retention_expired=True)
    ready = prev(db, tenant, sub, subscriber, None, ok, [a.id], CUTOFF)
    assert ready.decision is D.READY_RELEASE_RETENTION_EXPIRED
    # without a cutoff the retention path is not taken at all
    assert (
        prev(db, tenant, sub, subscriber, None, ok, [a.id], None).decision
        is D.SUBSCRIPTION_NOT_TERMINAL
    )
    # a management-role address is never retention-released
    row = db.scalar(select(IPv4Address).where(IPv4Address.address == "192.0.2.90"))
    with command_scope():
        row.allocation_type = "management"
        db.flush()
    assert (
        prev(db, tenant, sub, subscriber, None, ok, [a.id], CUTOFF).decision
        is D.ADDRESS_NOT_RELEASABLE
    )


# --- apply safety ---------------------------------------------------------


def test_apply_refuses_a_stale_preview_and_changed_caller_facts(env) -> None:
    db, tenant, pool, subscriber, sub = env
    (addr_id,) = seed_v4(db, tenant, pool, "192.0.2.100")
    preview = prev(db, tenant, sub, subscriber, addr_id)
    # IPAM evidence moved after preview
    legacy_row(db, tenant, pool, "192.0.2.101", subscriber)
    with pytest.raises(StalePreview):
        apply(db, tenant, preview, sub, subscriber)
    fresh = prev(db, tenant, sub, subscriber, addr_id)
    # the caller's truth changed
    with pytest.raises(StalePreview):
        apply(
            db,
            tenant,
            fresh,
            sub,
            subscriber,
            RepairFacts(serviceable=True, terminal=True),
        )


def test_apply_refuses_an_inapplicable_preview(env) -> None:
    db, tenant, pool, subscriber, sub = env
    preview = prev(db, tenant, sub, subscriber, uuid4())
    assert preview.decision is D.TARGET_ADDRESS_NOT_FOUND
    with pytest.raises(UnsafeRepair):
        apply(db, tenant, preview, sub, subscriber)


def test_apply_is_idempotent_and_a_reused_key_with_other_evidence_conflicts(
    env,
) -> None:
    db, tenant, pool, subscriber, sub = env
    (addr_id,) = seed_v4(db, tenant, pool, "192.0.2.110")
    preview = prev(db, tenant, sub, subscriber, addr_id)
    first = apply(db, tenant, preview, sub, subscriber)
    again = apply(db, tenant, preview, sub, subscriber)
    assert not first.replayed and again.replayed
    assert again.desired_assignment_id == first.desired_assignment_id
    assert len(db.scalars(select(IPAssignment)).all()) == 1
    (other_id,) = seed_v4(db, tenant, pool, "192.0.2.111")
    other = prev(db, tenant, uuid4(), subscriber, other_id)
    with pytest.raises(IpamConflict, match="idempotency"):
        apply(db, tenant, other, other.subscription_id, subscriber)
    types = [
        e.event_type
        for e in events(
            db, tenant_id=tenant, aggregate_ref="idempotency:assignment_repair:k-1"
        )
    ]
    assert types == ["AssignmentRepaired"]


def test_apply_demands_a_sane_idempotency_key_and_unique_cohort(env) -> None:
    db, tenant, pool, subscriber, sub = env
    (addr_id,) = seed_v4(db, tenant, pool, "192.0.2.120")
    preview = prev(db, tenant, sub, subscriber, addr_id)
    with pytest.raises(IpamError, match="idempotency key"):
        apply(db, tenant, preview, sub, subscriber, key="  ")
    with pytest.raises(IpamError, match="idempotency key"):
        apply(db, tenant, preview, sub, subscriber, key="x" * 200)
    dup = uuid4()
    with pytest.raises(IpamError, match="repeats"):
        apply_repair(
            db,
            tenant_id=tenant,
            command=ApplyRepair(
                subscription_id=sub,
                subscriber_id=subscriber,
                desired_address_id=None,
                deactivate_assignment_ids=(dup, dup),
                preview_fingerprint="x",
                idempotency_key="k",
                facts=RepairFacts(),
            ),
        )


def test_preview_is_read_only(env) -> None:
    db, tenant, pool, subscriber, sub = env
    (addr_id,) = seed_v4(db, tenant, pool, "192.0.2.130")
    db.commit()
    prev(db, tenant, sub, subscriber, addr_id)
    assert not db.new and not db.dirty and not db.deleted


# --- IPv6 link (#3812) -----------------------------------------------------------


@pytest.fixture
def v6env(env):
    db, tenant, _pool4, subscriber, sub = env
    pool = seed_pool(
        db, tenant, cidr="2001:db8::/64", family=AddressFamily.IPV6, name="v6"
    )
    return db, tenant, pool, subscriber, sub


def prev6(db, tenant, sub, subscriber, address_id, facts=None):
    return preview_ipv6_link(
        db,
        tenant_id=tenant,
        subscription_id=sub,
        subscriber_id=subscriber,
        desired_address_id=address_id,
        facts=facts or RepairFacts(),
    )


def test_ipv6_link_adopts_an_unbound_assignment_and_never_sets_primary(v6env) -> None:
    db, tenant, pool, subscriber, sub = v6env
    addr, row = legacy_row(db, tenant, pool, "2001:db8::10", subscriber, family="ipv6")
    preview = prev6(db, tenant, sub, subscriber, addr.id)
    assert preview.decision is D.READY_LINK and preview.applicable
    out = apply_ipv6_link(
        db,
        tenant_id=tenant,
        command=ApplyIPv6Link(
            sub,
            addr.id,
            preview.fingerprint,
            "v6-1",
            RepairFacts(),
            subscriber_id=subscriber,
        ),
    )
    assert out.linked_count == 1 and not out.replayed
    db.refresh(row)
    assert row.subscription_id == sub and not row.is_primary
    assert prev6(db, tenant, sub, subscriber, addr.id).decision is D.NOOP
    replay = apply_ipv6_link(
        db,
        tenant_id=tenant,
        command=ApplyIPv6Link(
            sub,
            addr.id,
            preview.fingerprint,
            "v6-1",
            RepairFacts(),
            subscriber_id=subscriber,
        ),
    )
    assert replay.replayed


def test_ipv6_link_decisions(v6env) -> None:
    db, tenant, pool, subscriber, sub = v6env
    assert (
        prev6(db, tenant, sub, subscriber, uuid4()).decision
        is D.TARGET_ADDRESS_NOT_FOUND
    )
    (free,) = seed_v6(db, tenant, pool, "2001:db8::20")
    assert (
        prev6(db, tenant, sub, subscriber, free).decision
        is D.TARGET_ASSIGNMENT_NOT_FOUND
    )
    other, _ = legacy_row(
        db,
        tenant,
        pool,
        "2001:db8::21",
        subscriber,
        subscription=uuid4(),
        family="ipv6",
    )
    assert (
        prev6(db, tenant, sub, subscriber, other.id).decision
        is D.TARGET_OWNED_BY_OTHER_SERVICE
    )
    held, _ = legacy_row(
        db, tenant, pool, "2001:db8::22", subscriber, subscription=sub, family="ipv6"
    )
    target, _ = legacy_row(db, tenant, pool, "2001:db8::23", subscriber, family="ipv6")
    assert (
        prev6(db, tenant, sub, subscriber, target.id).decision
        is D.SERVICE_ALREADY_HAS_IPV6_ASSIGNMENT
    )
    assert held
    assert (
        prev6(
            db, tenant, sub, subscriber, target.id, RepairFacts(serviceable=False)
        ).decision
        is D.SUBSCRIPTION_NOT_SERVICEABLE
    )
    assert prev6(db, tenant, sub, None, target.id).decision is D.SUBSCRIBER_NOT_FOUND


def test_ipv6_link_refuses_stale_and_unsafe(v6env) -> None:
    db, tenant, pool, subscriber, sub = v6env
    addr, _ = legacy_row(db, tenant, pool, "2001:db8::30", subscriber, family="ipv6")
    preview = prev6(db, tenant, sub, subscriber, addr.id)
    with pytest.raises(StalePreview):
        apply_ipv6_link(
            db,
            tenant_id=tenant,
            command=ApplyIPv6Link(
                sub,
                addr.id,
                preview.fingerprint,
                "v6-2",
                RepairFacts(serviceable=False),
                subscriber_id=subscriber,
            ),
        )
    missing = uuid4()
    bad = prev6(db, tenant, sub, subscriber, missing)
    with pytest.raises(UnsafeRepair):
        apply_ipv6_link(
            db,
            tenant_id=tenant,
            command=ApplyIPv6Link(
                sub,
                missing,
                bad.fingerprint,
                "v6-3",
                RepairFacts(),
                subscriber_id=subscriber,
            ),
        )


# --- unlink (the inverse) -----------------------------------------------------------


def prevu(db, tenant, row_id, sub, facts):
    return preview_unlink(
        db,
        tenant_id=tenant,
        assignment_id=row_id,
        expected_subscription_id=sub,
        facts=facts,
    )


def test_unlink_undoes_a_link_and_leaves_the_address_held(env) -> None:
    db, tenant, pool, subscriber, sub = env
    addr, row = legacy_row(db, tenant, pool, "192.0.2.140", subscriber)
    preview = prev(db, tenant, sub, subscriber, addr.id)
    apply(db, tenant, preview, sub, subscriber)
    facts = RepairFacts(
        subscription_subscriber_id=subscriber,
        served_address="192.0.2.140/32",
        live_session_present=True,
    )
    plan = prevu(db, tenant, row.id, sub, facts)
    assert plan.decision is D.READY_UNLINK and plan.applicable
    assert plan.served_address_matches_assignment and plan.is_primary
    out = apply_unlink(
        db,
        tenant_id=tenant,
        command=ApplyUnlink(
            row.id, sub, plan.fingerprint, "u-1", facts, reason="rollback"
        ),
    )
    db.refresh(row)
    assert row.subscription_id is None and not row.is_primary and row.is_active
    assert out.previous_subscription_id == sub and not out.replayed
    assert apply_unlink(
        db,
        tenant_id=tenant,
        command=ApplyUnlink(row.id, sub, plan.fingerprint, "u-1", facts),
    ).replayed


def test_unlink_decisions(env) -> None:
    db, tenant, pool, subscriber, sub = env
    facts = RepairFacts(subscription_subscriber_id=subscriber)
    assert prevu(db, tenant, uuid4(), sub, facts).decision is D.ASSIGNMENT_NOT_FOUND
    _, unbound = legacy_row(db, tenant, pool, "192.0.2.150", subscriber)
    assert prevu(db, tenant, unbound.id, sub, facts).decision is D.ASSIGNMENT_NOT_LINKED
    _, linked = legacy_row(
        db, tenant, pool, "192.0.2.151", subscriber, subscription=sub
    )
    assert (
        prevu(db, tenant, linked.id, uuid4(), facts).decision is D.SUBSCRIPTION_MISMATCH
    )
    assert (
        prevu(
            db, tenant, linked.id, sub, RepairFacts(subscription_exists=False)
        ).decision
        is D.SUBSCRIPTION_NOT_FOUND
    )
    assert (
        prevu(
            db, tenant, linked.id, sub, RepairFacts(subscription_subscriber_id=uuid4())
        ).decision
        is D.SUBSCRIBER_MISMATCH
    )
    with command_scope():
        linked.is_active = False
        db.flush()
    assert prevu(db, tenant, linked.id, sub, facts).decision is D.ASSIGNMENT_NOT_ACTIVE


def test_unlink_evidence_is_bound_into_the_fingerprint(env) -> None:
    db, tenant, pool, subscriber, sub = env
    _, linked = legacy_row(
        db, tenant, pool, "192.0.2.160", subscriber, subscription=sub
    )
    a = RepairFacts(subscription_subscriber_id=subscriber, live_session_present=False)
    b = RepairFacts(subscription_subscriber_id=subscriber, live_session_present=True)
    plan_a, plan_b = (
        prevu(db, tenant, linked.id, sub, a),
        prevu(db, tenant, linked.id, sub, b),
    )
    assert (
        plan_a.applicable and plan_b.applicable
    )  # a live session never blocks rollback
    assert plan_a.fingerprint != plan_b.fingerprint
    with pytest.raises(StalePreview):
        apply_unlink(
            db,
            tenant_id=tenant,
            command=ApplyUnlink(linked.id, sub, plan_a.fingerprint, "u-2", b),
        )
