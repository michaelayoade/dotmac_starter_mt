"""Parity canaries for inventory valuation extracted from ERP."""

from decimal import Decimal

import pytest
from dotmac_inventory.valuation import (
    FIFO_LAYER,
    ValuationState,
    consume_fifo,
    issue_weighted_average,
    lower_of_cost_and_nrv,
    receive_weighted_average,
)


def test_weighted_average_receipt_and_issue_preserve_source_math() -> None:
    opening = ValuationState(
        quantity=Decimal("100"),
        total_value=Decimal("1000"),
        unit_cost=Decimal("10"),
    )

    received = receive_weighted_average(
        opening, quantity=Decimal("50"), unit_cost=Decimal("16")
    )
    issued = issue_weighted_average(received, quantity=Decimal("30"))

    assert received == ValuationState(
        quantity=Decimal("150.000000"),
        total_value=Decimal("1800.000000"),
        unit_cost=Decimal("12.000000"),
    )
    assert issued.quantity == Decimal("120.000000")
    assert issued.total_value == Decimal("1440.000000")
    assert issued.unit_cost == Decimal("12.000000")


def test_issue_refuses_non_positive_or_insufficient_quantity() -> None:
    state = ValuationState(Decimal("10"), Decimal("100"), Decimal("10"))
    with pytest.raises(ValueError, match="positive"):
        issue_weighted_average(state, quantity=Decimal("0"))
    with pytest.raises(ValueError, match="Insufficient stock"):
        issue_weighted_average(state, quantity=Decimal("11"))


def test_fifo_consumes_oldest_layers_and_reports_exact_value() -> None:
    layers = (
        FIFO_LAYER("lot-a", Decimal("3"), Decimal("10")),
        FIFO_LAYER("lot-b", Decimal("5"), Decimal("14")),
    )

    result = consume_fifo(layers, quantity=Decimal("6"))

    assert [(part.lot_ref, part.quantity) for part in result.consumed] == [
        ("lot-a", Decimal("3.000000")),
        ("lot-b", Decimal("3.000000")),
    ]
    assert result.total_cost == Decimal("72.000000")
    assert result.remaining == (
        FIFO_LAYER("lot-b", Decimal("2.000000"), Decimal("14.000000")),
    )


def test_lower_of_cost_and_nrv_records_write_down_without_finance_policy() -> None:
    result = lower_of_cost_and_nrv(
        cost=Decimal("1200"),
        estimated_selling_price=Decimal("1250"),
        costs_to_complete=Decimal("100"),
        selling_costs=Decimal("25"),
    )

    assert result.nrv == Decimal("1125.000000")
    assert result.carrying_amount == Decimal("1125.000000")
    assert result.write_down == Decimal("75.000000")
