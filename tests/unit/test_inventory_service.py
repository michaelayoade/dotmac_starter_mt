"""Flush-only stock-ledger behavior for the reusable inventory owner."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from dotmac_inventory import (
    AdjustmentCommand,
    CostingMethod,
    InsufficientStock,
    InventoryConflict,
    InventoryError,
    IssueCommand,
    ItemCreate,
    LotReceipt,
    ReceiptCommand,
    ReservationCreate,
    ReservationStatus,
    SerialUnavailable,
    TransferCommand,
    ValuationSnapshotCreate,
    WarehouseCreate,
    adjust_stock,
    cancel_reservation,
    create_item,
    create_warehouse,
    issue_stock,
    issue_stock_evidence,
    rebuild_balance,
    receive_stock,
    record_valuation_snapshot,
    reserve_stock,
    transfer_stock,
)
from dotmac_inventory.models import (
    Item,
    Lot,
    LotBalance,
    MovementSerial,
    Serial,
    StockBalance,
    StockMovement,
    StockReservation,
    ValuationSnapshot,
    Warehouse,
)
from dotmac_kernel.models import Base, Tenant
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_inventory": None}},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            Tenant.__table__,
            Item.__table__,
            Warehouse.__table__,
            StockBalance.__table__,
            Lot.__table__,
            LotBalance.__table__,
            Serial.__table__,
            StockMovement.__table__,
            MovementSerial.__table__,
            StockReservation.__table__,
            ValuationSnapshot.__table__,
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


def _item(db: Session, tenant_id: UUID, **overrides) -> Item:
    data = {
        "sku": f"SKU-{uuid4().hex[:8]}",
        "name": "Drop cable",
        "base_uom": "m",
        "costing_method": CostingMethod.WEIGHTED_AVERAGE,
    }
    data.update(overrides)
    return create_item(db, tenant_id=tenant_id, command=ItemCreate(**data))


def _warehouse(db: Session, tenant_id: UUID, code: str) -> Warehouse:
    return create_warehouse(
        db,
        tenant_id=tenant_id,
        command=WarehouseCreate(code=code, name=f"{code} warehouse"),
    )


def _receipt(item: Item, warehouse: Warehouse, quantity: str, cost: str):
    return ReceiptCommand(
        item_id=item.id,
        warehouse_id=warehouse.id,
        quantity=Decimal(quantity),
        unit_cost=Decimal(cost),
        currency_code="NGN",
        source_ref=f"receipt:{uuid4()}",
        actor_ref="party:operator-1",
        occurred_at=datetime.now(UTC),
    )


def test_receipt_issue_and_rebuild_share_one_ledger_math(db: Session) -> None:
    tenant_id = _tenant(db)
    item = _item(db, tenant_id)
    warehouse = _warehouse(db, tenant_id, "ABJ")

    receive_stock(db, tenant_id=tenant_id, command=_receipt(item, warehouse, "10", "5"))
    receive_stock(db, tenant_id=tenant_id, command=_receipt(item, warehouse, "10", "7"))
    issue = issue_stock(
        db,
        tenant_id=tenant_id,
        command=IssueCommand(
            item_id=item.id,
            warehouse_id=warehouse.id,
            quantity=Decimal("5"),
            source_ref="issue:WO-1",
            actor_ref="party:operator-1",
            occurred_at=datetime.now(UTC),
        ),
    )

    balance = db.scalar(select(StockBalance))
    assert balance is not None
    assert balance.quantity_on_hand == Decimal("15.000000")
    assert balance.total_value == Decimal("90.000000")
    assert balance.current_unit_cost == Decimal("6.000000")
    assert issue.value_delta == Decimal("-30.000000")

    balance.quantity_on_hand = Decimal("999")
    balance.total_value = Decimal("1")
    rebuilt = rebuild_balance(
        db, tenant_id=tenant_id, item_id=item.id, warehouse_id=warehouse.id
    )
    assert rebuilt.quantity_on_hand == Decimal("15.000000")
    assert rebuilt.total_value == Decimal("90.000000")


def test_issue_exposes_immutable_asset_registration_evidence(db: Session) -> None:
    tenant_id = _tenant(db)
    item = _item(db, tenant_id)
    warehouse = _warehouse(db, tenant_id, "HANDOFF")
    receive_stock(db, tenant_id=tenant_id, command=_receipt(item, warehouse, "2", "5"))

    evidence = issue_stock_evidence(
        db,
        tenant_id=tenant_id,
        command=IssueCommand(
            item_id=item.id,
            warehouse_id=warehouse.id,
            quantity=Decimal("1"),
            source_ref="asset-registration:opaque-1",
            actor_ref="party:operator-1",
            occurred_at=datetime.now(UTC),
        ),
    )

    assert evidence.quantity_issued == Decimal("1.000000")
    assert evidence.value_issued == Decimal("5.000000")
    assert evidence.source_ref == "asset-registration:opaque-1"


def test_identity_conflict_preserves_the_outer_transaction(db: Session) -> None:
    tenant_id = _tenant(db)
    command = ItemCreate(
        sku="SKU-CONFLICT",
        name="First",
        base_uom="each",
    )
    item = create_item(db, tenant_id=tenant_id, command=command)

    with pytest.raises(InventoryConflict, match="SKU already exists"):
        create_item(db, tenant_id=tenant_id, command=command)

    warehouse = _warehouse(db, tenant_id, "AFTER-CONFLICT")
    assert db.get(Item, item.id) is item
    assert warehouse.code == "AFTER-CONFLICT"


def test_issue_never_drives_stock_negative(db: Session) -> None:
    tenant_id = _tenant(db)
    item = _item(db, tenant_id)
    warehouse = _warehouse(db, tenant_id, "LOS")
    receive_stock(db, tenant_id=tenant_id, command=_receipt(item, warehouse, "2", "5"))

    with pytest.raises(InsufficientStock, match="2.000000 available"):
        issue_stock(
            db,
            tenant_id=tenant_id,
            command=IssueCommand(
                item_id=item.id,
                warehouse_id=warehouse.id,
                quantity=Decimal("3"),
                source_ref="issue:too-many",
                actor_ref=None,
                occurred_at=datetime.now(UTC),
            ),
        )


def test_reservation_is_the_only_writer_of_reserved_projection(db: Session) -> None:
    tenant_id = _tenant(db)
    item = _item(db, tenant_id)
    warehouse = _warehouse(db, tenant_id, "GWA")
    receive_stock(db, tenant_id=tenant_id, command=_receipt(item, warehouse, "10", "5"))

    reservation = reserve_stock(
        db,
        tenant_id=tenant_id,
        command=ReservationCreate(
            item_id=item.id,
            warehouse_id=warehouse.id,
            quantity=Decimal("6"),
            reservation_ref="work-order:WO-42:line-1",
            actor_ref="party:dispatcher",
        ),
    )
    issue_stock(
        db,
        tenant_id=tenant_id,
        command=IssueCommand(
            item_id=item.id,
            warehouse_id=warehouse.id,
            quantity=Decimal("4"),
            source_ref="issue:WO-42:line-1",
            actor_ref="party:storekeeper",
            occurred_at=datetime.now(UTC),
            reservation_id=reservation.id,
        ),
    )

    assert reservation.status == ReservationStatus.PARTIALLY_FULFILLED.value
    assert reservation.quantity_fulfilled == Decimal("4.000000")
    balance = db.scalar(select(StockBalance))
    assert balance is not None
    assert balance.quantity_on_hand == Decimal("6.000000")
    assert balance.quantity_reserved == Decimal("2.000000")
    assert balance.quantity_available == Decimal("4.000000")

    cancel_reservation(
        db,
        tenant_id=tenant_id,
        reservation_id=reservation.id,
        reason="work cancelled",
    )
    assert reservation.status == ReservationStatus.CANCELLED.value
    assert balance.quantity_reserved == Decimal("0.000000")


def test_transfer_writes_paired_value_conserving_legs(db: Session) -> None:
    tenant_id = _tenant(db)
    item = _item(db, tenant_id)
    source = _warehouse(db, tenant_id, "SOURCE")
    destination = _warehouse(db, tenant_id, "DEST")
    receive_stock(db, tenant_id=tenant_id, command=_receipt(item, source, "10", "8"))

    result = transfer_stock(
        db,
        tenant_id=tenant_id,
        command=TransferCommand(
            item_id=item.id,
            source_warehouse_id=source.id,
            destination_warehouse_id=destination.id,
            quantity=Decimal("3"),
            source_ref="transfer:TR-1",
            actor_ref="party:storekeeper",
            occurred_at=datetime.now(UTC),
        ),
    )

    assert result.outbound.movement_group_id == result.inbound.movement_group_id
    assert result.outbound.quantity_delta + result.inbound.quantity_delta == 0
    assert result.outbound.value_delta + result.inbound.value_delta == 0
    balances = {row.warehouse_id: row for row in db.scalars(select(StockBalance)).all()}
    assert balances[source.id].quantity_on_hand == Decimal("7.000000")
    assert balances[destination.id].quantity_on_hand == Decimal("3.000000")
    assert balances[destination.id].total_value == Decimal("24.000000")


def test_adjustments_are_signed_ledger_evidence_and_rebuildable(db: Session) -> None:
    tenant_id = _tenant(db)
    item = _item(db, tenant_id)
    warehouse = _warehouse(db, tenant_id, "COUNT")

    gain = adjust_stock(
        db,
        tenant_id=tenant_id,
        command=AdjustmentCommand(
            item_id=item.id,
            warehouse_id=warehouse.id,
            quantity_delta=Decimal("3"),
            unit_cost=Decimal("4"),
            currency_code="NGN",
            source_ref="count:2026-08:gain",
            actor_ref="party:storekeeper",
            occurred_at=datetime.now(UTC),
        ),
    )
    loss = adjust_stock(
        db,
        tenant_id=tenant_id,
        command=AdjustmentCommand(
            item_id=item.id,
            warehouse_id=warehouse.id,
            quantity_delta=Decimal("-1"),
            source_ref="count:2026-08:loss",
            actor_ref="party:storekeeper",
            occurred_at=datetime.now(UTC),
        ),
    )

    assert gain.kind == loss.kind == "adjustment"
    assert gain.quantity_delta == Decimal("3.000000")
    assert loss.quantity_delta == Decimal("-1.000000")
    rebuilt = rebuild_balance(
        db, tenant_id=tenant_id, item_id=item.id, warehouse_id=warehouse.id
    )
    assert rebuilt.quantity_on_hand == Decimal("2.000000")
    assert rebuilt.total_value == Decimal("8.000000")


def test_receipt_refuses_a_currency_outside_the_item_ledger(db: Session) -> None:
    tenant_id = _tenant(db)
    item = _item(db, tenant_id)
    warehouse = _warehouse(db, tenant_id, "FX")
    command = _receipt(item, warehouse, "1", "10")

    with pytest.raises(InventoryError, match="currency does not match"):
        receive_stock(
            db,
            tenant_id=tenant_id,
            command=ReceiptCommand(**{**command.__dict__, "currency_code": "USD"}),
        )


def test_lot_and_serial_receipt_and_issue_preserve_traceability(db: Session) -> None:
    tenant_id = _tenant(db)
    item = _item(
        db,
        tenant_id,
        name="ONT",
        base_uom="each",
        track_lots=True,
        track_serials=True,
    )
    warehouse = _warehouse(db, tenant_id, "SERIAL")
    command = _receipt(item, warehouse, "2", "45000")
    command = ReceiptCommand(
        **{
            **command.__dict__,
            "lot": LotReceipt(code="BATCH-2026-08", supplier_lot_ref="OEM-44"),
            "serial_numbers": ("ONT-001", "ONT-002"),
        }
    )
    receive_stock(db, tenant_id=tenant_id, command=command)

    lot = db.scalar(select(Lot))
    serials = {row.serial_number: row for row in db.scalars(select(Serial)).all()}
    assert lot is not None
    assert set(serials) == {"ONT-001", "ONT-002"}
    assert {row.lot_id for row in serials.values()} == {lot.id}

    with pytest.raises(SerialUnavailable, match="already exists"):
        receive_stock(
            db,
            tenant_id=tenant_id,
            command=ReceiptCommand(
                **{
                    **_receipt(item, warehouse, "1", "45000").__dict__,
                    "lot": LotReceipt(code="BATCH-2026-08"),
                    "serial_numbers": ("ONT-001",),
                }
            ),
        )

    issue_stock(
        db,
        tenant_id=tenant_id,
        command=IssueCommand(
            item_id=item.id,
            warehouse_id=warehouse.id,
            quantity=Decimal("1"),
            source_ref="issue:install-1",
            actor_ref="party:storekeeper",
            occurred_at=datetime.now(UTC),
            lot_id=lot.id,
            serial_numbers=("ONT-001",),
        ),
    )
    assert serials["ONT-001"].status == "issued"
    assert serials["ONT-001"].warehouse_id is None
    assert serials["ONT-002"].status == "available"
    assert len(db.scalars(select(MovementSerial)).all()) == 3


def test_lot_valuation_uses_only_that_lots_quantity_and_cost(db: Session) -> None:
    tenant_id = _tenant(db)
    item = _item(db, tenant_id, track_lots=True)
    warehouse = _warehouse(db, tenant_id, "VALUE")
    for code, quantity, cost in (("LOT-A", "2", "10"), ("LOT-B", "3", "20")):
        command = _receipt(item, warehouse, quantity, cost)
        receive_stock(
            db,
            tenant_id=tenant_id,
            command=ReceiptCommand(
                **{**command.__dict__, "lot": LotReceipt(code=code)}
            ),
        )
    lot_a = db.scalar(select(Lot).where(Lot.code == "LOT-A"))
    assert lot_a is not None

    snapshot = record_valuation_snapshot(
        db,
        tenant_id=tenant_id,
        command=ValuationSnapshotCreate(
            item_id=item.id,
            warehouse_id=warehouse.id,
            lot_id=lot_a.id,
            valuation_ref="valuation:LOT-A:2026-08-18",
            as_of_date=datetime.now(UTC).date(),
            currency_code="NGN",
        ),
    )

    assert snapshot.quantity_on_hand == Decimal("2.000000")
    assert snapshot.unit_cost == Decimal("10.000000")
    assert snapshot.total_cost == Decimal("20.000000")
