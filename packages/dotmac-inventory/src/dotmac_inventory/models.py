"""Tenant-scoped persistence for inventory stock, traceability, and valuation."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

SCHEMA = module_schema("inventory")
_QUANTITY = Numeric(20, 6)
_MONEY = Numeric(24, 6)


def _tenant_id() -> Mapped[UUID]:
    return mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )


class Item(Base, TimestampMixin):
    __tablename__ = "items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_inventory_items_tenant_id_id"),
        UniqueConstraint("tenant_id", "sku", name="uq_inventory_items_tenant_sku"),
        CheckConstraint(
            "standard_cost IS NULL OR standard_cost >= 0",
            name="ck_inventory_items_standard_cost_nonnegative",
        ),
        Index("ix_inventory_items_tenant_active", "tenant_id", "is_active"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    sku: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_uom: Mapped[str] = mapped_column(String(32), nullable=False)
    costing_method: Mapped[str] = mapped_column(String(32), nullable=False)
    standard_cost: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    track_lots: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    track_serials: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Warehouse(Base, TimestampMixin):
    __tablename__ = "warehouses"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_inventory_warehouses_tenant_id_id"
        ),
        UniqueConstraint(
            "tenant_id", "code", name="uq_inventory_warehouses_tenant_code"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    allows_receipts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    allows_issues: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class StockBalance(Base, TimestampMixin):
    __tablename__ = "stock_balances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "item_id"],
            [f"{SCHEMA}.items.tenant_id", f"{SCHEMA}.items.id"],
            name="fk_inventory_stock_balances_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "warehouse_id"],
            [f"{SCHEMA}.warehouses.tenant_id", f"{SCHEMA}.warehouses.id"],
            name="fk_inventory_stock_balances_warehouse",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "item_id", "warehouse_id", name="uq_inventory_stock_balance"
        ),
        CheckConstraint(
            "quantity_on_hand >= 0", name="ck_inventory_stock_on_hand_nonnegative"
        ),
        CheckConstraint(
            "quantity_reserved >= 0",
            name="ck_inventory_stock_reserved_nonnegative",
        ),
        CheckConstraint(
            "quantity_reserved <= quantity_on_hand",
            name="ck_inventory_stock_reserved_lte_on_hand",
        ),
        CheckConstraint(
            "total_value >= 0", name="ck_inventory_stock_value_nonnegative"
        ),
        CheckConstraint(
            "current_unit_cost >= 0", name="ck_inventory_stock_cost_nonnegative"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    item_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    warehouse_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    quantity_on_hand: Mapped[Decimal] = mapped_column(
        _QUANTITY, nullable=False, default=Decimal("0")
    )
    quantity_reserved: Mapped[Decimal] = mapped_column(
        _QUANTITY, nullable=False, default=Decimal("0")
    )
    total_value: Mapped[Decimal] = mapped_column(
        _MONEY, nullable=False, default=Decimal("0")
    )
    current_unit_cost: Mapped[Decimal] = mapped_column(
        _MONEY, nullable=False, default=Decimal("0")
    )

    @property
    def quantity_available(self) -> Decimal:
        return self.quantity_on_hand - self.quantity_reserved


class Lot(Base, TimestampMixin):
    __tablename__ = "lots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "item_id"],
            [f"{SCHEMA}.items.tenant_id", f"{SCHEMA}.items.id"],
            name="fk_inventory_lots_item",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_lots_tenant_id_id"),
        UniqueConstraint(
            "tenant_id", "item_id", "code", name="uq_inventory_lots_item_code"
        ),
        CheckConstraint("unit_cost >= 0", name="ck_inventory_lots_cost_nonnegative"),
        Index("ix_inventory_lots_fifo", "tenant_id", "item_id", "received_at"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    item_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    supplier_lot_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    manufacture_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    unit_cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    quarantine_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)


class LotBalance(Base, TimestampMixin):
    __tablename__ = "lot_balances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "lot_id"],
            [f"{SCHEMA}.lots.tenant_id", f"{SCHEMA}.lots.id"],
            name="fk_inventory_lot_balances_lot",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "warehouse_id"],
            [f"{SCHEMA}.warehouses.tenant_id", f"{SCHEMA}.warehouses.id"],
            name="fk_inventory_lot_balances_warehouse",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "lot_id", "warehouse_id", name="uq_inventory_lot_balance"
        ),
        CheckConstraint(
            "quantity_on_hand >= 0", name="ck_inventory_lot_on_hand_nonnegative"
        ),
        CheckConstraint(
            "quantity_reserved >= 0", name="ck_inventory_lot_reserved_nonnegative"
        ),
        CheckConstraint(
            "quantity_reserved <= quantity_on_hand",
            name="ck_inventory_lot_reserved_lte_on_hand",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    lot_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    warehouse_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    quantity_on_hand: Mapped[Decimal] = mapped_column(
        _QUANTITY, nullable=False, default=Decimal("0")
    )
    quantity_reserved: Mapped[Decimal] = mapped_column(
        _QUANTITY, nullable=False, default=Decimal("0")
    )

    @property
    def quantity_available(self) -> Decimal:
        return self.quantity_on_hand - self.quantity_reserved


class Serial(Base, TimestampMixin):
    __tablename__ = "serials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "item_id"],
            [f"{SCHEMA}.items.tenant_id", f"{SCHEMA}.items.id"],
            name="fk_inventory_serials_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "warehouse_id"],
            [f"{SCHEMA}.warehouses.tenant_id", f"{SCHEMA}.warehouses.id"],
            name="fk_inventory_serials_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "lot_id"],
            [f"{SCHEMA}.lots.tenant_id", f"{SCHEMA}.lots.id"],
            name="fk_inventory_serials_lot",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_serials_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "item_id",
            "serial_number",
            name="uq_inventory_serials_item_number",
        ),
        Index("ix_inventory_serials_warehouse", "tenant_id", "warehouse_id", "status"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    item_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    serial_number: Mapped[str] = mapped_column(String(160), nullable=False)
    warehouse_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    lot_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    last_movement_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)


class StockMovement(Base, TimestampMixin):
    __tablename__ = "stock_movements"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "item_id"],
            [f"{SCHEMA}.items.tenant_id", f"{SCHEMA}.items.id"],
            name="fk_inventory_movements_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "warehouse_id"],
            [f"{SCHEMA}.warehouses.tenant_id", f"{SCHEMA}.warehouses.id"],
            name="fk_inventory_movements_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "lot_id"],
            [f"{SCHEMA}.lots.tenant_id", f"{SCHEMA}.lots.id"],
            name="fk_inventory_movements_lot",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_inventory_movements_tenant_id_id"),
        CheckConstraint(
            "quantity_delta <> 0", name="ck_inventory_movement_quantity_nonzero"
        ),
        CheckConstraint(
            "unit_cost >= 0", name="ck_inventory_movement_cost_nonnegative"
        ),
        CheckConstraint(
            "quantity_after >= 0", name="ck_inventory_movement_after_nonnegative"
        ),
        CheckConstraint(
            "value_after >= 0", name="ck_inventory_movement_value_after_nonnegative"
        ),
        Index(
            "ix_inventory_movements_balance",
            "tenant_id",
            "item_id",
            "warehouse_id",
            "occurred_at",
        ),
        Index("ix_inventory_movements_group", "tenant_id", "movement_group_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    movement_group_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    item_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    warehouse_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    lot_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    quantity_delta: Mapped[Decimal] = mapped_column(_QUANTITY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    value_delta: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    cost_variance: Mapped[Decimal] = mapped_column(
        _MONEY, nullable=False, default=Decimal("0")
    )
    quantity_after: Mapped[Decimal] = mapped_column(_QUANTITY, nullable=False)
    value_after: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    actor_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class MovementSerial(Base, TimestampMixin):
    __tablename__ = "movement_serials"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "movement_id"],
            [f"{SCHEMA}.stock_movements.tenant_id", f"{SCHEMA}.stock_movements.id"],
            name="fk_inventory_movement_serials_movement",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "serial_id"],
            [f"{SCHEMA}.serials.tenant_id", f"{SCHEMA}.serials.id"],
            name="fk_inventory_movement_serials_serial",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "movement_id", "serial_id", name="uq_inventory_movement_serial"
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    movement_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    serial_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)


class StockReservation(Base, TimestampMixin):
    __tablename__ = "stock_reservations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "item_id"],
            [f"{SCHEMA}.items.tenant_id", f"{SCHEMA}.items.id"],
            name="fk_inventory_reservations_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "warehouse_id"],
            [f"{SCHEMA}.warehouses.tenant_id", f"{SCHEMA}.warehouses.id"],
            name="fk_inventory_reservations_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "lot_id"],
            [f"{SCHEMA}.lots.tenant_id", f"{SCHEMA}.lots.id"],
            name="fk_inventory_reservations_lot",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "reservation_ref", name="uq_inventory_reservation_ref"
        ),
        CheckConstraint(
            "quantity_reserved > 0", name="ck_inventory_reservation_quantity_positive"
        ),
        CheckConstraint(
            "quantity_fulfilled >= 0",
            name="ck_inventory_reservation_fulfilled_nonnegative",
        ),
        CheckConstraint(
            "quantity_released >= 0",
            name="ck_inventory_reservation_released_nonnegative",
        ),
        CheckConstraint(
            "quantity_fulfilled + quantity_released <= quantity_reserved",
            name="ck_inventory_reservation_consumed_lte_reserved",
        ),
        Index(
            "ix_inventory_reservations_active",
            "tenant_id",
            "item_id",
            "warehouse_id",
            "status",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    item_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    warehouse_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    lot_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    reservation_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    quantity_reserved: Mapped[Decimal] = mapped_column(_QUANTITY, nullable=False)
    quantity_fulfilled: Mapped[Decimal] = mapped_column(
        _QUANTITY, nullable=False, default=Decimal("0")
    )
    quantity_released: Mapped[Decimal] = mapped_column(
        _QUANTITY, nullable=False, default=Decimal("0")
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancellation_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)

    @property
    def quantity_remaining(self) -> Decimal:
        return self.quantity_reserved - self.quantity_fulfilled - self.quantity_released


class ValuationSnapshot(Base, TimestampMixin):
    __tablename__ = "valuation_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "item_id"],
            [f"{SCHEMA}.items.tenant_id", f"{SCHEMA}.items.id"],
            name="fk_inventory_valuations_item",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "warehouse_id"],
            [f"{SCHEMA}.warehouses.tenant_id", f"{SCHEMA}.warehouses.id"],
            name="fk_inventory_valuations_warehouse",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "lot_id"],
            [f"{SCHEMA}.lots.tenant_id", f"{SCHEMA}.lots.id"],
            name="fk_inventory_valuations_lot",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id", "valuation_ref", name="uq_inventory_valuation_ref"
        ),
        CheckConstraint(
            "quantity_on_hand >= 0", name="ck_inventory_valuation_quantity_nonnegative"
        ),
        CheckConstraint(
            "total_cost >= 0", name="ck_inventory_valuation_cost_nonnegative"
        ),
        CheckConstraint(
            "carrying_amount >= 0", name="ck_inventory_valuation_carrying_nonnegative"
        ),
        CheckConstraint(
            "write_down >= 0", name="ck_inventory_valuation_write_down_nonnegative"
        ),
        Index("ix_inventory_valuations_as_of", "tenant_id", "as_of_date"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = _tenant_id()
    valuation_ref: Mapped[str] = mapped_column(String(240), nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    item_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    warehouse_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    lot_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    quantity_on_hand: Mapped[Decimal] = mapped_column(_QUANTITY, nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    costing_method: Mapped[str] = mapped_column(String(32), nullable=False)
    net_realizable_value: Mapped[Decimal | None] = mapped_column(_MONEY, nullable=True)
    carrying_amount: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    write_down: Mapped[Decimal] = mapped_column(_MONEY, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)


TENANT_MODELS = (
    Item,
    Warehouse,
    StockBalance,
    Lot,
    LotBalance,
    Serial,
    StockMovement,
    MovementSerial,
    StockReservation,
    ValuationSnapshot,
)
TENANT_TABLES = tuple(model.__tablename__ for model in TENANT_MODELS)

__all__ = [
    "SCHEMA",
    "TENANT_MODELS",
    "TENANT_TABLES",
    "Item",
    "Lot",
    "LotBalance",
    "MovementSerial",
    "Serial",
    "StockBalance",
    "StockMovement",
    "StockReservation",
    "ValuationSnapshot",
    "Warehouse",
]
