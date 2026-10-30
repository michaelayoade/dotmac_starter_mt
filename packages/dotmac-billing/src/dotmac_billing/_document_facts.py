"""Strict decoding of the complete, persisted invoice fact V1 wire shape.

This is deliberately separate from draft snapshot readers: their defaults and
coercions would manufacture evidence that the issued fact never contained.
"""

from __future__ import annotations

import re
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, DecimalException, localcontext
from enum import Enum
from types import UnionType
from typing import Any, Literal, Union, cast, get_args, get_origin, get_type_hints
from uuid import UUID

from dotmac_kernel.money import Currency, Money, MoneyError

from dotmac_billing.contracts import InvoiceDocumentFactV1, InvoiceDocumentFactV2
from dotmac_billing.errors import BillingRuleViolation
from dotmac_billing.tax_evidence import validate_invoice_tax_evidence


def _object(value: object, names: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != names:
        raise ValueError("stored object must contain exactly the V1 fields")
    return cast(dict[str, object], value)


def _decimal(value: object) -> Decimal:
    if not isinstance(value, str):
        raise ValueError("stored decimal must be text")
    # Decimal accepts whitespace and underscores; neither is a wire decimal.
    if (
        re.fullmatch(
            r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", value
        )
        is None
    ):
        raise ValueError("stored decimal text is invalid")
    result = Decimal(value)
    if not result.is_finite():
        raise ValueError("stored decimal must be finite")
    return result


def _money(value: object) -> Money:
    raw = _object(value, {"amount", "currency", "minor_units"})
    code, units = raw["currency"], raw["minor_units"]
    if not isinstance(code, str) or re.fullmatch(r"[A-Z]{3}", code) is None:
        raise ValueError("stored currency must be an uppercase ISO code")
    if type(units) is not int or not 0 <= units <= 6:
        raise ValueError("stored minor units must be an integer from zero to six")
    amount = _decimal(raw["amount"])
    exponent = cast(int, amount.as_tuple().exponent)
    if exponent < -units:
        raise ValueError("stored money exceeds its minor-unit precision")
    # The persisted NUMERIC(20,6) boundary also bounds hostile exponents before
    # allocating a decimal context. No current currency lookup or rounding.
    if amount.adjusted() >= 14:
        raise ValueError("stored money exceeds Billing's numeric precision")
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits) + max(exponent, 0) + units)
        return Money(amount, Currency(code, units))


# The producer uses datetime.isoformat() at default precision. Z, a space
# separator and comma fractions are also accepted historical ISO spellings.
# A closed grammar plus explicit construction avoids both CPython's precision
# truncation and its loss of fractional offsets whose whole seconds are zero.
_INSTANT = re.compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})[T ]"
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2}):(?P<second>[0-9]{2})"
    r"(?:[.,](?P<fraction>[0-9]{1,6}))?"
    r"(?:Z|(?P<sign>[+-])(?P<offset_hour>[0-9]{2}):(?P<offset_minute>[0-9]{2})"
    r"(?::(?P<offset_second>[0-9]{2})(?:[.,](?P<offset_fraction>[0-9]{1,6}))?)?)"
)


def _microseconds(fraction: str | None) -> int:
    return int(fraction.ljust(6, "0")) if fraction else 0


def _instant(value: object) -> datetime:
    if not isinstance(value, str) or (match := _INSTANT.fullmatch(value)) is None:
        raise ValueError("stored instant must be losslessly representable ISO text")
    offset = timedelta(0)
    if match["sign"] is not None:
        hours = int(match["offset_hour"])
        minutes = int(match["offset_minute"])
        seconds = int(match["offset_second"] or "0")
        if hours > 23 or minutes > 59 or seconds > 59:
            raise ValueError("stored instant has an invalid timezone offset")
        offset = timedelta(
            hours=hours,
            minutes=minutes,
            seconds=seconds,
            microseconds=_microseconds(match["offset_fraction"]),
        )
        if match["sign"] == "-":
            offset = -offset
    return datetime(
        int(match["year"]),
        int(match["month"]),
        int(match["day"]),
        int(match["hour"]),
        int(match["minute"]),
        int(match["second"]),
        _microseconds(match["fraction"]),
        tzinfo=timezone(offset),
    )


def _decode(value: object, schema: Any) -> Any:
    """Private schema dispatch; every leaf is checked before construction."""
    origin, arguments = get_origin(schema), get_args(schema)
    if origin in (Union, UnionType):
        for member in arguments:
            try:
                return _decode(value, member)
            except (ValueError, TypeError, DecimalException, MoneyError):
                continue
        raise ValueError("stored value does not match its union")
    if origin is Literal:
        if not any(type(value) is type(item) and value == item for item in arguments):
            raise ValueError("stored discriminator is unsupported")
        return value
    if origin is tuple:
        if not isinstance(value, list):
            raise ValueError("stored tuple must be a JSON array")
        return tuple(_decode(item, arguments[0]) for item in value)
    if schema is type(None):
        if value is not None:
            raise ValueError("stored value must be null")
        return None
    if schema is Money:
        return _money(value)
    if schema is Decimal:
        return _decimal(value)
    if schema is date:
        if (
            not isinstance(value, str)
            or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None
        ):
            raise ValueError("stored date must be exact ISO date text")
        return date.fromisoformat(value)
    if schema is datetime:
        return _instant(value)
    if schema is UUID:
        if not isinstance(value, str):
            raise ValueError("stored UUID must be text")
        return UUID(value)
    if isinstance(schema, type) and issubclass(schema, Enum):
        if not isinstance(value, str):
            raise ValueError("stored enum must be text")
        return schema(value)
    if isinstance(schema, type) and is_dataclass(schema):
        raw = _object(value, {field.name for field in fields(schema)})
        hints = get_type_hints(schema)
        return schema(
            **{name: _decode(item, hints[name]) for name, item in raw.items()}
        )
    if schema in (str, int, bool):
        if type(value) is not schema:
            raise ValueError("stored scalar has the wrong type")
        return value
    raise TypeError("unsupported invoice fact schema")


def decode_invoice_document_fact(
    payload: object, *, v2: bool = False
) -> InvoiceDocumentFactV1 | InvoiceDocumentFactV2:
    """Decode only stored V1 evidence, with no defaults or lossy coercions."""
    try:
        fact = cast(
            InvoiceDocumentFactV1 | InvoiceDocumentFactV2,
            _decode(payload, InvoiceDocumentFactV2 if v2 else InvoiceDocumentFactV1),
        )
        if isinstance(fact, InvoiceDocumentFactV2):
            if fact.tax_snapshots:
                raise ValueError("V2 fact cannot mix detailed and V1 tax evidence")
            validate_invoice_tax_evidence(
                fact.tax_evidence, scope=fact.scope, tax_amount=fact.tax_total
            )
        if fact.fact_version < 1 or not 0 <= fact.minor_units <= 6:
            raise ValueError("invalid stored fact version or minor units")
        if re.fullmatch(r"[A-Z]{3}", fact.currency) is None:
            raise ValueError("invalid stored fact currency")
        if re.fullmatch(r"[0-9a-f]{64}", fact.presentation_model_digest) is None:
            raise ValueError("invalid stored presentation digest")
        amounts = [fact.subtotal, fact.tax_total, fact.grand_total]
        for line in fact.lines:
            amounts.extend(
                (
                    line.unit_amount,
                    line.pre_tax_amount,
                    line.discount_total,
                    line.tax_amount,
                    line.total_amount,
                )
            )
            amounts.extend(discount.amount for discount in line.discounts)
        for tax in fact.tax_snapshots:
            amounts.extend((tax.taxable_basis, tax.tax_amount))
        if any(
            money.currency != Currency(fact.currency, fact.minor_units)
            for money in amounts
        ):
            raise ValueError("stored monetary currency snapshots disagree")
        return fact
    except (ValueError, TypeError, DecimalException, MoneyError) as exc:
        raise BillingRuleViolation(
            "document_fact_corrupt",
            "stored invoice document fact is not valid V1 evidence",
        ) from exc
