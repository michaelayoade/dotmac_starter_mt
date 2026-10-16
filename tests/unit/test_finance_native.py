"""Native finance parity against the displaced Python operation sequence."""

from dataclasses import astuple
from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Rounded,
    localcontext,
)

import pytest
from dotmac_finance import _native
from dotmac_finance.calculation import (
    DepreciationResult,
    FinanceRuleViolation,
    _amount,
    _q,
    calculate_depreciation,
)
from dotmac_finance.contracts import DepreciationMethod


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


def reference_depreciation(
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


ROUNDINGS = [
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_UP,
    ROUND_DOWN,
    ROUND_HALF_UP,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_05UP,
]


def arguments(method, **changes):
    values = {
        "carrying_amount": Decimal("100.015"),
        "residual_value": Decimal("2.001"),
        "unimpaired_carrying_amount": Decimal("130.005"),
        "remaining_life_months": 7,
        "useful_life_months": 13,
        "periods": 4,
        "method": method,
        "minor_units": 2,
    }
    values.update(changes)
    return values


def observed(
    function,
    values,
    *,
    precision=28,
    rounding=ROUND_HALF_EVEN,
    traps=(),
    emin=-999999,
    emax=999999,
    clamp=0,
):
    with localcontext() as context:
        context.prec = precision
        context.rounding = rounding
        context.Emin, context.Emax, context.clamp = emin, emax, clamp
        for signal in context.traps:
            context.traps[signal] = signal in traps
        context.clear_flags()
        context.flags[Rounded] = True
        try:
            result = function(**values)
            outcome = tuple(
                value.as_tuple() if isinstance(value, Decimal) else value
                for value in astuple(result)
            )
        except (ArithmeticError, FinanceRuleViolation) as error:
            outcome = (type(error), error.args)
        return outcome, dict(context.flags)


@pytest.mark.parametrize("method", list(DepreciationMethod))
@pytest.mark.parametrize("rounding", ROUNDINGS)
@pytest.mark.parametrize("precision", [3, 7, 28])
def test_all_methods_context_rounding_and_existing_flags(method, rounding, precision):
    values = arguments(method)
    assert observed(
        calculate_depreciation, values, precision=precision, rounding=rounding
    ) == observed(
        reference_depreciation, values, precision=precision, rounding=rounding
    )


@pytest.mark.parametrize("method", list(DepreciationMethod))
@pytest.mark.parametrize(
    "changes",
    [
        {"remaining_life_months": 0},
        {"periods": 20},
        {"remaining_life_months": 1},
        {"residual_value": Decimal("101")},
        {"carrying_amount": Decimal("-0"), "residual_value": Decimal("0")},
        {"minor_units": 7},
        {"minor_units": -1},
        {
            "carrying_amount": Decimal("9E+4"),
            "unimpaired_carrying_amount": Decimal("9E+4"),
        },
        {"carrying_amount": Decimal("1E-9"), "residual_value": Decimal("0")},
    ],
)
def test_life_residual_signed_zero_late_scale_and_exponent_boundaries(method, changes):
    values = arguments(method, **changes)
    for traps in [(), (InvalidOperation,), (Inexact,), (Rounded,), (DivisionByZero,)]:
        assert observed(
            calculate_depreciation,
            values,
            precision=3,
            traps=traps,
            emin=-3,
            emax=3,
            clamp=1,
        ) == observed(
            reference_depreciation,
            values,
            precision=3,
            traps=traps,
            emin=-3,
            emax=3,
            clamp=1,
        )


def test_real_calculation_calls_native_once_for_both_trajectories(monkeypatch):
    calls = []
    native = _native.depreciation_charges

    def record(*args):
        calls.append(args)
        return native(*args)

    monkeypatch.setattr(_native, "depreciation_charges", record)
    result = calculate_depreciation(
        **arguments(
            DepreciationMethod.STRAIGHT_LINE,
            carrying_amount=Decimal("100"),
            residual_value=Decimal("0"),
            unimpaired_carrying_amount=Decimal("120"),
            remaining_life_months=10,
            useful_life_months=10,
            periods=2,
        )
    )
    assert result.charge == Decimal("20.00")
    assert result.unimpaired_charge == Decimal("24.00")
    assert len(calls) == 1


@pytest.mark.parametrize("method", list(DepreciationMethod))
def test_last_month_charges_full_depreciable_balance(method):
    result = calculate_depreciation(
        **arguments(
            method,
            carrying_amount=Decimal("123.45"),
            residual_value=Decimal("23.45"),
            unimpaired_carrying_amount=Decimal("223.45"),
            remaining_life_months=1,
            useful_life_months=100,
            periods=1,
        )
    )
    assert result.charge == Decimal("100.00")
    assert result.unimpaired_charge == Decimal("200.00")
    assert result.closing_carrying_amount == Decimal("23.45")
    assert result.closing_unimpaired_carrying_amount == Decimal("23.45")


@pytest.mark.parametrize(
    "huge", [2**80, 10**5000], ids=["above-u64", "above-int-str-limit"]
)
def test_straight_line_periods_preserve_unbounded_python_integers(huge):
    values = arguments(
        DepreciationMethod.STRAIGHT_LINE,
        periods=huge,
        remaining_life_months=huge,
        useful_life_months=huge,
    )
    assert observed(calculate_depreciation, values) == observed(
        reference_depreciation, values
    )


@pytest.mark.parametrize(
    "method",
    [DepreciationMethod.DECLINING_BALANCE, DepreciationMethod.DOUBLE_DECLINING],
)
def test_huge_declining_periods_can_break_at_residual_without_narrowing(method):
    huge = 2**80
    values = arguments(
        method,
        periods=huge,
        remaining_life_months=huge,
        useful_life_months=huge,
        carrying_amount=Decimal("12.34"),
        unimpaired_carrying_amount=Decimal("12.34"),
        residual_value=Decimal("12.34"),
    )
    assert observed(calculate_depreciation, values) == observed(
        reference_depreciation, values
    )
