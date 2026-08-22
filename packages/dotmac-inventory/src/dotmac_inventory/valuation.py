"""Deterministic inventory valuation calculations with six-decimal precision."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

_SCALE = Decimal("0.000001")


def quantize(value: Decimal) -> Decimal:
    return value.quantize(_SCALE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ValuationState:
    quantity: Decimal
    total_value: Decimal
    unit_cost: Decimal


@dataclass(frozen=True)
class FIFO_LAYER:
    lot_ref: str
    quantity: Decimal
    unit_cost: Decimal


@dataclass(frozen=True)
class FIFOConsumption:
    consumed: tuple[FIFO_LAYER, ...]
    remaining: tuple[FIFO_LAYER, ...]
    total_cost: Decimal


@dataclass(frozen=True)
class LowerOfCostAndNRV:
    cost: Decimal
    nrv: Decimal
    carrying_amount: Decimal
    write_down: Decimal


def receive_weighted_average(
    current: ValuationState, *, quantity: Decimal, unit_cost: Decimal
) -> ValuationState:
    if quantity <= 0:
        raise ValueError("Receipt quantity must be positive")
    if unit_cost < 0:
        raise ValueError("Receipt unit cost cannot be negative")
    new_quantity = quantize(current.quantity + quantity)
    new_value = quantize(current.total_value + quantity * unit_cost)
    return ValuationState(
        quantity=new_quantity,
        total_value=new_value,
        unit_cost=quantize(new_value / new_quantity),
    )


def issue_weighted_average(
    current: ValuationState, *, quantity: Decimal
) -> ValuationState:
    if quantity <= 0:
        raise ValueError("Issue quantity must be positive")
    if current.quantity < quantity:
        raise ValueError(
            f"Insufficient stock: {quantize(current.quantity)} available, "
            f"{quantize(quantity)} requested"
        )
    new_quantity = quantize(current.quantity - quantity)
    if new_quantity == 0:
        return ValuationState(
            Decimal("0.000000"), Decimal("0.000000"), Decimal("0.000000")
        )
    value_out = quantize(quantity * current.unit_cost)
    new_value = quantize(current.total_value - value_out)
    return ValuationState(new_quantity, new_value, quantize(new_value / new_quantity))


def consume_fifo(
    layers: tuple[FIFO_LAYER, ...], *, quantity: Decimal
) -> FIFOConsumption:
    if quantity <= 0:
        raise ValueError("FIFO issue quantity must be positive")
    available = sum((layer.quantity for layer in layers), Decimal("0"))
    if available < quantity:
        raise ValueError(
            f"Insufficient stock: {quantize(available)} available, "
            f"{quantize(quantity)} requested"
        )
    needed = quantity
    consumed: list[FIFO_LAYER] = []
    remaining: list[FIFO_LAYER] = []
    total_cost = Decimal("0")
    for layer in layers:
        layer_quantity = quantize(layer.quantity)
        layer_cost = quantize(layer.unit_cost)
        if needed <= 0:
            remaining.append(FIFO_LAYER(layer.lot_ref, layer_quantity, layer_cost))
            continue
        used = min(layer_quantity, needed)
        consumed.append(FIFO_LAYER(layer.lot_ref, quantize(used), layer_cost))
        total_cost += used * layer_cost
        left = quantize(layer_quantity - used)
        if left > 0:
            remaining.append(FIFO_LAYER(layer.lot_ref, left, layer_cost))
        needed -= used
    return FIFOConsumption(
        consumed=tuple(consumed),
        remaining=tuple(remaining),
        total_cost=quantize(total_cost),
    )


def lower_of_cost_and_nrv(
    *,
    cost: Decimal,
    estimated_selling_price: Decimal,
    costs_to_complete: Decimal = Decimal("0"),
    selling_costs: Decimal = Decimal("0"),
) -> LowerOfCostAndNRV:
    for name, value in (
        ("cost", cost),
        ("estimated selling price", estimated_selling_price),
        ("costs to complete", costs_to_complete),
        ("selling costs", selling_costs),
    ):
        if value < 0:
            raise ValueError(f"{name} cannot be negative")
    nrv = quantize(
        max(Decimal("0"), estimated_selling_price - costs_to_complete - selling_costs)
    )
    normalized_cost = quantize(cost)
    carrying = min(normalized_cost, nrv)
    return LowerOfCostAndNRV(
        cost=normalized_cost,
        nrv=nrv,
        carrying_amount=carrying,
        write_down=quantize(normalized_cost - carrying),
    )


__all__ = [
    "FIFOConsumption",
    "FIFO_LAYER",
    "LowerOfCostAndNRV",
    "ValuationState",
    "consume_fifo",
    "issue_weighted_average",
    "lower_of_cost_and_nrv",
    "quantize",
    "receive_weighted_average",
]
