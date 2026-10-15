"""Independent Decimal parity and the stock-ledger native-consumer canary."""

from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Clamped,
    Context,
    Decimal,
    DecimalException,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    localcontext,
)
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_inventory import _native, service, valuation

ROUNDINGS = (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
)


def _reference(columns):
    return tuple(
        sum(column, Decimal("0")).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        for column in columns
    )


def _outcome(operation, columns, context):
    with localcontext(context) as active:
        try:
            result = operation(columns)
        except DecimalException as error:
            outcome = (type(error), error.args)
        else:
            outcome = tuple(value.as_tuple() for value in result)
        return outcome, dict(active.flags)


@pytest.mark.parametrize("rounding", ROUNDINGS)
@pytest.mark.parametrize("precision", (3, 7, 28, 60))
@pytest.mark.parametrize("clamp", (0, 1))
@pytest.mark.parametrize(
    "columns",
    (
        ((), ("-0", "-0.0000000")),
        (("12345.67895", "0.00005", "-12345.67895"), ("1E-20",)),
        (("1E+30", "-1E+30", "0.1234565"), ("-0.0000005",)),
        (
            (
                "1234567890123456789012345678901234567890123456789012345678901234567890",
                "-1234567890123456789012345678901234567890123456789012345678901234567890",
                "0.000001",
            ),
            ("-0.00000000000000000000000000000000000000000000001",),
        ),
        (("0.000000000001", "0.000000000001"), ("9.999999999E+9",)),
        (("NaN123", "1"), ("-NaN45",)),
        (("sNaN123", "1"), ("1",)),
        (("Infinity", "-Infinity"), ("sNaN45",)),
        (("-Infinity",), ("Infinity",)),
    ),
)
def test_sequential_decimal_context_parity(rounding, precision, clamp, columns):
    context = Context(prec=precision, rounding=rounding, Emin=-9, Emax=9, clamp=clamp)
    context.clear_traps()
    values = tuple(tuple(Decimal(value) for value in column) for column in columns)
    assert _outcome(valuation._sum_quantized_columns, values, context) == _outcome(
        _reference, values, context
    )


@pytest.mark.parametrize(
    "traps",
    (
        (InvalidOperation,),
        (Overflow,),
        (Underflow, Subnormal),
        (Inexact, Rounded),
        (Clamped, Rounded, Inexact, Overflow, InvalidOperation),
    ),
)
@pytest.mark.parametrize(
    "columns",
    (
        (("1.23456789",), ("sNaN42",)),
        (("1E+20",), ("sNaN42",)),
        (("1E-20",), ("sNaN42",)),
        (("Infinity", "-Infinity"), ("1.23456789",)),
        (("-0E-30",), ("sNaN42",)),
    ),
)
def test_trapped_exception_priority_args_and_first_failure(traps, columns):
    context = Context(prec=7, Emin=-9, Emax=9, clamp=1)
    context.clear_traps()
    for signal in traps:
        context.traps[signal] = True
    values = tuple(tuple(Decimal(value) for value in column) for column in columns)
    assert _outcome(valuation._sum_quantized_columns, values, context) == _outcome(
        _reference, values, context
    )


def test_existing_flags_are_sticky_and_do_not_retrap():
    context = Context(prec=28)
    context.clear_traps()
    context.flags[Inexact] = True
    context.flags[InvalidOperation] = True
    context.traps[Inexact] = True
    context.traps[InvalidOperation] = True
    columns = ((Decimal("1"), Decimal("2")),)
    assert _outcome(valuation._sum_quantized_columns, columns, context) == _outcome(
        _reference, columns, context
    )


def test_extreme_python_context_exponents_preserve_underflow_and_signed_zero():
    context = Context(prec=28, Emin=-999999999999999999, Emax=999999999999999999)
    context.clear_traps()
    columns = (
        (Decimal("-1E-999999999999999999"),),
        (Decimal("-0E+999999999999999999"),),
    )
    assert _outcome(valuation._sum_quantized_columns, columns, context) == _outcome(
        _reference, columns, context
    )


def test_native_reports_first_failed_row_and_stops_before_later_column():
    # Inexact addition at row zero wins over the later sNaN's invalid operation.
    results, status, failure = _native.sum_quantized_columns(
        [["1.23456789", "sNaN42"], ["Infinity", "-Infinity"]],
        (7, 6, -9, 9, 0),
        (1 << 0) | (1 << 6),
    )
    assert results == []
    assert status == (1 << 6) | (1 << 7)
    assert failure == (status, 0, 0)


def test_rebuild_balance_calls_real_native_batch_before_python_unit_cost(monkeypatch):
    calls = []
    real_native = _native.sum_quantized_columns

    def recording_native(*args):
        calls.append(args[0])
        return real_native(*args)

    monkeypatch.setattr(_native, "sum_quantized_columns", recording_native)
    monkeypatch.setattr(service, "_item", lambda *args: None)
    monkeypatch.setattr(service, "_warehouse", lambda *args: None)
    balance = SimpleNamespace()
    monkeypatch.setattr(service, "_balance", lambda *args: balance)
    rows = iter(
        (
            [
                SimpleNamespace(quantity_delta=Decimal("2"), value_delta=Decimal("8")),
                SimpleNamespace(quantity_delta=Decimal("3"), value_delta=Decimal("12")),
            ],
            [SimpleNamespace(quantity_remaining=Decimal("1"))],
        )
    )
    flushes = []
    db = SimpleNamespace(
        scalars=lambda query: SimpleNamespace(all=lambda: next(rows)),
        flush=lambda: flushes.append(True),
    )
    result = service._rebuild_balance(
        db, tenant_id=uuid4(), item_id=uuid4(), warehouse_id=uuid4()
    )
    assert result is balance
    assert calls == [[["2", "3"], ["8", "12"]], [["1"]]]
    assert balance.quantity_on_hand.as_tuple() == Decimal("5.000000").as_tuple()
    assert balance.quantity_reserved.as_tuple() == Decimal("1.000000").as_tuple()
    assert balance.total_value.as_tuple() == Decimal("20.000000").as_tuple()
    assert balance.current_unit_cost.as_tuple() == Decimal("4.000000").as_tuple()
    assert flushes == [True]


def test_negative_rebuilt_stock_fails_before_loading_reservations(monkeypatch):
    calls = []
    real_native = _native.sum_quantized_columns

    def recording_native(*args):
        calls.append(args[0])
        return real_native(*args)

    monkeypatch.setattr(_native, "sum_quantized_columns", recording_native)
    monkeypatch.setattr(service, "_item", lambda *args: None)
    monkeypatch.setattr(service, "_warehouse", lambda *args: None)
    monkeypatch.setattr(service, "_balance", lambda *args: SimpleNamespace())
    queries = []

    def scalars(query):
        queries.append(query)
        assert len(queries) == 1, "reservation query must follow negative-stock check"
        return SimpleNamespace(
            all=lambda: [
                SimpleNamespace(quantity_delta=Decimal("-1"), value_delta=Decimal("2"))
            ]
        )

    db = SimpleNamespace(scalars=scalars)
    with pytest.raises(service.InventoryError, match="negative stock or value"):
        service._rebuild_balance(
            db, tenant_id=uuid4(), item_id=uuid4(), warehouse_id=uuid4()
        )
    assert calls == [[["-1"], ["2"]]]
