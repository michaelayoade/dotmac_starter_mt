"""Exact commercial values and stable cross-process canonicalization."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from dotmac_kernel.cache import Scope, scope_segment

from dotmac_subscriptions.cadence import (
    IntervalUnit,
    ProrationPolicy,
    RateBasis,
)
from dotmac_subscriptions.errors import SubscriptionDataError


def canonical_decimal(value: Decimal) -> str:
    """Language-neutral, non-exponent decimal text with no insignificant zeroes."""
    if (
        isinstance(value, float)
        or not isinstance(value, Decimal)
        or not value.is_finite()
    ):
        raise SubscriptionDataError(
            "values.invalid_decimal",
            "Commercial decimal values must be finite Decimal instances, never float.",
        )
    normalized = value.normalize()
    rendered = format(normalized, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"-0", ""} else rendered


def _currency(value: str) -> str:
    if len(value) != 3 or not value.isalpha() or value != value.upper():
        raise SubscriptionDataError(
            "values.invalid_currency",
            "Currency is required as an uppercase alphabetic three-letter code.",
        )
    return value


def _aware(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SubscriptionDataError(
            "values.naive_datetime",
            f"{field} must be timezone-aware.",
            {"field": field},
        )
    return value


def canonical_instant(value: datetime) -> str:
    return (
        _aware(value, field="instant")
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
    )


@dataclass(frozen=True, slots=True)
class ExactAmount:
    """Exact decimal amount with required currency and explicit scale."""

    amount: Decimal
    currency: str
    scale: int

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):
            raise SubscriptionDataError(
                "values.float_money",
                "ExactAmount refuses float input.",
            )
        if not isinstance(self.amount, Decimal) or not self.amount.is_finite():
            raise SubscriptionDataError(
                "values.invalid_money",
                "ExactAmount requires a finite Decimal.",
            )
        _currency(self.currency)
        if not 0 <= self.scale <= 6:
            raise SubscriptionDataError(
                "values.invalid_scale",
                "ExactAmount scale must be between zero and six.",
            )
        quantum = Decimal(1).scaleb(-self.scale)
        try:
            quantized = self.amount.quantize(quantum)
        except InvalidOperation as exc:
            raise SubscriptionDataError(
                "values.invalid_money",
                "ExactAmount cannot be represented at its declared scale.",
            ) from exc
        if quantized != self.amount:
            raise SubscriptionDataError(
                "values.scale_mismatch",
                "ExactAmount has more fractional digits than its declared scale.",
            )
        if len(quantized.as_tuple().digits) > 20:
            raise SubscriptionDataError(
                "values.precision_exceeded",
                "ExactAmount exceeds NUMERIC(20,6) precision.",
            )
        object.__setattr__(self, "amount", quantized)

    def as_wire(self) -> dict[str, str | int]:
        return {
            "amount": format(self.amount, f".{self.scale}f"),
            "currency": self.currency,
            "scale": self.scale,
        }


def _digest(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def rating_input_fingerprint(
    *,
    unit_price: ExactAmount,
    quantity: Decimal,
    rate_basis: RateBasis,
    rate_unit: IntervalUnit,
    rate_quantity: Decimal,
    rate_units: Decimal,
    proration_policy: ProrationPolicy,
    proration_factor: Decimal,
    coverage_start: datetime,
    coverage_end: datetime,
    currency: str,
    timezone_name: str,
    rating_policy_version: str,
    offer_version_ref: str,
) -> str:
    _currency(currency)
    return _digest(
        {
            "unit_price": unit_price.as_wire(),
            "quantity": canonical_decimal(quantity),
            "rate_basis": rate_basis.value,
            "rate_unit": rate_unit.value,
            "rate_quantity": canonical_decimal(rate_quantity),
            "rate_units": canonical_decimal(rate_units),
            "proration_policy": proration_policy.value,
            "proration_factor": canonical_decimal(proration_factor),
            "coverage_start": canonical_instant(coverage_start),
            "coverage_end": canonical_instant(coverage_end),
            "currency": currency,
            "timezone_name": timezone_name,
            "rating_policy_version": rating_policy_version,
            "offer_version_ref": offer_version_ref,
        }
    )


def occurrence_idempotency_key(
    *,
    scope: Scope,
    contract_line_key: UUID,
    contract_version_id: UUID,
    charge_model_code: str,
    source_code: str,
    source_id: UUID,
    source_version: int,
    period_start: datetime,
    period_end: datetime,
    currency: str,
) -> str:
    payload = {
        "scope": scope_segment(scope),
        "contract_line_key": str(contract_line_key),
        "contract_version_id": str(contract_version_id),
        "charge_model_code": charge_model_code,
        "source_code": source_code,
        "source_id": str(source_id),
        "source_version": source_version,
        "period_start": canonical_instant(period_start),
        "period_end": canonical_instant(period_end),
        "currency": _currency(currency),
    }
    return f"subscriptions:occurrence:{_digest(payload)}"


def entitlement_projection_fingerprint(
    *,
    entitlement_codes: tuple[str, ...],
    quantity: Decimal,
    effective_from: datetime,
    effective_until: datetime | None,
    source_code: str,
    source_id: UUID,
    source_version: int,
) -> str:
    return _digest(
        {
            "entitlement_codes": list(entitlement_codes),
            "quantity": canonical_decimal(quantity),
            "effective_from": canonical_instant(effective_from),
            "effective_until": (
                canonical_instant(effective_until)
                if effective_until is not None
                else None
            ),
            "source_code": source_code,
            "source_id": str(source_id),
            "source_version": source_version,
        }
    )


__all__ = [
    "ExactAmount",
    "canonical_decimal",
    "canonical_instant",
    "entitlement_projection_fingerprint",
    "occurrence_idempotency_key",
    "rating_input_fingerprint",
]
