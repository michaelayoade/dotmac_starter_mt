"""Pure scalar aggregation; no persistence, payroll writer or payment execution.

Ported from ERP's payroll entry totals and payment-item parity check. Net is
summed independently: this seam does not impose gross minus deductions = net.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from dotmac_kernel.money import Currency


class PayrollTotalsViolation(ValueError):
    """Scalar totals evidence is invalid or payment parity failed."""


def _minor_units(amount: Decimal, currency: Currency) -> int:
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise PayrollTotalsViolation("payroll amounts must be finite decimals")
    sign, digits, exponent = amount.as_tuple()
    # Finite Decimal exponents are integers; do not use context-sensitive
    # quantize/scaleb/add here: aggregation must never silently round.
    assert isinstance(exponent, int)
    coefficient = 0
    for digit in digits:
        coefficient = coefficient * 10 + digit
    if coefficient == 0:
        return 0
    shift = exponent + currency.minor_units
    if shift < 0:
        divisor = 10 ** (-shift)
        coefficient, remainder = divmod(coefficient, divisor)
        if remainder:
            raise PayrollTotalsViolation("payroll amounts exceed currency precision")
    else:
        coefficient *= 10**shift
    return -coefficient if sign else coefficient


def _amount(units: int, currency: Currency) -> Decimal:
    return Decimal(
        (
            int(units < 0),
            Decimal(abs(units)).as_tuple().digits,
            -currency.minor_units,
        )
    )


@dataclass(frozen=True, slots=True, repr=False)
class PayrollAmountSnapshot:
    """One caller-read calculation; reference identifies a calculation, not a person."""

    calculation_ref: str
    currency: Currency
    gross_amount: Decimal
    deduction_amount: Decimal
    net_amount: Decimal

    def __post_init__(self) -> None:
        if (
            not isinstance(self.calculation_ref, str)
            or not self.calculation_ref.strip()
        ):
            raise PayrollTotalsViolation("calculation reference is required")
        for amount in (self.gross_amount, self.deduction_amount, self.net_amount):
            _minor_units(amount, self.currency)


@dataclass(frozen=True, slots=True, repr=False)
class PayrollRunTotals:
    """Derived totals only; never a second stored payroll run."""

    currency: Currency
    calculation_count: int
    gross_amount: Decimal
    deduction_amount: Decimal
    net_amount: Decimal

    def __post_init__(self) -> None:
        if type(self.calculation_count) is not int or self.calculation_count < 0:
            raise PayrollTotalsViolation("calculation count must be non-negative")
        for amount in (self.gross_amount, self.deduction_amount, self.net_amount):
            _minor_units(amount, self.currency)


def aggregate_payroll_run_totals(
    *, currency: Currency, snapshots: Iterable[PayrollAmountSnapshot]
) -> PayrollRunTotals:
    """Sum independent gross/deduction/net facts exactly in one currency.

    Callers read persisted facts after flushing their session. This function
    neither loads ORM instances nor recalculates any employee's payroll.
    """
    seen: set[str] = set()
    gross = deductions = net = 0
    for snapshot in snapshots:
        if snapshot.currency != currency:
            raise PayrollTotalsViolation(
                "calculation currency does not match payroll run"
            )
        if snapshot.calculation_ref in seen:
            raise PayrollTotalsViolation("calculation references must be unique")
        seen.add(snapshot.calculation_ref)
        gross += _minor_units(snapshot.gross_amount, currency)
        deductions += _minor_units(snapshot.deduction_amount, currency)
        net += _minor_units(snapshot.net_amount, currency)
    return PayrollRunTotals(
        currency,
        len(seen),
        _amount(gross, currency),
        _amount(deductions, currency),
        _amount(net, currency),
    )


def validate_payment_total(
    *,
    run_totals: PayrollRunTotals,
    currency: Currency,
    payment_amounts: Iterable[Decimal],
) -> None:
    """Require exact payment-item sum parity with run net; accept net-only data.

    The payment owner selects the items and retains authorization, bank details,
    format rules and execution. No employee or bank identities are accepted.
    """
    if currency != run_totals.currency:
        raise PayrollTotalsViolation("payment currency does not match payroll run")
    total = sum(_minor_units(amount, currency) for amount in payment_amounts)
    if total != _minor_units(run_totals.net_amount, currency):
        raise PayrollTotalsViolation(
            "payment total does not match payroll run net total"
        )


__all__ = [
    "PayrollAmountSnapshot",
    "PayrollRunTotals",
    "PayrollTotalsViolation",
    "aggregate_payroll_run_totals",
    "validate_payment_total",
]
