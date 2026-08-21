"""Flush-only owner for stock movements and their rebuildable projections."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_inventory.contracts import (
    AdjustmentCommand,
    CostingMethod,
    IssueCommand,
    ItemCreate,
    LotReceipt,
    LotStatus,
    MovementKind,
    ReceiptCommand,
    ReservationCreate,
    ReservationStatus,
    SerialStatus,
    StockIssueEvidence,
    TransferCommand,
    TransferResult,
    ValuationSnapshotCreate,
    WarehouseCreate,
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
from dotmac_inventory.valuation import (
    ValuationState,
    lower_of_cost_and_nrv,
    quantize,
    receive_weighted_average,
)


class InventoryError(ValueError):
    """Base error for a refused inventory decision."""


class InventoryNotFound(InventoryError):
    """A tenant-local inventory entity was not found."""


class InventoryConflict(InventoryError):
    """A tenant-local inventory identity conflicts with an existing row."""


class InsufficientStock(InventoryError):
    """A movement or reservation would consume unavailable stock."""


class TraceabilityRequired(InventoryError):
    """The item's lot/serial contract was not satisfied."""


class SerialUnavailable(InventoryError):
    """A serial is duplicated, absent, or not available at the warehouse."""


class ReservationConflict(InventoryError):
    """A reservation identity or lifecycle transition conflicts."""


def _clean(value: str, label: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise InventoryError(f"{label} must not be blank")
    return cleaned


def _quantity(value: Decimal, label: str = "quantity") -> Decimal:
    normalized = quantize(Decimal(value))
    if normalized <= 0:
        raise InventoryError(f"{label} must be positive")
    return normalized


def _money(value: Decimal, label: str = "unit cost") -> Decimal:
    normalized = quantize(Decimal(value))
    if normalized < 0:
        raise InventoryError(f"{label} cannot be negative")
    return normalized


def _currency(value: str) -> str:
    normalized = _clean(value, "currency code").upper()
    if len(normalized) != 3 or not normalized.isalpha():
        raise InventoryError("currency code must be a three-letter code")
    return normalized


def _item(db: Session, tenant_id: UUID, item_id: UUID) -> Item:
    row = db.scalar(
        select(Item)
        .where(Item.tenant_id == tenant_id, Item.id == item_id)
        .with_for_update()
    )
    if row is None or not row.is_active:
        raise InventoryNotFound("active inventory item not found")
    return row


def _warehouse(db: Session, tenant_id: UUID, warehouse_id: UUID) -> Warehouse:
    row = db.scalar(
        select(Warehouse)
        .where(Warehouse.tenant_id == tenant_id, Warehouse.id == warehouse_id)
        .with_for_update()
    )
    if row is None or not row.is_active:
        raise InventoryNotFound("active warehouse not found")
    return row


def _balance(
    db: Session, tenant_id: UUID, item_id: UUID, warehouse_id: UUID
) -> StockBalance:
    row = db.scalar(
        select(StockBalance)
        .where(
            StockBalance.tenant_id == tenant_id,
            StockBalance.item_id == item_id,
            StockBalance.warehouse_id == warehouse_id,
        )
        .with_for_update()
    )
    if row is None:
        row = StockBalance(
            tenant_id=tenant_id,
            item_id=item_id,
            warehouse_id=warehouse_id,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
            total_value=Decimal("0"),
            current_unit_cost=Decimal("0"),
        )
        db.add(row)
        db.flush()
    return row


def _lot_by_id(db: Session, tenant_id: UUID, lot_id: UUID) -> Lot:
    row = db.scalar(
        select(Lot)
        .where(Lot.tenant_id == tenant_id, Lot.id == lot_id)
        .with_for_update()
    )
    if row is None:
        raise InventoryNotFound("inventory lot not found")
    if row.status == LotStatus.QUARANTINED.value:
        raise TraceabilityRequired("quarantined lot cannot move")
    return row


def _receipt_lot(
    db: Session,
    *,
    tenant_id: UUID,
    item: Item,
    command: LotReceipt,
    received_at: datetime,
    unit_cost: Decimal,
) -> Lot:
    code = _clean(command.code, "lot code")
    row = db.scalar(
        select(Lot)
        .where(Lot.tenant_id == tenant_id, Lot.item_id == item.id, Lot.code == code)
        .with_for_update()
    )
    if row is None:
        row = Lot(
            tenant_id=tenant_id,
            item_id=item.id,
            code=code,
            supplier_lot_ref=command.supplier_lot_ref,
            manufacture_date=command.manufacture_date,
            expiry_date=command.expiry_date,
            received_at=received_at,
            unit_cost=unit_cost,
            status=LotStatus.AVAILABLE.value,
        )
        db.add(row)
        db.flush()
    elif row.status == LotStatus.QUARANTINED.value:
        raise TraceabilityRequired("quarantined lot cannot receive stock")
    return row


def _lot_balance(
    db: Session,
    *,
    tenant_id: UUID,
    lot_id: UUID,
    warehouse_id: UUID,
) -> LotBalance:
    row = db.scalar(
        select(LotBalance)
        .where(
            LotBalance.tenant_id == tenant_id,
            LotBalance.lot_id == lot_id,
            LotBalance.warehouse_id == warehouse_id,
        )
        .with_for_update()
    )
    if row is None:
        row = LotBalance(
            tenant_id=tenant_id,
            lot_id=lot_id,
            warehouse_id=warehouse_id,
            quantity_on_hand=Decimal("0"),
            quantity_reserved=Decimal("0"),
        )
        db.add(row)
        db.flush()
    return row


def _serial_numbers(
    item: Item, quantity: Decimal, values: tuple[str, ...]
) -> tuple[str, ...]:
    normalized = tuple(_clean(value, "serial number") for value in values)
    if len(set(normalized)) != len(normalized):
        raise SerialUnavailable("duplicate serial number in operation")
    if item.track_serials:
        if quantity != Decimal(int(quantity)):
            raise TraceabilityRequired(
                "serialized stock quantity must be a whole number"
            )
        if len(normalized) != int(quantity):
            raise TraceabilityRequired(
                "serialized stock requires exactly one serial per unit"
            )
    elif normalized:
        raise TraceabilityRequired("serial numbers supplied for a non-serialized item")
    return normalized


def _available_serials(
    db: Session,
    *,
    tenant_id: UUID,
    item_id: UUID,
    warehouse_id: UUID,
    serial_numbers: tuple[str, ...],
    lot_id: UUID | None,
) -> list[Serial]:
    if not serial_numbers:
        return []
    rows = list(
        db.scalars(
            select(Serial)
            .where(
                Serial.tenant_id == tenant_id,
                Serial.item_id == item_id,
                Serial.serial_number.in_(serial_numbers),
            )
            .with_for_update()
        ).all()
    )
    found = {row.serial_number: row for row in rows}
    for value in serial_numbers:
        row = found.get(value)
        if (
            row is None
            or row.status != SerialStatus.AVAILABLE.value
            or row.warehouse_id != warehouse_id
            or (lot_id is not None and row.lot_id != lot_id)
        ):
            raise SerialUnavailable(f"serial {value!r} is not available")
    return [found[value] for value in serial_numbers]


def _movement(
    db: Session,
    *,
    tenant_id: UUID,
    group_id: UUID,
    kind: MovementKind,
    item_id: UUID,
    warehouse_id: UUID,
    lot_id: UUID | None,
    quantity_delta: Decimal,
    unit_cost: Decimal,
    value_delta: Decimal,
    cost_variance: Decimal,
    balance: StockBalance,
    currency_code: str,
    source_ref: str,
    actor_ref: str | None,
    occurred_at: datetime,
) -> StockMovement:
    row = StockMovement(
        tenant_id=tenant_id,
        movement_group_id=group_id,
        kind=kind.value,
        item_id=item_id,
        warehouse_id=warehouse_id,
        lot_id=lot_id,
        quantity_delta=quantize(quantity_delta),
        unit_cost=quantize(unit_cost),
        value_delta=quantize(value_delta),
        cost_variance=quantize(cost_variance),
        quantity_after=quantize(balance.quantity_on_hand),
        value_after=quantize(balance.total_value),
        currency_code=_currency(currency_code),
        source_ref=_clean(source_ref, "source reference"),
        actor_ref=actor_ref,
        occurred_at=occurred_at,
    )
    db.add(row)
    db.flush()
    return row


def _link_serials(
    db: Session, *, tenant_id: UUID, movement: StockMovement, serials: list[Serial]
) -> None:
    for serial in serials:
        serial.last_movement_id = movement.id
        db.add(
            MovementSerial(
                tenant_id=tenant_id,
                movement_id=movement.id,
                serial_id=serial.id,
            )
        )


def create_item(db: Session, *, tenant_id: UUID, command: ItemCreate) -> Item:
    standard_cost = (
        _money(command.standard_cost, "standard cost")
        if command.standard_cost is not None
        else None
    )
    if command.costing_method is CostingMethod.STANDARD_COST and standard_cost is None:
        raise InventoryError("standard-cost items require standard_cost")
    row = Item(
        tenant_id=tenant_id,
        sku=_clean(command.sku, "SKU"),
        name=_clean(command.name, "item name"),
        base_uom=_clean(command.base_uom, "base UOM"),
        costing_method=command.costing_method.value,
        standard_cost=standard_cost,
        currency_code=_currency(command.currency_code),
        track_lots=command.track_lots
        or command.costing_method
        in {CostingMethod.FIFO, CostingMethod.SPECIFIC_IDENTIFICATION},
        track_serials=command.track_serials,
        is_active=True,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise InventoryConflict("inventory SKU already exists for this tenant") from exc
    return row


def create_warehouse(
    db: Session, *, tenant_id: UUID, command: WarehouseCreate
) -> Warehouse:
    row = Warehouse(
        tenant_id=tenant_id,
        code=_clean(command.code, "warehouse code"),
        name=_clean(command.name, "warehouse name"),
        allows_receipts=command.allows_receipts,
        allows_issues=command.allows_issues,
        is_active=True,
    )
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        raise InventoryConflict(
            "warehouse code already exists for this tenant"
        ) from exc
    return row


def _receive_stock(
    db: Session,
    *,
    tenant_id: UUID,
    command: ReceiptCommand,
    movement_kind: MovementKind,
) -> StockMovement:
    quantity = _quantity(command.quantity, "receipt quantity")
    purchase_cost = _money(command.unit_cost)
    item = _item(db, tenant_id, command.item_id)
    warehouse = _warehouse(db, tenant_id, command.warehouse_id)
    if _currency(command.currency_code) != item.currency_code:
        raise InventoryError("receipt currency does not match the item currency")
    if not warehouse.allows_receipts:
        raise InventoryError("warehouse does not allow receipts")
    serial_numbers = _serial_numbers(item, quantity, command.serial_numbers)
    if serial_numbers:
        duplicate = db.scalar(
            select(Serial.id).where(
                Serial.tenant_id == tenant_id,
                Serial.item_id == item.id,
                Serial.serial_number.in_(serial_numbers),
            )
        )
        if duplicate is not None:
            raise SerialUnavailable("serial number already exists")
    if item.track_lots and command.lot is None:
        raise TraceabilityRequired("lot-tracked receipt requires a lot")

    balance = _balance(db, tenant_id, item.id, warehouse.id)
    method = CostingMethod(item.costing_method)
    cost_variance = Decimal("0")
    if method is CostingMethod.STANDARD_COST:
        if item.standard_cost is None:
            raise InventoryError("standard-cost item is missing its standard cost")
        movement_cost = quantize(item.standard_cost)
        cost_variance = quantize((purchase_cost - movement_cost) * quantity)
    else:
        movement_cost = purchase_cost
    current = ValuationState(
        Decimal(balance.quantity_on_hand),
        Decimal(balance.total_value),
        Decimal(balance.current_unit_cost),
    )
    valued = receive_weighted_average(
        current, quantity=quantity, unit_cost=movement_cost
    )
    balance.quantity_on_hand = valued.quantity
    balance.total_value = valued.total_value
    balance.current_unit_cost = valued.unit_cost

    lot: Lot | None = None
    if command.lot is not None:
        lot = _receipt_lot(
            db,
            tenant_id=tenant_id,
            item=item,
            command=command.lot,
            received_at=command.occurred_at,
            unit_cost=movement_cost,
        )
        lot_balance = _lot_balance(
            db,
            tenant_id=tenant_id,
            lot_id=lot.id,
            warehouse_id=warehouse.id,
        )
        previous_lot_quantity = Decimal(lot_balance.quantity_on_hand)
        new_lot_quantity = quantize(previous_lot_quantity + quantity)
        if new_lot_quantity > 0:
            lot.unit_cost = quantize(
                (
                    previous_lot_quantity * Decimal(lot.unit_cost)
                    + quantity * movement_cost
                )
                / new_lot_quantity
            )
        lot_balance.quantity_on_hand = new_lot_quantity
        lot.status = LotStatus.AVAILABLE.value

    group_id = uuid4()
    movement = _movement(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        kind=movement_kind,
        item_id=item.id,
        warehouse_id=warehouse.id,
        lot_id=lot.id if lot is not None else None,
        quantity_delta=quantity,
        unit_cost=movement_cost,
        value_delta=quantity * movement_cost,
        cost_variance=cost_variance,
        balance=balance,
        currency_code=command.currency_code,
        source_ref=command.source_ref,
        actor_ref=command.actor_ref,
        occurred_at=command.occurred_at,
    )
    serials: list[Serial] = []
    for number in serial_numbers:
        serial = Serial(
            tenant_id=tenant_id,
            item_id=item.id,
            serial_number=number,
            warehouse_id=warehouse.id,
            lot_id=lot.id if lot is not None else None,
            status=SerialStatus.AVAILABLE.value,
            last_movement_id=movement.id,
        )
        db.add(serial)
        serials.append(serial)
    db.flush()
    _link_serials(db, tenant_id=tenant_id, movement=movement, serials=serials)
    db.flush()
    return movement


def receive_stock(
    db: Session, *, tenant_id: UUID, command: ReceiptCommand
) -> StockMovement:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            return _receive_stock(
                db,
                tenant_id=tenant_id,
                command=command,
                movement_kind=MovementKind.RECEIPT,
            )
    except IntegrityError as exc:
        raise InventoryConflict(
            "receipt identity conflicts with existing stock"
        ) from exc


def _reservation_for_issue(
    db: Session,
    *,
    tenant_id: UUID,
    reservation_id: UUID | None,
    item_id: UUID,
    warehouse_id: UUID,
    lot_id: UUID | None,
    quantity: Decimal,
) -> StockReservation | None:
    if reservation_id is None:
        return None
    row = db.scalar(
        select(StockReservation)
        .where(
            StockReservation.tenant_id == tenant_id,
            StockReservation.id == reservation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise InventoryNotFound("stock reservation not found")
    if row.status not in {
        ReservationStatus.RESERVED.value,
        ReservationStatus.PARTIALLY_FULFILLED.value,
    }:
        raise ReservationConflict("stock reservation is not active")
    if (
        row.item_id != item_id
        or row.warehouse_id != warehouse_id
        or row.lot_id != lot_id
    ):
        raise ReservationConflict("issue does not match stock reservation scope")
    if row.quantity_remaining < quantity:
        raise ReservationConflict("issue exceeds remaining reserved quantity")
    return row


def _issue_stock(
    db: Session,
    *,
    tenant_id: UUID,
    command: IssueCommand,
    movement_kind: MovementKind,
) -> StockMovement:
    quantity = _quantity(command.quantity, "issue quantity")
    item = _item(db, tenant_id, command.item_id)
    warehouse = _warehouse(db, tenant_id, command.warehouse_id)
    if not warehouse.allows_issues:
        raise InventoryError("warehouse does not allow issues")
    if item.track_lots and command.lot_id is None:
        raise TraceabilityRequired("lot-tracked issue requires a lot")
    lot = _lot_by_id(db, tenant_id, command.lot_id) if command.lot_id else None
    if lot is not None and lot.item_id != item.id:
        raise TraceabilityRequired("lot does not belong to the item")

    serial_numbers = _serial_numbers(item, quantity, command.serial_numbers)
    serials = _available_serials(
        db,
        tenant_id=tenant_id,
        item_id=item.id,
        warehouse_id=warehouse.id,
        serial_numbers=serial_numbers,
        lot_id=lot.id if lot is not None else None,
    )
    balance = _balance(db, tenant_id, item.id, warehouse.id)
    reservation = _reservation_for_issue(
        db,
        tenant_id=tenant_id,
        reservation_id=command.reservation_id,
        item_id=item.id,
        warehouse_id=warehouse.id,
        lot_id=lot.id if lot is not None else None,
        quantity=quantity,
    )
    reserved_for_this_issue = (
        Decimal(reservation.quantity_remaining)
        if reservation is not None
        else Decimal("0")
    )
    available = Decimal(balance.quantity_available) + reserved_for_this_issue
    if available < quantity:
        raise InsufficientStock(
            f"{quantize(available)} available, {quantity} requested"
        )

    lot_balance: LotBalance | None = None
    if lot is not None:
        lot_balance = _lot_balance(
            db,
            tenant_id=tenant_id,
            lot_id=lot.id,
            warehouse_id=warehouse.id,
        )
        lot_available = Decimal(lot_balance.quantity_available)
        if reservation is not None and reservation.lot_id == lot.id:
            lot_available += Decimal(reservation.quantity_remaining)
        if lot_available < quantity:
            raise InsufficientStock(
                f"{quantize(lot_available)} available in lot, {quantity} requested"
            )

    method = CostingMethod(item.costing_method)
    if method is CostingMethod.STANDARD_COST:
        if item.standard_cost is None:
            raise InventoryError("standard-cost item is missing its standard cost")
        issue_cost = quantize(item.standard_cost)
    elif method in {CostingMethod.FIFO, CostingMethod.SPECIFIC_IDENTIFICATION}:
        if lot is None:
            raise TraceabilityRequired("costing method requires a lot")
        issue_cost = quantize(lot.unit_cost)
    else:
        issue_cost = quantize(balance.current_unit_cost)
    value_out = quantize(quantity * issue_cost)
    balance.quantity_on_hand = quantize(Decimal(balance.quantity_on_hand) - quantity)
    balance.total_value = quantize(
        max(Decimal("0"), Decimal(balance.total_value) - value_out)
    )
    balance.current_unit_cost = (
        quantize(Decimal(balance.total_value) / Decimal(balance.quantity_on_hand))
        if balance.quantity_on_hand > 0
        else Decimal("0.000000")
    )
    if reservation is not None:
        reservation.quantity_fulfilled = quantize(
            Decimal(reservation.quantity_fulfilled) + quantity
        )
        balance.quantity_reserved = quantize(
            Decimal(balance.quantity_reserved) - quantity
        )
        if reservation.quantity_remaining == 0:
            reservation.status = ReservationStatus.FULFILLED.value
            reservation.fulfilled_at = command.occurred_at
        else:
            reservation.status = ReservationStatus.PARTIALLY_FULFILLED.value
    if lot_balance is not None:
        if lot is None:
            raise InventoryError("lot balance exists without a loaded lot")
        lot_balance.quantity_on_hand = quantize(
            Decimal(lot_balance.quantity_on_hand) - quantity
        )
        if reservation is not None:
            lot_balance.quantity_reserved = quantize(
                Decimal(lot_balance.quantity_reserved) - quantity
            )
        if lot_balance.quantity_on_hand == 0:
            db.flush()
            lot_total = sum(
                (
                    Decimal(row.quantity_on_hand)
                    for row in db.scalars(
                        select(LotBalance).where(
                            LotBalance.tenant_id == tenant_id,
                            LotBalance.lot_id == lot.id,
                        )
                    ).all()
                ),
                Decimal("0"),
            )
            if lot_total == 0:
                lot.status = LotStatus.DEPLETED.value

    movement = _movement(
        db,
        tenant_id=tenant_id,
        group_id=uuid4(),
        kind=movement_kind,
        item_id=item.id,
        warehouse_id=warehouse.id,
        lot_id=lot.id if lot is not None else None,
        quantity_delta=-quantity,
        unit_cost=issue_cost,
        value_delta=-value_out,
        cost_variance=Decimal("0"),
        balance=balance,
        currency_code=item.currency_code,
        source_ref=command.source_ref,
        actor_ref=command.actor_ref,
        occurred_at=command.occurred_at,
    )
    for serial in serials:
        serial.status = SerialStatus.ISSUED.value
        serial.warehouse_id = None
    _link_serials(db, tenant_id=tenant_id, movement=movement, serials=serials)
    db.flush()
    return movement


def issue_stock(
    db: Session, *, tenant_id: UUID, command: IssueCommand
) -> StockMovement:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            return _issue_stock(
                db,
                tenant_id=tenant_id,
                command=command,
                movement_kind=MovementKind.ISSUE,
            )
    except IntegrityError as exc:
        raise InventoryConflict("issue identity conflicts with existing stock") from exc


def issue_stock_evidence(
    db: Session, *, tenant_id: UUID, command: IssueCommand
) -> StockIssueEvidence:
    """Issue stock and return the immutable durable-asset handoff contract."""
    movement = issue_stock(db, tenant_id=tenant_id, command=command)
    serial_numbers = tuple(
        db.scalars(
            select(Serial.serial_number)
            .join(
                MovementSerial,
                (MovementSerial.tenant_id == Serial.tenant_id)
                & (MovementSerial.serial_id == Serial.id),
            )
            .where(
                MovementSerial.tenant_id == tenant_id,
                MovementSerial.movement_id == movement.id,
            )
            .order_by(Serial.serial_number)
        )
    )
    return StockIssueEvidence(
        movement_id=movement.id,
        tenant_id=movement.tenant_id,
        movement_group_id=movement.movement_group_id,
        item_id=movement.item_id,
        warehouse_id=movement.warehouse_id,
        lot_id=movement.lot_id,
        quantity_issued=-movement.quantity_delta,
        unit_cost=movement.unit_cost,
        value_issued=-movement.value_delta,
        currency_code=movement.currency_code,
        source_ref=movement.source_ref,
        actor_ref=movement.actor_ref,
        occurred_at=movement.occurred_at,
        serial_numbers=serial_numbers,
    )


def _adjust_stock(
    db: Session, *, tenant_id: UUID, command: AdjustmentCommand
) -> StockMovement:
    quantity_delta = quantize(Decimal(command.quantity_delta))
    if quantity_delta == 0:
        raise InventoryError("adjustment quantity cannot be zero")
    if quantity_delta > 0:
        if command.unit_cost is None or command.currency_code is None:
            raise InventoryError(
                "positive adjustment requires unit cost and currency code"
            )
        if command.lot_id is not None:
            raise TraceabilityRequired(
                "positive adjustment identifies a lot receipt, not an existing lot id"
            )
        return _receive_stock(
            db,
            tenant_id=tenant_id,
            command=ReceiptCommand(
                item_id=command.item_id,
                warehouse_id=command.warehouse_id,
                quantity=quantity_delta,
                unit_cost=command.unit_cost,
                currency_code=command.currency_code,
                source_ref=command.source_ref,
                actor_ref=command.actor_ref,
                occurred_at=command.occurred_at,
                lot=command.lot,
                serial_numbers=command.serial_numbers,
            ),
            movement_kind=MovementKind.ADJUSTMENT,
        )
    if command.lot is not None:
        raise TraceabilityRequired(
            "negative adjustment identifies an existing lot id, not a lot receipt"
        )
    return _issue_stock(
        db,
        tenant_id=tenant_id,
        command=IssueCommand(
            item_id=command.item_id,
            warehouse_id=command.warehouse_id,
            quantity=-quantity_delta,
            source_ref=command.source_ref,
            actor_ref=command.actor_ref,
            occurred_at=command.occurred_at,
            lot_id=command.lot_id,
            serial_numbers=command.serial_numbers,
        ),
        movement_kind=MovementKind.ADJUSTMENT,
    )


def adjust_stock(
    db: Session, *, tenant_id: UUID, command: AdjustmentCommand
) -> StockMovement:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            return _adjust_stock(db, tenant_id=tenant_id, command=command)
    except IntegrityError as exc:
        raise InventoryConflict(
            "adjustment identity conflicts with existing stock"
        ) from exc


def _transfer_stock(
    db: Session, *, tenant_id: UUID, command: TransferCommand
) -> TransferResult:
    quantity = _quantity(command.quantity, "transfer quantity")
    if command.source_warehouse_id == command.destination_warehouse_id:
        raise InventoryError("transfer warehouses must be different")
    item = _item(db, tenant_id, command.item_id)
    source = _warehouse(db, tenant_id, command.source_warehouse_id)
    destination = _warehouse(db, tenant_id, command.destination_warehouse_id)
    if not source.allows_issues or not destination.allows_receipts:
        raise InventoryError("warehouse transfer eligibility is not satisfied")
    if item.track_lots and command.lot_id is None:
        raise TraceabilityRequired("lot-tracked transfer requires a lot")
    lot = _lot_by_id(db, tenant_id, command.lot_id) if command.lot_id else None
    if lot is not None and lot.item_id != item.id:
        raise TraceabilityRequired("lot does not belong to the item")
    serial_numbers = _serial_numbers(item, quantity, command.serial_numbers)
    serials = _available_serials(
        db,
        tenant_id=tenant_id,
        item_id=item.id,
        warehouse_id=source.id,
        serial_numbers=serial_numbers,
        lot_id=lot.id if lot is not None else None,
    )

    source_balance = _balance(db, tenant_id, item.id, source.id)
    if source_balance.quantity_available < quantity:
        raise InsufficientStock(
            f"{quantize(source_balance.quantity_available)} available, "
            f"{quantity} requested"
        )
    destination_balance = _balance(db, tenant_id, item.id, destination.id)
    method = CostingMethod(item.costing_method)
    unit_cost = (
        quantize(lot.unit_cost)
        if lot is not None
        and method in {CostingMethod.FIFO, CostingMethod.SPECIFIC_IDENTIFICATION}
        else quantize(source_balance.current_unit_cost)
    )
    transfer_value = quantize(quantity * unit_cost)
    source_balance.quantity_on_hand = quantize(
        Decimal(source_balance.quantity_on_hand) - quantity
    )
    source_balance.total_value = quantize(
        max(Decimal("0"), Decimal(source_balance.total_value) - transfer_value)
    )
    source_balance.current_unit_cost = (
        quantize(
            Decimal(source_balance.total_value)
            / Decimal(source_balance.quantity_on_hand)
        )
        if source_balance.quantity_on_hand > 0
        else Decimal("0.000000")
    )
    destination_state = receive_weighted_average(
        ValuationState(
            Decimal(destination_balance.quantity_on_hand),
            Decimal(destination_balance.total_value),
            Decimal(destination_balance.current_unit_cost),
        ),
        quantity=quantity,
        unit_cost=unit_cost,
    )
    destination_balance.quantity_on_hand = destination_state.quantity
    destination_balance.total_value = destination_state.total_value
    destination_balance.current_unit_cost = destination_state.unit_cost

    if lot is not None:
        source_lot = _lot_balance(
            db, tenant_id=tenant_id, lot_id=lot.id, warehouse_id=source.id
        )
        if source_lot.quantity_available < quantity:
            raise InsufficientStock("insufficient available quantity in source lot")
        destination_lot = _lot_balance(
            db, tenant_id=tenant_id, lot_id=lot.id, warehouse_id=destination.id
        )
        source_lot.quantity_on_hand = quantize(
            Decimal(source_lot.quantity_on_hand) - quantity
        )
        destination_lot.quantity_on_hand = quantize(
            Decimal(destination_lot.quantity_on_hand) + quantity
        )

    group_id = uuid4()
    outbound = _movement(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        kind=MovementKind.TRANSFER_OUT,
        item_id=item.id,
        warehouse_id=source.id,
        lot_id=lot.id if lot is not None else None,
        quantity_delta=-quantity,
        unit_cost=unit_cost,
        value_delta=-transfer_value,
        cost_variance=Decimal("0"),
        balance=source_balance,
        currency_code=item.currency_code,
        source_ref=command.source_ref,
        actor_ref=command.actor_ref,
        occurred_at=command.occurred_at,
    )
    inbound = _movement(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        kind=MovementKind.TRANSFER_IN,
        item_id=item.id,
        warehouse_id=destination.id,
        lot_id=lot.id if lot is not None else None,
        quantity_delta=quantity,
        unit_cost=unit_cost,
        value_delta=transfer_value,
        cost_variance=Decimal("0"),
        balance=destination_balance,
        currency_code=item.currency_code,
        source_ref=command.source_ref,
        actor_ref=command.actor_ref,
        occurred_at=command.occurred_at,
    )
    for serial in serials:
        serial.warehouse_id = destination.id
    _link_serials(db, tenant_id=tenant_id, movement=outbound, serials=serials)
    _link_serials(db, tenant_id=tenant_id, movement=inbound, serials=serials)
    for serial in serials:
        serial.last_movement_id = inbound.id
    db.flush()
    return TransferResult(outbound=outbound, inbound=inbound)


def transfer_stock(
    db: Session, *, tenant_id: UUID, command: TransferCommand
) -> TransferResult:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            return _transfer_stock(db, tenant_id=tenant_id, command=command)
    except IntegrityError as exc:
        raise InventoryConflict(
            "transfer identity conflicts with existing stock"
        ) from exc


def _reserve_stock(
    db: Session, *, tenant_id: UUID, command: ReservationCreate
) -> StockReservation:
    quantity = _quantity(command.quantity, "reservation quantity")
    reference = _clean(command.reservation_ref, "reservation reference")
    existing = db.scalar(
        select(StockReservation)
        .where(
            StockReservation.tenant_id == tenant_id,
            StockReservation.reservation_ref == reference,
        )
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.item_id == command.item_id
            and existing.warehouse_id == command.warehouse_id
            and existing.lot_id == command.lot_id
            and Decimal(existing.quantity_reserved) == quantity
        ):
            return existing
        raise ReservationConflict(
            "reservation reference was reused with different stock"
        )
    item = _item(db, tenant_id, command.item_id)
    warehouse = _warehouse(db, tenant_id, command.warehouse_id)
    if item.track_lots and command.lot_id is None:
        raise TraceabilityRequired("lot-tracked reservation requires a lot")
    lot = _lot_by_id(db, tenant_id, command.lot_id) if command.lot_id else None
    if lot is not None and lot.item_id != item.id:
        raise TraceabilityRequired("lot does not belong to the item")
    balance = _balance(db, tenant_id, item.id, warehouse.id)
    if balance.quantity_available < quantity:
        raise InsufficientStock(
            f"{quantize(balance.quantity_available)} available, {quantity} requested"
        )
    lot_balance = None
    if lot is not None:
        lot_balance = _lot_balance(
            db, tenant_id=tenant_id, lot_id=lot.id, warehouse_id=warehouse.id
        )
        if lot_balance.quantity_available < quantity:
            raise InsufficientStock("insufficient available quantity in lot")
    balance.quantity_reserved = quantize(Decimal(balance.quantity_reserved) + quantity)
    if lot_balance is not None:
        lot_balance.quantity_reserved = quantize(
            Decimal(lot_balance.quantity_reserved) + quantity
        )
    row = StockReservation(
        tenant_id=tenant_id,
        item_id=item.id,
        warehouse_id=warehouse.id,
        lot_id=lot.id if lot is not None else None,
        reservation_ref=reference,
        quantity_reserved=quantity,
        quantity_fulfilled=Decimal("0"),
        quantity_released=Decimal("0"),
        status=ReservationStatus.RESERVED.value,
        actor_ref=command.actor_ref,
        expires_at=command.expires_at,
    )
    db.add(row)
    db.flush()
    return row


def reserve_stock(
    db: Session, *, tenant_id: UUID, command: ReservationCreate
) -> StockReservation:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            return _reserve_stock(db, tenant_id=tenant_id, command=command)
    except IntegrityError as exc:
        raise InventoryConflict(
            "reservation identity conflicts with existing stock"
        ) from exc


def _release_reservation(
    db: Session,
    *,
    reservation: StockReservation,
    status: ReservationStatus,
    occurred_at: datetime,
    reason: str | None,
) -> StockReservation:
    if reservation.status in {
        ReservationStatus.CANCELLED.value,
        ReservationStatus.EXPIRED.value,
    }:
        return reservation
    if reservation.status == ReservationStatus.FULFILLED.value:
        raise ReservationConflict("fulfilled reservation cannot be released")
    remaining = quantize(reservation.quantity_remaining)
    balance = _balance(
        db,
        reservation.tenant_id,
        reservation.item_id,
        reservation.warehouse_id,
    )
    balance.quantity_reserved = quantize(Decimal(balance.quantity_reserved) - remaining)
    if reservation.lot_id is not None:
        lot_balance = _lot_balance(
            db,
            tenant_id=reservation.tenant_id,
            lot_id=reservation.lot_id,
            warehouse_id=reservation.warehouse_id,
        )
        lot_balance.quantity_reserved = quantize(
            Decimal(lot_balance.quantity_reserved) - remaining
        )
    reservation.quantity_released = quantize(
        Decimal(reservation.quantity_released) + remaining
    )
    reservation.status = status.value
    reservation.cancelled_at = occurred_at
    reservation.cancellation_reason = reason
    db.flush()
    return reservation


def cancel_reservation(
    db: Session,
    *,
    tenant_id: UUID,
    reservation_id: UUID,
    reason: str,
    cancelled_at: datetime | None = None,
) -> StockReservation:
    row = db.scalar(
        select(StockReservation)
        .where(
            StockReservation.tenant_id == tenant_id,
            StockReservation.id == reservation_id,
        )
        .with_for_update()
    )
    if row is None:
        raise InventoryNotFound("stock reservation not found")
    return _release_reservation(
        db,
        reservation=row,
        status=ReservationStatus.CANCELLED,
        occurred_at=cancelled_at or datetime.now(UTC),
        reason=_clean(reason, "cancellation reason"),
    )


def expire_reservations(
    db: Session, *, tenant_id: UUID, expired_at: datetime
) -> tuple[StockReservation, ...]:
    rows = list(
        db.scalars(
            select(StockReservation)
            .where(
                StockReservation.tenant_id == tenant_id,
                StockReservation.status.in_(
                    (
                        ReservationStatus.RESERVED.value,
                        ReservationStatus.PARTIALLY_FULFILLED.value,
                    )
                ),
                StockReservation.expires_at.is_not(None),
                StockReservation.expires_at <= expired_at,
            )
            .with_for_update()
        ).all()
    )
    for row in rows:
        _release_reservation(
            db,
            reservation=row,
            status=ReservationStatus.EXPIRED,
            occurred_at=expired_at,
            reason="expired",
        )
    db.flush()
    return tuple(rows)


def _rebuild_balance(
    db: Session,
    *,
    tenant_id: UUID,
    item_id: UUID,
    warehouse_id: UUID,
) -> StockBalance:
    _item(db, tenant_id, item_id)
    _warehouse(db, tenant_id, warehouse_id)
    balance = _balance(db, tenant_id, item_id, warehouse_id)
    movements = list(
        db.scalars(
            select(StockMovement).where(
                StockMovement.tenant_id == tenant_id,
                StockMovement.item_id == item_id,
                StockMovement.warehouse_id == warehouse_id,
            )
        ).all()
    )
    quantity_on_hand = quantize(
        sum((Decimal(row.quantity_delta) for row in movements), Decimal("0"))
    )
    total_value = quantize(
        sum((Decimal(row.value_delta) for row in movements), Decimal("0"))
    )
    if quantity_on_hand < 0 or total_value < 0:
        raise InventoryError("movement ledger rebuild produced negative stock or value")
    reservations = list(
        db.scalars(
            select(StockReservation).where(
                StockReservation.tenant_id == tenant_id,
                StockReservation.item_id == item_id,
                StockReservation.warehouse_id == warehouse_id,
                StockReservation.status.in_(
                    (
                        ReservationStatus.RESERVED.value,
                        ReservationStatus.PARTIALLY_FULFILLED.value,
                    )
                ),
            )
        ).all()
    )
    reserved = quantize(
        sum((Decimal(row.quantity_remaining) for row in reservations), Decimal("0"))
    )
    if reserved > quantity_on_hand:
        raise InventoryError("active reservations exceed rebuilt stock")
    balance.quantity_on_hand = quantity_on_hand
    balance.quantity_reserved = reserved
    balance.total_value = total_value
    balance.current_unit_cost = (
        quantize(total_value / quantity_on_hand)
        if quantity_on_hand > 0
        else Decimal("0.000000")
    )
    db.flush()
    return balance


def rebuild_balance(
    db: Session,
    *,
    tenant_id: UUID,
    item_id: UUID,
    warehouse_id: UUID,
) -> StockBalance:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            return _rebuild_balance(
                db,
                tenant_id=tenant_id,
                item_id=item_id,
                warehouse_id=warehouse_id,
            )
    except IntegrityError as exc:
        raise InventoryConflict(
            "balance identity conflicts with existing stock"
        ) from exc


def _record_valuation_snapshot(
    db: Session,
    *,
    tenant_id: UUID,
    command: ValuationSnapshotCreate,
) -> ValuationSnapshot:
    item = _item(db, tenant_id, command.item_id)
    warehouse = _warehouse(db, tenant_id, command.warehouse_id)
    balance = _balance(db, tenant_id, item.id, warehouse.id)
    currency_code = _currency(command.currency_code)
    if currency_code != item.currency_code:
        raise InventoryError("valuation currency does not match the item currency")
    if command.lot_id is None:
        quantity_on_hand = quantize(Decimal(balance.quantity_on_hand))
        unit_cost = quantize(Decimal(balance.current_unit_cost))
        cost = quantize(Decimal(balance.total_value))
    else:
        lot = _lot_by_id(db, tenant_id, command.lot_id)
        if lot.item_id != item.id:
            raise TraceabilityRequired("lot does not belong to the item")
        lot_balance = _lot_balance(
            db,
            tenant_id=tenant_id,
            lot_id=lot.id,
            warehouse_id=warehouse.id,
        )
        quantity_on_hand = quantize(Decimal(lot_balance.quantity_on_hand))
        unit_cost = quantize(Decimal(lot.unit_cost))
        cost = quantize(quantity_on_hand * unit_cost)
    if command.estimated_selling_price is None:
        nrv = None
        carrying = cost
        write_down = Decimal("0.000000")
    else:
        result = lower_of_cost_and_nrv(
            cost=cost,
            estimated_selling_price=command.estimated_selling_price,
            costs_to_complete=command.costs_to_complete,
            selling_costs=command.selling_costs,
        )
        nrv = result.nrv
        carrying = result.carrying_amount
        write_down = result.write_down
    row = ValuationSnapshot(
        tenant_id=tenant_id,
        valuation_ref=_clean(command.valuation_ref, "valuation reference"),
        as_of_date=command.as_of_date,
        item_id=item.id,
        warehouse_id=warehouse.id,
        lot_id=command.lot_id,
        quantity_on_hand=quantity_on_hand,
        unit_cost=unit_cost,
        total_cost=cost,
        costing_method=item.costing_method,
        net_realizable_value=nrv,
        carrying_amount=carrying,
        write_down=write_down,
        currency_code=currency_code,
    )
    db.add(row)
    db.flush()
    return row


def record_valuation_snapshot(
    db: Session,
    *,
    tenant_id: UUID,
    command: ValuationSnapshotCreate,
) -> ValuationSnapshot:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            return _record_valuation_snapshot(
                db,
                tenant_id=tenant_id,
                command=command,
            )
    except IntegrityError as exc:
        raise InventoryConflict(
            "valuation identity conflicts with existing stock"
        ) from exc


__all__ = [
    "InsufficientStock",
    "InventoryConflict",
    "InventoryError",
    "InventoryNotFound",
    "ReservationConflict",
    "SerialUnavailable",
    "TraceabilityRequired",
    "adjust_stock",
    "cancel_reservation",
    "create_item",
    "create_warehouse",
    "expire_reservations",
    "issue_stock",
    "issue_stock_evidence",
    "receive_stock",
    "rebuild_balance",
    "record_valuation_snapshot",
    "reserve_stock",
    "transfer_stock",
]
