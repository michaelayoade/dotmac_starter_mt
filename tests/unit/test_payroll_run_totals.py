"""Canaries for the scalar Payroll totals and payment parity contract."""

import subprocess
import sys
from dataclasses import FrozenInstanceError
from decimal import Decimal, localcontext

import pytest
from dotmac_kernel.money import Currency
from dotmac_payroll import (
    PayrollAmountSnapshot,
    PayrollTotalsViolation,
    aggregate_payroll_run_totals,
    validate_payment_total,
)

NGN = Currency("NGN", 2)


def snapshot(
    ref: str = "calculation:1",
    gross: str = "10.00",
    deduction: str = "2.00",
    net: str = "8.00",
    currency: Currency = NGN,
) -> PayrollAmountSnapshot:
    return PayrollAmountSnapshot(
        ref, currency, Decimal(gross), Decimal(deduction), Decimal(net)
    )


def test_payment_mismatch_is_refused_without_sensitive_values() -> None:
    totals = aggregate_payroll_run_totals(currency=NGN, snapshots=(snapshot(),))
    with pytest.raises(PayrollTotalsViolation) as error:
        validate_payment_total(
            run_totals=totals, currency=NGN, payment_amounts=(Decimal("7.99"),)
        )
    assert str(error.value) == "payment total does not match payroll run net total"


def test_empty_zero_and_multiple_snapshots_preserve_independent_sums() -> None:
    empty = aggregate_payroll_run_totals(currency=NGN, snapshots=())
    assert (
        empty.calculation_count,
        empty.gross_amount,
        empty.deduction_amount,
        empty.net_amount,
    ) == (0, Decimal("0.00"), Decimal("0.00"), Decimal("0.00"))
    validate_payment_total(run_totals=empty, currency=NGN, payment_amounts=())
    totals = aggregate_payroll_run_totals(
        currency=NGN,
        snapshots=(
            snapshot(),
            snapshot("calculation:2", "0", "0", "0"),
            snapshot("calculation:3", "4.10", "1.01", "9.99"),
        ),
    )
    assert (
        totals.calculation_count,
        totals.gross_amount,
        totals.deduction_amount,
        totals.net_amount,
    ) == (3, Decimal("14.10"), Decimal("3.01"), Decimal("17.99"))
    validate_payment_total(
        run_totals=totals,
        currency=NGN,
        payment_amounts=(Decimal("8.00"), Decimal("9.99")),
    )
    with pytest.raises(FrozenInstanceError):
        totals.net_amount = Decimal("0")
    with pytest.raises(FrozenInstanceError):
        snapshot().net_amount = Decimal("0")


def test_totals_remain_exact_under_a_small_decimal_context() -> None:
    with localcontext() as context:
        context.prec = 3
        totals = aggregate_payroll_run_totals(
            currency=NGN,
            snapshots=(
                snapshot(gross="999999999999999999999999.99"),
                snapshot("second", gross="0.01"),
            ),
        )
    assert totals.gross_amount == Decimal("1000000000000000000000000.00")


@pytest.mark.parametrize("amount", ["NaN", "sNaN", "Infinity", "-Infinity", "1.001"])
def test_invalid_snapshot_and_payment_amounts_are_refused(amount: str) -> None:
    with pytest.raises(PayrollTotalsViolation):
        snapshot(net=amount)
    totals = aggregate_payroll_run_totals(currency=NGN, snapshots=())
    with pytest.raises(PayrollTotalsViolation):
        validate_payment_total(
            run_totals=totals, currency=NGN, payment_amounts=(Decimal(amount),)
        )


@pytest.mark.parametrize("currency", [Currency("USD", 2), Currency("NGN", 3)])
def test_mixed_currency_or_scale_is_refused(currency: Currency) -> None:
    with pytest.raises(PayrollTotalsViolation):
        aggregate_payroll_run_totals(
            currency=NGN, snapshots=(snapshot(currency=currency),)
        )
    totals = aggregate_payroll_run_totals(currency=NGN, snapshots=())
    with pytest.raises(PayrollTotalsViolation):
        validate_payment_total(run_totals=totals, currency=currency, payment_amounts=())


def test_duplicate_snapshot_identity_is_refused_without_identity() -> None:
    with pytest.raises(
        PayrollTotalsViolation, match="^calculation references must be unique$"
    ):
        aggregate_payroll_run_totals(currency=NGN, snapshots=(snapshot(), snapshot()))


def test_exact_trailing_zero_scale_and_signed_values_are_preserved() -> None:
    totals = aggregate_payroll_run_totals(
        currency=NGN,
        snapshots=(snapshot(gross="-1.000", deduction="0.000", net="-2.000"),),
    )
    assert totals.gross_amount == Decimal("-1.00")
    validate_payment_total(
        run_totals=totals, currency=NGN, payment_amounts=(Decimal("-2.000"),)
    )


@pytest.mark.parametrize(
    "currency,amount", [(Currency("JPY", 0), "2"), (Currency("KWD", 3), "2.123")]
)
def test_currency_scale_is_supplied_by_the_caller(
    currency: Currency, amount: str
) -> None:
    totals = aggregate_payroll_run_totals(
        currency=currency,
        snapshots=(
            snapshot(currency=currency, gross=amount, deduction="0", net=amount),
        ),
    )
    validate_payment_total(
        run_totals=totals, currency=currency, payment_amounts=(Decimal(amount),)
    )


def test_public_import_does_not_construct_a_database_engine_or_session() -> None:
    script = """
import sqlalchemy
import sqlalchemy.orm

def forbidden(*args, **kwargs):
    raise AssertionError("database construction during public import")

sqlalchemy.create_engine = forbidden
sqlalchemy.engine.create_engine = forbidden
sqlalchemy.orm.Session.__init__ = forbidden
sqlalchemy.orm.sessionmaker = forbidden
from dotmac_payroll import (
    PayrollAmountSnapshot, aggregate_payroll_run_totals, validate_payment_total,
)
from dotmac_payroll.totals import PayrollRunTotals
"""
    # The executable and script are fixed by this test, with no caller input.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    # Sensitivity: an import-time engine call must trip the same guard.
    planted = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script + '\nsqlalchemy.create_engine("sqlite://")'],
        capture_output=True,
        text=True,
        check=False,
    )
    assert planted.returncode != 0
    assert "database construction during public import" in planted.stderr
