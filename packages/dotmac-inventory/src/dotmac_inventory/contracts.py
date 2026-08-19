"""Product-neutral commands and vocabularies for inventory operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from dotmac_inventory.models import StockMovement


class CostingMethod(StrEnum):
    WEIGHTED_AVERAGE = "weighted_average"
    FIFO = "fifo"
    SPECIFIC_IDENTIFICATION = "specific_identification"
    STANDARD_COST = "standard_cost"


class MovementKind(StrEnum):
    RECEIPT = "receipt"
    ISSUE = "issue"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"
    ADJUSTMENT = "adjustment"


class ReservationStatus(StrEnum):
    RESERVED = "reserved"
    PARTIALLY_FULFILLED = "partially_fulfilled"
    FULFILLED = "fulfilled"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class LotStatus(StrEnum):
    AVAILABLE = "available"
    QUARANTINED = "quarantined"
    DEPLETED = "depleted"


class SerialStatus(StrEnum):
    AVAILABLE = "available"
    ISSUED = "issued"
    QUARANTINED = "quarantined"


@dataclass(frozen=True)
class ItemCreate:
    sku: str
    name: str
    base_uom: str
    costing_method: CostingMethod = CostingMethod.WEIGHTED_AVERAGE
    track_lots: bool = False
    track_serials: bool = False
    standard_cost: Decimal | None = None
    currency_code: str = "NGN"


@dataclass(frozen=True)
class WarehouseCreate:
    code: str
    name: str
    allows_receipts: bool = True
    allows_issues: bool = True


@dataclass(frozen=True)
class LotReceipt:
    code: str
    manufacture_date: date | None = None
    expiry_date: date | None = None
    supplier_lot_ref: str | None = None


@dataclass(frozen=True)
class ReceiptCommand:
    item_id: UUID
    warehouse_id: UUID
    quantity: Decimal
    unit_cost: Decimal
    currency_code: str
    source_ref: str
    actor_ref: str | None
    occurred_at: datetime
    lot: LotReceipt | None = None
    serial_numbers: tuple[str, ...] = ()


@dataclass(frozen=True)
class IssueCommand:
    item_id: UUID
    warehouse_id: UUID
    quantity: Decimal
    source_ref: str
    actor_ref: str | None
    occurred_at: datetime
    lot_id: UUID | None = None
    serial_numbers: tuple[str, ...] = ()
    reservation_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class StockIssueEvidence:
    movement_id: UUID
    tenant_id: UUID
    movement_group_id: UUID
    item_id: UUID
    warehouse_id: UUID
    lot_id: UUID | None
    quantity_issued: Decimal
    unit_cost: Decimal
    value_issued: Decimal
    currency_code: str
    source_ref: str
    actor_ref: str | None
    occurred_at: datetime
    serial_numbers: tuple[str, ...]


@dataclass(frozen=True)
class AdjustmentCommand:
    item_id: UUID
    warehouse_id: UUID
    quantity_delta: Decimal
    source_ref: str
    actor_ref: str | None
    occurred_at: datetime
    unit_cost: Decimal | None = None
    currency_code: str | None = None
    lot: LotReceipt | None = None
    lot_id: UUID | None = None
    serial_numbers: tuple[str, ...] = ()


@dataclass(frozen=True)
class TransferCommand:
    item_id: UUID
    source_warehouse_id: UUID
    destination_warehouse_id: UUID
    quantity: Decimal
    source_ref: str
    actor_ref: str | None
    occurred_at: datetime
    lot_id: UUID | None = None
    serial_numbers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReservationCreate:
    item_id: UUID
    warehouse_id: UUID
    quantity: Decimal
    reservation_ref: str
    actor_ref: str | None
    lot_id: UUID | None = None
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ValuationSnapshotCreate:
    item_id: UUID
    warehouse_id: UUID
    valuation_ref: str
    as_of_date: date
    currency_code: str
    estimated_selling_price: Decimal | None = None
    costs_to_complete: Decimal = Decimal("0")
    selling_costs: Decimal = Decimal("0")
    lot_id: UUID | None = None


@dataclass(frozen=True)
class TransferResult:
    outbound: StockMovement
    inbound: StockMovement


__all__ = [
    "AdjustmentCommand",
    "CostingMethod",
    "IssueCommand",
    "ItemCreate",
    "LotReceipt",
    "LotStatus",
    "MovementKind",
    "ReceiptCommand",
    "ReservationCreate",
    "ReservationStatus",
    "SerialStatus",
    "StockIssueEvidence",
    "TransferCommand",
    "TransferResult",
    "ValuationSnapshotCreate",
    "WarehouseCreate",
]
