"""Pure accounting canaries ported from ERP and tightened at the seams."""

from __future__ import annotations

from decimal import Decimal

import pytest
from dotmac_finance import (
    DepreciationMethod,
    FinanceRuleViolation,
    calculate_depreciation,
    calculate_disposal,
    calculate_impairment,
    calculate_revaluation,
)


def test_straight_line_allocates_the_remaining_amount_prospectively() -> None:
    result = calculate_depreciation(
        carrying_amount=Decimal("10000.00"),
        residual_value=Decimal("1000.00"),
        unimpaired_carrying_amount=Decimal("10000.00"),
        remaining_life_months=45,
        useful_life_months=60,
        periods=3,
        method=DepreciationMethod.STRAIGHT_LINE,
        minor_units=2,
    )

    assert result.charge == Decimal("600.00")
    assert result.closing_carrying_amount == Decimal("9400.00")
    assert result.closing_remaining_life_months == 42


def test_declining_balance_true_up_never_crosses_residual_value() -> None:
    result = calculate_depreciation(
        carrying_amount=Decimal("510.00"),
        residual_value=Decimal("500.00"),
        unimpaired_carrying_amount=Decimal("510.00"),
        remaining_life_months=1,
        useful_life_months=60,
        periods=1,
        method=DepreciationMethod.DOUBLE_DECLINING,
        minor_units=2,
    )

    assert result.charge == Decimal("10.00")
    assert result.closing_carrying_amount == Decimal("500.00")


def test_money_calculation_refuses_float_and_unknown_method() -> None:
    with pytest.raises(TypeError, match="float"):
        calculate_depreciation(
            carrying_amount=1000.0,  # type: ignore[arg-type]
            residual_value=Decimal("0"),
            unimpaired_carrying_amount=Decimal("1000"),
            remaining_life_months=12,
            useful_life_months=12,
            periods=1,
            method=DepreciationMethod.STRAIGHT_LINE,
            minor_units=2,
        )

    with pytest.raises(FinanceRuleViolation, match="unsupported depreciation"):
        calculate_depreciation(
            carrying_amount=Decimal("1000"),
            residual_value=Decimal("0"),
            unimpaired_carrying_amount=Decimal("1000"),
            remaining_life_months=12,
            useful_life_months=12,
            periods=1,
            method="units_of_production",  # type: ignore[arg-type]
            minor_units=2,
        )


def test_impairment_uses_the_higher_recoverable_amount_and_tracks_reversal_cap() -> (
    None
):
    loss = calculate_impairment(
        carrying_amount=Decimal("1000.00"),
        unimpaired_carrying_amount=Decimal("1000.00"),
        fair_value_less_costs_of_disposal=Decimal("650.00"),
        value_in_use=Decimal("700.00"),
        impairment_loss_balance=Decimal("0"),
        reserve_reduction_balance=Decimal("0"),
        revaluation_reserve_balance=Decimal("100.00"),
        minor_units=2,
    )
    assert loss.recoverable_amount == Decimal("700.00")
    assert loss.loss_to_reserve == Decimal("100.00")
    assert loss.loss_to_profit_or_loss == Decimal("200.00")
    assert loss.closing_carrying_amount == Decimal("700.00")

    reversal = calculate_impairment(
        carrying_amount=Decimal("700.00"),
        unimpaired_carrying_amount=Decimal("900.00"),
        fair_value_less_costs_of_disposal=Decimal("980.00"),
        value_in_use=Decimal("950.00"),
        impairment_loss_balance=Decimal("200.00"),
        reserve_reduction_balance=Decimal("100.00"),
        revaluation_reserve_balance=Decimal("0"),
        minor_units=2,
    )
    assert reversal.reversal_to_profit_or_loss == Decimal("200.00")
    assert reversal.closing_carrying_amount == Decimal("900.00")
    assert reversal.reversal_to_reserve == Decimal("0.00")


def test_revaluation_consumes_and_rebuilds_one_running_reserve() -> None:
    decrease = calculate_revaluation(
        carrying_amount=Decimal("1000.00"),
        fair_value=Decimal("850.00"),
        revaluation_reserve_balance=Decimal("100.00"),
        prior_revaluation_loss_balance=Decimal("0"),
        minor_units=2,
    )
    assert decrease.reserve_reversed == Decimal("100.00")
    assert decrease.loss_to_profit_or_loss == Decimal("50.00")
    assert decrease.closing_reserve_balance == Decimal("0.00")
    assert decrease.closing_prior_loss_balance == Decimal("50.00")

    increase = calculate_revaluation(
        carrying_amount=Decimal("850.00"),
        fair_value=Decimal("1000.00"),
        revaluation_reserve_balance=decrease.closing_reserve_balance,
        prior_revaluation_loss_balance=decrease.closing_prior_loss_balance,
        minor_units=2,
    )
    assert increase.loss_reversed_to_profit_or_loss == Decimal("50.00")
    assert increase.surplus_to_reserve == Decimal("100.00")


def test_disposal_gain_or_loss_is_net_proceeds_less_carrying_value() -> None:
    result = calculate_disposal(
        carrying_amount=Decimal("20000.00"),
        proceeds=Decimal("25000.00"),
        costs_of_disposal=Decimal("1000.00"),
        minor_units=2,
    )
    assert result.net_proceeds == Decimal("24000.00")
    assert result.gain_or_loss == Decimal("4000.00")
