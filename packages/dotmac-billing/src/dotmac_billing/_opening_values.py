"""Private exact storage/shape helpers; Kernel remains the money/wire owner."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import datetime
from decimal import (
    ROUND_HALF_UP,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from types import UnionType
from typing import Literal, Union, get_args, get_origin, get_type_hints

from dotmac_kernel.money import Currency, Money, MoneyError

from dotmac_billing.opening_contracts import OpeningEvidenceError


def minor_units_of(value: Money) -> int:
    if (
        type(value) is not Money
        or type(value.currency) is not Currency
        or type(value.amount) is not Decimal
        or type(value.currency.minor_units) is not int
    ):
        raise OpeningEvidenceError("opening cash requires exact Money")
    code = value.currency.code
    if (
        type(code) is not str
        or len(code) != 3
        or not code.isascii()
        or not code.isalpha()
        or not code.isupper()
    ):
        raise OpeningEvidenceError("opening cash requires an exact ISO currency")
    units = value.currency.minor_units
    amount = value.amount
    if not 0 <= units <= 6 or not amount.is_finite() or amount.adjusted() >= 14:
        raise OpeningEvidenceError("opening cash exceeds Numeric(20,6) storage")
    sign, digits, exponent = amount.as_tuple()
    if not isinstance(exponent, int):
        raise OpeningEvidenceError("opening cash must be finite")
    coefficient = int("".join(str(digit) for digit in digits))
    if coefficient == 0:
        return 0
    shift = exponent + units
    if shift < 0 and -shift >= len(digits):
        raise OpeningEvidenceError("opening cash must not require rounding")
    scale = 1
    for _ in range(abs(shift)):
        scale *= 10
    if shift < 0:
        if coefficient % scale:
            raise OpeningEvidenceError("opening cash must not require rounding")
        coefficient //= scale
    else:
        coefficient *= scale
    return -coefficient if sign else coefficient


def money_from_minor(value: int, currency: str, precision: int) -> Money:
    if type(value) is not int or type(precision) is not int or not 0 <= precision <= 6:
        raise OpeningEvidenceError("unsupported exact opening cash precision")
    if abs(value) >= 10 ** (14 + precision):
        raise OpeningEvidenceError("opening cash exceeds Numeric(20,6) storage")
    amount = Decimal(
        (int(value < 0), tuple(int(c) for c in str(abs(value))), -precision)
    )
    try:
        with localcontext(cash_decimal_context()):
            return Money(amount, Currency(currency, precision))
    except (DecimalException, MoneyError) as exc:
        raise OpeningEvidenceError("opening cash cannot be stored exactly") from exc


def cash_decimal_context() -> Context:
    """Fresh private context, including exponent bounds and traps, never ambient."""
    return Context(
        prec=40,
        rounding=ROUND_HALF_UP,
        Emin=-999999,
        Emax=999999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def _validate_closed(value: object, expected: object) -> None:
    origin = get_origin(expected)
    if origin is UnionType or origin is Union:
        for member in get_args(expected):
            try:
                _validate_closed(value, member)
                return
            except OpeningEvidenceError:
                continue
        raise OpeningEvidenceError("opening value does not match its closed union")
    if origin is Literal:
        if not any(
            type(value) is type(item) and value == item for item in get_args(expected)
        ):
            raise OpeningEvidenceError("unsupported opening discriminator")
        return
    if origin is tuple:
        if type(value) is not tuple:
            raise OpeningEvidenceError("opening sequences must be immutable tuples")
        member = get_args(expected)[0]
        for item in value:
            _validate_closed(item, member)
        return
    if type(value) is not expected:
        raise OpeningEvidenceError("invalid opening field type")
    if type(value) is Money:
        minor_units_of(value)
        return
    if is_dataclass(value) and not isinstance(value, type):
        hints = get_type_hints(type(value))
        for field in fields(value):
            _validate_closed(getattr(value, field.name), hints[field.name])
        return
    if isinstance(value, str) and not value.strip():
        raise OpeningEvidenceError("empty opening field")
    if isinstance(value, datetime) and (
        value.tzinfo is None or value.utcoffset() is None
    ):
        raise OpeningEvidenceError("naive opening instant")
    if type(value) is int and value.bit_length() > 4096:
        raise OpeningEvidenceError("oversized opening integer")


class OpeningCashValue:
    __slots__ = ()

    def __post_init__(self) -> None:
        # Reused historical DTOs are recursively qualified here, without changing
        # their published constructors or trusting their unchecked nested fields.
        _validate_closed(self, type(self))
