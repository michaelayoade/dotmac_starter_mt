"""Flush guard, reader surface and schema shape for dotmac-ipam 0.2.0a1."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from dotmac_ipam import (
    IpamWriteOutsideCommand,
    SweepExpired,
    flush_guard_installed,
    install_flush_guard,
    module,
    read_models,
    sweep_expired,
)
from dotmac_ipam import models as ipam_models
from dotmac_ipam.guard import _command_scope as command_scope
from dotmac_ipam.guard import command, in_command_scope
from dotmac_ipam.models import IpPool, IPv4Address
from sqlalchemy import inspect, select

from tests.unit.ipam_support import make_session, seed_pool, seed_tenant, seed_v4


def test_guard_is_installed_and_idempotent() -> None:
    assert flush_guard_installed()
    assert install_flush_guard() is False


def test_writes_outside_a_command_scope_are_refused() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant)
    db.add(IPv4Address(tenant_id=tenant, address="192.0.2.10", pool_id=pool))
    with pytest.raises(IpamWriteOutsideCommand, match="IPv4Address"):
        db.flush()
    db.rollback()


def test_updates_and_deletes_outside_a_command_scope_are_refused() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant)
    seed_v4(db, tenant, pool, "192.0.2.10")
    db.commit()
    row = db.scalar(select(IPv4Address))
    row.notes = "reader mutation"
    with pytest.raises(IpamWriteOutsideCommand):
        db.flush()
    db.rollback()
    row = db.scalar(select(IPv4Address))
    db.delete(row)
    with pytest.raises(IpamWriteOutsideCommand):
        db.flush()
    db.rollback()


def test_a_command_scope_permits_writes_and_unwinds() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant)
    assert not in_command_scope()
    with command_scope():
        assert in_command_scope()
        with command_scope():
            assert in_command_scope()
        assert in_command_scope()
        db.add(IPv4Address(tenant_id=tenant, address="192.0.2.11", pool_id=pool))
        db.flush()
    assert not in_command_scope()


def test_unmodified_loaded_rows_do_not_trip_the_guard() -> None:
    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant)
    seed_v4(db, tenant, pool, "192.0.2.10")
    row = db.scalar(select(IPv4Address))
    row.notes = row.notes  # no net change
    db.flush()


def test_other_models_and_reads_are_unaffected() -> None:
    db = make_session()
    seed_tenant(db)  # Tenant is not a module-mapped class
    assert db.scalars(select(IpPool)).all() == []


def test_read_models_exports_the_mapped_classes_under_sub_names() -> None:
    expected = {
        "IPAssignment",
        "IPv4Address",
        "IPv6Address",
        "IpBlock",
        "IpPool",
        "IpPoolUtilizationSnapshot",
        "IpamEvent",
        "Ipv6DelegatedPrefix",
        "SubscriberAdditionalRoute",
    }
    assert set(read_models.__all__) == expected
    for name in expected:
        assert getattr(read_models, name) is getattr(ipam_models, name)


def test_manifest_tables_match_the_models_and_version() -> None:
    assert set(module.tables) == {m.__tablename__ for m in ipam_models.ALL_MODELS}
    assert module.version == "0.2.0a1"
    assert module.migration_prefix == "ip"


def test_every_table_is_tenant_scoped_with_a_tenant_id_key() -> None:
    for model in ipam_models.ALL_MODELS:
        table = model.__table__
        assert "tenant_id" in table.c, table.name
        assert table.schema == "mod_ipam"
        keys = [
            {col.name for col in constraint.columns}
            for constraint in table.constraints
            if constraint.__class__.__name__ == "UniqueConstraint"
        ]
        assert {"tenant_id", "id"} in keys, f"{table.name} lacks UNIQUE(tenant_id,id)"


def test_no_foreign_key_leaves_the_module_except_to_tenants() -> None:
    for model in ipam_models.ALL_MODELS:
        for fk in model.__table__.foreign_keys:
            target_schema = fk.column.table.schema
            assert target_schema in {"mod_ipam", None}, (model.__tablename__, fk)
            if target_schema is None:
                assert fk.column.table.name == "tenants"


def test_product_identities_are_opaque_columns_with_sub_names() -> None:
    assignment = ipam_models.IPAssignment.__table__
    for name in ("subscriber_id", "subscription_id"):
        assert name in assignment.c
        assert not assignment.c[name].foreign_keys
    pool = ipam_models.IpPool.__table__
    assert "site_ref" in pool.c and not pool.c.site_ref.foreign_keys
    v4 = ipam_models.IPv4Address.__table__
    assert "device_ref" in v4.c and "ont_unit_id" not in v4.c
    for gone in ("nas_device_id", "olt_device_id", "vlan_id", "next_available_ip"):
        assert gone not in pool.c


def test_partial_unique_indexes_carry_a_tenant_prefix() -> None:
    names = {i.name: i for i in ipam_models.IPAssignment.__table__.indexes}
    for name in (
        "uq_ip_assignments_ipv4_active",
        "uq_ip_assignments_ipv6_active",
        "uq_ip_assignments_primary_ipv4_active",
    ):
        index = names[name]
        assert index.unique
        assert next(iter(index.columns)).name == "tenant_id"


def test_customer_pool_without_site_is_a_database_error() -> None:
    from dotmac_ipam.models import IpPool as Pool
    from sqlalchemy.exc import IntegrityError

    db = make_session()
    tenant = seed_tenant(db)
    with command_scope():
        db.add(
            Pool(
                tenant_id=tenant,
                name="no-site",
                ip_version="ipv4",
                cidr="192.0.2.0/24",
                is_active=True,
                purpose="customer",
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
    db.rollback()


def test_inspector_sees_only_the_module_tables() -> None:
    db = make_session()
    names = set(inspect(db.get_bind()).get_table_names())
    assert {m.__tablename__ for m in ipam_models.ALL_MODELS} <= names
    assert "address_spaces" not in names and "pools" not in names


def test_no_module_source_commits_rolls_back_or_opens_its_own_session() -> None:
    """Flush-only: every extracted Sub ``db.commit()`` is gone, package-wide."""
    import ast
    from pathlib import Path

    import dotmac_ipam

    root = Path(dotmac_ipam.__file__).parent
    for path in root.rglob("*.py"):
        if "migrations" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                func = node.func
                called = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else getattr(func, "id", "")
                )
                assert called not in {
                    "commit",
                    "rollback",
                    "SessionLocal",
                    "sessionmaker",
                }, f"{path.name}:{node.lineno} calls {called}()"


def test_event_payloads_are_checked_against_the_typed_schema() -> None:
    import dataclasses

    from dotmac_ipam import EVENT_SCHEMAS, IpamError
    from dotmac_ipam._common import event

    assert set(EVENT_SCHEMAS) == {
        "AddressAssigned",
        "AssignmentReleased",
        "AssignmentMoved",
        "PrimaryChanged",
        "AssignmentRepaired",
        "PrefixDelegated",
        "RoutedPrefixChanged",
    }
    for schema in EVENT_SCHEMAS.values():
        assert dataclasses.is_dataclass(schema)
        assert schema.__dataclass_params__.frozen
    db = make_session()
    tenant = seed_tenant(db)
    with command_scope():
        with pytest.raises(IpamError, match="undeclared keys"):
            event(
                db,
                tenant_id=tenant,
                aggregate_ref="x",
                event_type="PrimaryChanged",
                payload={"assignment_id": "1", "surprise": "2"},
            )
        # free-form operational events are not in the vocabulary
        event(
            db,
            tenant_id=tenant,
            aggregate_ref="x",
            event_type="pool_created",
            payload={"a": 1},
        )
        db.flush()


def test_command_scope_is_not_a_public_export() -> None:
    import dotmac_ipam

    assert not hasattr(dotmac_ipam, "command_scope")
    assert "command_scope" not in dotmac_ipam.__all__
    assert "in_command_scope" not in dotmac_ipam.__all__


def test_bulk_dml_outside_a_command_is_refused() -> None:
    from dotmac_ipam.models import IPAssignment
    from sqlalchemy import delete, insert, update

    db = make_session()
    tenant = seed_tenant(db)
    pool = seed_pool(db, tenant)
    seed_v4(db, tenant, pool, "192.0.2.10")
    db.commit()
    with pytest.raises(IpamWriteOutsideCommand):
        db.execute(update(IPv4Address).values(notes="x"))
    with pytest.raises(IpamWriteOutsideCommand):
        db.execute(delete(IPv4Address))
    with pytest.raises(IpamWriteOutsideCommand):
        db.execute(insert(IPAssignment).values(tenant_id=tenant))
    with pytest.raises(IpamWriteOutsideCommand):
        db.execute(insert(IPv4Address.__table__).values(tenant_id=tenant))
    with pytest.raises(IpamWriteOutsideCommand):
        db.bulk_insert_mappings(IPv4Address, [{"tenant_id": tenant}])
    with pytest.raises(IpamWriteOutsideCommand):
        db.bulk_save_objects([IPv4Address(tenant_id=tenant, address="192.0.2.99")])
    db.rollback()
    assert db.scalar(select(IPv4Address).limit(1)).notes is None
    # inside a scope the same statements are permitted
    with command_scope():
        db.execute(update(IPv4Address).values(notes="ok"))
    # reads and non-module tables are unaffected
    assert db.scalar(select(IPv4Address.notes)) == "ok"


@pytest.mark.parametrize("mutation", ["insert", "update", "delete"])
@pytest.mark.parametrize("keyword_db", [False, True])
def test_noop_command_refuses_pending_caller_writes(mutation, keyword_db) -> None:
    with make_session() as db:
        tenant = seed_tenant(db)
        pool = seed_pool(db, tenant)
        seed_v4(db, tenant, pool, "192.0.2.10")
        db.commit()
        row = db.scalar(select(IPv4Address))
        if mutation == "insert":
            db.add(IPv4Address(tenant_id=tenant, pool_id=pool, address="192.0.2.11"))
        elif mutation == "update":
            row.notes = "caller mutation"
        else:
            db.delete(row)
        kwargs = {"tenant_id": tenant, "command": SweepExpired(datetime.now(UTC))}
        with pytest.raises(IpamWriteOutsideCommand, match="IPv4Address"):
            if keyword_db:
                sweep_expired(db=db, **kwargs)
            else:
                sweep_expired(db, **kwargs)
        assert not in_command_scope()
        db.rollback()
        rows = db.scalars(select(IPv4Address)).all()
        assert len(rows) == 1 and rows[0].notes is None


def test_nested_command_accepts_writes_owned_by_outer_command() -> None:
    @command
    def outer(db, *, tenant, pool):
        db.add(IPv4Address(tenant_id=tenant, pool_id=pool, address="192.0.2.11"))
        return sweep_expired(
            db, tenant_id=tenant, command=SweepExpired(datetime.now(UTC))
        )

    with make_session() as db:
        tenant = seed_tenant(db)
        pool = seed_pool(db, tenant)
        assert outer(db, tenant=tenant, pool=pool) == 0
        assert db.scalar(select(IPv4Address.address)) == "192.0.2.11"
        assert not in_command_scope()
