"""Pure fixed-asset accounting calculations with explicit rounding."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from dotmac_finance.contracts import DepreciationMethod


class FinanceRuleViolation(ValueError):
    """The requested accounting operation violates the declared policy."""


def _amount(value: Decimal, label: str) -> Decimal:
    if isinstance(value, float):
        raise TypeError(f"{label} refuses float; pass Decimal")
    if not isinstance(value, Decimal):
        raise TypeError(f"{label} must be Decimal")
    if not value.is_finite():
        raise FinanceRuleViolation(f"{label} must be finite")
    return value


def _quantum(minor_units: int) -> Decimal:
    if not 0 <= minor_units <= 6:
        raise FinanceRuleViolation("minor units must be between zero and six")
    return Decimal(1).scaleb(-minor_units)


def _q(value: Decimal, minor_units: int) -> Decimal:
    return value.quantize(_quantum(minor_units), rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class DepreciationResult:
    charge: Decimal
    unimpaired_charge: Decimal
    closing_carrying_amount: Decimal
    closing_unimpaired_carrying_amount: Decimal
    closing_remaining_life_months: int


def _declining_charge(
    *,
    carrying: Decimal,
    residual: Decimal,
    useful_life_months: int,
    periods: int,
    remaining_life_months: int,
    multiplier: Decimal,
    minor_units: int,
) -> Decimal:
    current = carrying
    for period in range(periods):
        maximum = max(Decimal(0), current - residual)
        if maximum == 0:
            break
        if period + 1 == remaining_life_months:
            charge = maximum
        else:
            charge = min(
                maximum,
                _q(current * multiplier / Decimal(useful_life_months), minor_units),
            )
        current -= charge
    return _q(carrying - current, minor_units)


def calculate_depreciation(
    *,
    carrying_amount: Decimal,
    residual_value: Decimal,
    unimpaired_carrying_amount: Decimal,
    remaining_life_months: int,
    useful_life_months: int,
    periods: int,
    method: DepreciationMethod,
    minor_units: int,
) -> DepreciationResult:
    carrying = _amount(carrying_amount, "carrying amount")
    residual = _amount(residual_value, "residual value")
    unimpaired = _amount(unimpaired_carrying_amount, "unimpaired carrying amount")
    if not isinstance(method, DepreciationMethod):
        raise FinanceRuleViolation(f"unsupported depreciation method: {method}")
    if useful_life_months <= 0 or remaining_life_months < 0:
        raise FinanceRuleViolation("useful and remaining life must be valid months")
    if periods <= 0:
        raise FinanceRuleViolation("depreciation needs at least one period")
    effective_periods = min(periods, remaining_life_months)
    if effective_periods == 0:
        zero = _q(Decimal(0), minor_units)
        return DepreciationResult(zero, zero, carrying, unimpaired, 0)
    if min(carrying, residual, unimpaired) < 0:
        raise FinanceRuleViolation("carrying and residual values cannot be negative")

    if method is DepreciationMethod.STRAIGHT_LINE:
        charge = _q(
            max(Decimal(0), carrying - residual)
            * Decimal(effective_periods)
            / Decimal(remaining_life_months),
            minor_units,
        )
        unimpaired_charge = _q(
            max(Decimal(0), unimpaired - residual)
            * Decimal(effective_periods)
            / Decimal(remaining_life_months),
            minor_units,
        )
    else:
        multiplier = (
            Decimal(2) if method is DepreciationMethod.DOUBLE_DECLINING else Decimal(1)
        )
        charge = _declining_charge(
            carrying=carrying,
            residual=residual,
            useful_life_months=useful_life_months,
            periods=effective_periods,
            remaining_life_months=remaining_life_months,
            multiplier=multiplier,
            minor_units=minor_units,
        )
        unimpaired_charge = _declining_charge(
            carrying=unimpaired,
            residual=residual,
            useful_life_months=useful_life_months,
            periods=effective_periods,
            remaining_life_months=remaining_life_months,
            multiplier=multiplier,
            minor_units=minor_units,
        )

    charge = min(charge, max(Decimal(0), carrying - residual))
    unimpaired_charge = min(unimpaired_charge, max(Decimal(0), unimpaired - residual))
    return DepreciationResult(
        charge=_q(charge, minor_units),
        unimpaired_charge=_q(unimpaired_charge, minor_units),
        closing_carrying_amount=_q(carrying - charge, minor_units),
        closing_unimpaired_carrying_amount=_q(
            unimpaired - unimpaired_charge, minor_units
        ),
        closing_remaining_life_months=remaining_life_months - effective_periods,
    )


@dataclass(frozen=True, slots=True)
class ImpairmentResult:
    recoverable_amount: Decimal
    loss_to_reserve: Decimal
    loss_to_profit_or_loss: Decimal
    reversal_to_profit_or_loss: Decimal
    reversal_to_reserve: Decimal
    closing_carrying_amount: Decimal
    closing_revaluation_reserve_balance: Decimal
    closing_impairment_loss_balance: Decimal
    closing_reserve_reduction_balance: Decimal


def calculate_impairment(
    *,
    carrying_amount: Decimal,
    unimpaired_carrying_amount: Decimal,
    fair_value_less_costs_of_disposal: Decimal | None,
    value_in_use: Decimal | None,
    impairment_loss_balance: Decimal,
    reserve_reduction_balance: Decimal,
    revaluation_reserve_balance: Decimal,
    minor_units: int,
) -> ImpairmentResult:
    carrying = _amount(carrying_amount, "carrying amount")
    ceiling = _amount(unimpaired_carrying_amount, "unimpaired carrying amount")
    loss_balance = _amount(impairment_loss_balance, "impairment loss balance")
    reserve_reduction = _amount(reserve_reduction_balance, "reserve reduction balance")
    reserve = _amount(revaluation_reserve_balance, "revaluation reserve")
    candidates = [
        _amount(value, "recoverable amount input")
        for value in (fair_value_less_costs_of_disposal, value_in_use)
        if value is not None
    ]
    if not candidates:
        raise FinanceRuleViolation("impairment requires a recoverable amount input")
    if (
        min(
            [
                *candidates,
                carrying,
                ceiling,
                loss_balance,
                reserve_reduction,
                reserve,
            ]
        )
        < 0
    ):
        raise FinanceRuleViolation("impairment values cannot be negative")
    recoverable = _q(max(candidates), minor_units)
    zero = _q(Decimal(0), minor_units)
    loss_to_reserve = loss_to_pl = reversal_to_pl = reversal_to_reserve = zero

    closing = carrying
    if recoverable < carrying:
        loss = _q(carrying - recoverable, minor_units)
        loss_to_reserve = min(loss, reserve)
        loss_to_pl = loss - loss_to_reserve
        closing = recoverable
        reserve -= loss_to_reserve
        reserve_reduction += loss_to_reserve
        loss_balance += loss_to_pl
    elif recoverable > carrying:
        permitted = min(
            recoverable - carrying,
            max(Decimal(0), ceiling - carrying),
            loss_balance + reserve_reduction,
        )
        permitted = _q(permitted, minor_units)
        reversal_to_pl = min(permitted, loss_balance)
        reversal_to_reserve = min(permitted - reversal_to_pl, reserve_reduction)
        closing += reversal_to_pl + reversal_to_reserve
        loss_balance -= reversal_to_pl
        reserve_reduction -= reversal_to_reserve
        reserve += reversal_to_reserve

    return ImpairmentResult(
        recoverable_amount=recoverable,
        loss_to_reserve=_q(loss_to_reserve, minor_units),
        loss_to_profit_or_loss=_q(loss_to_pl, minor_units),
        reversal_to_profit_or_loss=_q(reversal_to_pl, minor_units),
        reversal_to_reserve=_q(reversal_to_reserve, minor_units),
        closing_carrying_amount=_q(closing, minor_units),
        closing_revaluation_reserve_balance=_q(reserve, minor_units),
        closing_impairment_loss_balance=_q(loss_balance, minor_units),
        closing_reserve_reduction_balance=_q(reserve_reduction, minor_units),
    )


@dataclass(frozen=True, slots=True)
class RevaluationResult:
    surplus_to_reserve: Decimal
    loss_reversed_to_profit_or_loss: Decimal
    reserve_reversed: Decimal
    loss_to_profit_or_loss: Decimal
    closing_carrying_amount: Decimal
    closing_reserve_balance: Decimal
    closing_prior_loss_balance: Decimal


def calculate_revaluation(
    *,
    carrying_amount: Decimal,
    fair_value: Decimal,
    revaluation_reserve_balance: Decimal,
    prior_revaluation_loss_balance: Decimal,
    minor_units: int,
) -> RevaluationResult:
    carrying = _amount(carrying_amount, "carrying amount")
    fair = _amount(fair_value, "fair value")
    reserve = _amount(revaluation_reserve_balance, "revaluation reserve")
    prior_loss = _amount(prior_revaluation_loss_balance, "prior revaluation loss")
    if min(carrying, fair, reserve, prior_loss) < 0:
        raise FinanceRuleViolation("revaluation values cannot be negative")
    zero = _q(Decimal(0), minor_units)
    surplus = loss_reversed = reserve_reversed = loss = zero
    difference = _q(fair - carrying, minor_units)
    if difference > 0:
        loss_reversed = min(difference, prior_loss)
        surplus = difference - loss_reversed
        prior_loss -= loss_reversed
        reserve += surplus
    elif difference < 0:
        decrease = -difference
        reserve_reversed = min(decrease, reserve)
        loss = decrease - reserve_reversed
        reserve -= reserve_reversed
        prior_loss += loss
    return RevaluationResult(
        surplus_to_reserve=_q(surplus, minor_units),
        loss_reversed_to_profit_or_loss=_q(loss_reversed, minor_units),
        reserve_reversed=_q(reserve_reversed, minor_units),
        loss_to_profit_or_loss=_q(loss, minor_units),
        closing_carrying_amount=_q(fair, minor_units),
        closing_reserve_balance=_q(reserve, minor_units),
        closing_prior_loss_balance=_q(prior_loss, minor_units),
    )


@dataclass(frozen=True, slots=True)
class DisposalResult:
    net_proceeds: Decimal
    gain_or_loss: Decimal


def calculate_disposal(
    *,
    carrying_amount: Decimal,
    proceeds: Decimal,
    costs_of_disposal: Decimal,
    minor_units: int,
) -> DisposalResult:
    carrying = _amount(carrying_amount, "carrying amount")
    gross = _amount(proceeds, "disposal proceeds")
    costs = _amount(costs_of_disposal, "costs of disposal")
    if min(carrying, gross, costs) < 0:
        raise FinanceRuleViolation("disposal values cannot be negative")
    net = _q(gross - costs, minor_units)
    return DisposalResult(
        net_proceeds=net,
        gain_or_loss=_q(net - carrying, minor_units),
    )


__all__ = [
    "DepreciationResult",
    "DisposalResult",
    "FinanceRuleViolation",
    "ImpairmentResult",
    "RevaluationResult",
    "calculate_depreciation",
    "calculate_disposal",
    "calculate_impairment",
    "calculate_revaluation",
]
