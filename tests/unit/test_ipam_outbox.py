"""Typed IPAM events reach the Kernel outbox in the writer's transaction."""

from __future__ import annotations

from uuid import uuid4

import pytest
from dotmac_ipam import (
    OUTBOX_EVENT_TYPES,
    AddressFamily,
    AggregateImported,
    AllocateAddress,
    ImportedAggregate,
    ImportedKind,
    IpamError,
    IpamNotFound,
    RecordImported,
    ReleaseAssignment,
    allocate_address,
    module,
    record_imported,
    release_assignment,
)
from dotmac_ipam.contracts import EVENT_SCHEMAS
from dotmac_ipam.models import IpamEvent
from dotmac_kernel.idempotency import IdempotencyConflict
from dotmac_kernel.idempotency_models import IdempotencyRecord
from dotmac_kernel.messaging.models import OutboxEvent
from sqlalchemy import select

from tests.unit.ipam_support import make_session, seed_pool, seed_tenant


def outbox(db):
    return db.scalars(select(OutboxEvent).order_by(OutboxEvent.created_at)).all()


def test_manifest_declares_every_routing_code_and_the_relay() -> None:
    assert set(module.outbox_event_types) == set(OUTBOX_EVENT_TYPES.values())
    assert "outbox_relay.v1" in module.requires
    assert "idempotency_ledger.v1" in module.requires
    assert set(OUTBOX_EVENT_TYPES) == set(EVENT_SCHEMAS)
    assert all(
        code.startswith("ipam.") and code.endswith(".v1")
        for code in module.outbox_event_types
    )
    assert len(set(OUTBOX_EVENT_TYPES.values())) == len(OUTBOX_EVENT_TYPES)


def test_a_command_enqueues_its_events_with_the_ledger_events() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    before = len(outbox(db))
    got = allocate_address(
        db,
        tenant_id=tenant,
        command=AllocateAddress((pool,), uuid4(), uuid4()),
    )
    rows = outbox(db)[before:]
    codes = sorted(row.event_type for row in rows)
    assert codes == ["ipam.address.assigned.v1", "ipam.assignment.primary_changed.v1"]
    assigned = next(r for r in rows if r.event_type == "ipam.address.assigned.v1")
    assert assigned.tenant_id == tenant
    assert assigned.payload["event"] == "AddressAssigned"
    assert assigned.payload["data"]["address"] == got.address
    assert assigned.payload["aggregate_ref"] == f"subscription:{got.subscription_id}"
    assert assigned.status == "pending"


def test_outbox_rows_commit_or_roll_back_with_the_state_change() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    db.commit()
    got = allocate_address(
        db, tenant_id=tenant, command=AllocateAddress((pool,), uuid4(), None)
    )
    release_assignment(
        db, tenant_id=tenant, command=ReleaseAssignment(got.id, reason="x")
    )
    assert any(r.event_type == "ipam.assignment.released.v1" for r in outbox(db))
    db.rollback()
    assert [r for r in outbox(db) if r.event_type.startswith("ipam.address")] == []
    assert [r for r in outbox(db) if r.event_type.startswith("ipam.assignment")] == []


def test_free_form_operational_events_are_not_published() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    seed_pool(db, tenant, cidr="192.0.2.0/28")  # pool_created is operational
    assert outbox(db) == []


def test_record_imported_writes_one_event_per_aggregate_and_is_idempotent() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pools = [
        seed_pool(db, tenant, cidr=f"192.0.2.{i * 16}/28", name=f"p{i}")
        for i in range(3)
    ]
    before = len(outbox(db))
    command = RecordImported(
        source_ref="legacy:ip_pools",
        aggregates=tuple(
            ImportedAggregate(ImportedKind.POOL, pool, "d" * 8) for pool in pools
        ),
    )
    assert record_imported(db, tenant_id=tenant, command=command) == 3
    assert record_imported(db, tenant_id=tenant, command=command) == 0
    rows = outbox(db)[before:]
    assert [r.event_type for r in rows] == ["ipam.aggregate.imported.v1"] * 3
    assert {r.payload["aggregate_ref"] for r in rows} == {f"pool:{i}" for i in pools}
    assert rows[0].payload["data"]["source_ref"] == "legacy:ip_pools"
    assert {r.correlation_id for r in rows} == {"legacy:ip_pools"}
    assert AggregateImported.__dataclass_params__.frozen
    with pytest.raises(IpamError, match="blank"):
        record_imported(
            db,
            tenant_id=tenant,
            command=RecordImported(source_ref=" ", aggregates=()),
        )


def test_a_repeated_aggregate_in_one_command_is_written_once() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant, cidr="192.0.2.0/28")
    item = ImportedAggregate(ImportedKind.POOL, pool)
    command = RecordImported("legacy:ip_pools", (item, item))
    assert record_imported(db, tenant_id=tenant, command=command) == 1


def test_import_events_are_refused_for_ids_not_in_the_module_for_the_tenant() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    other = seed_tenant(db)
    foreign_pool = seed_pool(db, other, cidr="192.0.2.0/28")
    pool = seed_pool(db, tenant, cidr="198.51.100.0/28")
    before = len(outbox(db))
    for kind, ident in (
        (ImportedKind.POOL, uuid4()),  # unknown
        (ImportedKind.POOL, foreign_pool),  # another tenant's
        (ImportedKind.ADDRESS, pool),  # right id, wrong kind
        (ImportedKind.PREFIX, uuid4()),
        (ImportedKind.ROUTED, uuid4()),
        (ImportedKind.SUBSCRIPTION, uuid4()),
    ):
        with pytest.raises(IpamNotFound, match="not present"):
            record_imported(
                db,
                tenant_id=tenant,
                command=RecordImported("legacy", (ImportedAggregate(kind, ident),)),
            )
    assert len(outbox(db)) == before  # nothing was enqueued
    with pytest.raises(ValueError):  # the kind is an enum of the existing ref kinds
        ImportedKind("table")


def test_each_known_kind_is_accepted_when_present() -> None:
    from dotmac_ipam import (
        AllocateDelegatedPrefix,
        AssignAddress,
        UpsertRoutedPrefix,
        allocate_delegated_prefix,
        assign_address,
        upsert_routed_prefix,
    )

    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant, cidr="192.0.2.0/24")
    sub = uuid4()
    got = assign_address(
        db,
        tenant_id=tenant,
        command=AssignAddress("192.0.2.5", sub, uuid4(), pool_id=pool),
    )
    pd_pool = seed_pool(
        db,
        tenant,
        cidr="2001:db8::/62",
        family=AddressFamily.IPV6,
        delegation=64,
        name="pd",
    )
    prefix = allocate_delegated_prefix(
        db, tenant_id=tenant, command=AllocateDelegatedPrefix(pd_pool, uuid4())
    )
    route = upsert_routed_prefix(
        db,
        tenant_id=tenant,
        command=UpsertRoutedPrefix(uuid4(), "198.51.100.0/29"),
    )
    items = (
        ImportedAggregate(ImportedKind.POOL, pool),
        ImportedAggregate(ImportedKind.ADDRESS, got.address_id),
        ImportedAggregate(ImportedKind.SUBSCRIPTION, sub),
        ImportedAggregate(ImportedKind.PREFIX, prefix.id),
        ImportedAggregate(ImportedKind.ROUTED, route.id),
    )
    assert (
        record_imported(db, tenant_id=tenant, command=RecordImported("legacy", items))
        == 5
    )


def test_the_outbox_module_is_import_safe_without_a_database() -> None:
    import ast
    from pathlib import Path

    import dotmac_ipam.outbox as mod

    tree = ast.parse(Path(mod.__file__).read_text())
    top_level = [n for n in tree.body if isinstance(n, ast.ImportFrom)]
    assert not any((n.module or "").startswith("dotmac_kernel") for n in top_level)


@pytest.mark.parametrize(
    "source,digest", [("other", "d"), ("legacy", "changed"), ("legacy", None)]
)
def test_import_provenance_conflict_keeps_original_pair(source, digest) -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant)
    original = RecordImported(
        "legacy", (ImportedAggregate(ImportedKind.POOL, pool, "d"),)
    )
    assert record_imported(db, tenant_id=tenant, command=original) == 1
    with pytest.raises(IdempotencyConflict):
        record_imported(
            db,
            tenant_id=tenant,
            command=RecordImported(
                source, (ImportedAggregate(ImportedKind.POOL, pool, digest),)
            ),
        )
    assert record_imported(db, tenant_id=tenant, command=original) == 0
    assert len(outbox(db)) == 1
    assert len(db.scalars(select(IdempotencyRecord)).all()) == 1
    imported = db.scalars(
        select(IpamEvent).where(IpamEvent.event_type == "AggregateImported")
    ).all()
    assert len(imported) == 1
    assert imported[0].payload["source_ref"] == "legacy"
    assert imported[0].payload["source_digest"] == "d"


def test_conflicting_duplicate_rolls_back_entire_import_batch() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant)
    with pytest.raises(IdempotencyConflict):
        record_imported(
            db,
            tenant_id=tenant,
            command=RecordImported(
                "legacy",
                (
                    ImportedAggregate(ImportedKind.POOL, pool, "one"),
                    ImportedAggregate(ImportedKind.POOL, pool, "two"),
                ),
            ),
        )
    assert outbox(db) == []
    assert db.scalars(select(IdempotencyRecord)).all() == []
    assert (
        db.scalars(
            select(IpamEvent).where(IpamEvent.event_type == "AggregateImported")
        ).all()
        == []
    )
    assert (
        record_imported(
            db,
            tenant_id=tenant,
            command=RecordImported(
                "legacy",
                (ImportedAggregate(ImportedKind.POOL, pool, "two"),),
            ),
        )
        == 1
    )
