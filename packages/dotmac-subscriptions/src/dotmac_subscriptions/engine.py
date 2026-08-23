"""Persistence-free fixed-recurring rating behavior ported from Sub."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from dotmac_subscriptions.cadence import (
    BillingCadence,
    Interval,
    ProrationPolicy,
    RateBasis,
    proration_factor,
    rate_units_in,
)
from dotmac_subscriptions.errors import SubscriptionDataError
from dotmac_subscriptions.values import ExactAmount, rating_input_fingerprint

FIXED_RATING_POLICY_VERSION = "fixed.v1"


@dataclass(frozen=True, slots=True)
class RatingInput:
    unit_price: ExactAmount
    quantity: Decimal
    cadence: BillingCadence
    period: Interval
    coverage: Interval
    rating_policy_version: str
    offer_version_ref: str

    def __post_init__(self) -> None:
        if isinstance(self.quantity, float) or not isinstance(self.quantity, Decimal):
            raise SubscriptionDataError(
                "rating.invalid_quantity",
                "Rating quantity must be an exact Decimal.",
            )
        if self.quantity <= 0:
            raise SubscriptionDataError(
                "rating.invalid_quantity",
                "Rating quantity must be positive.",
            )
        if not self.rating_policy_version or not self.offer_version_ref:
            raise SubscriptionDataError(
                "rating.missing_provenance",
                "Rating policy and immutable offer-version provenance are required.",
            )


@dataclass(frozen=True, slots=True)
class RatingResult:
    pre_tax_amount: ExactAmount
    rate_units: Decimal
    proration_factor: Decimal
    request_fingerprint: str


def rate_recurring_line(value: RatingInput) -> RatingResult:
    """Rate one fixed-recurring line without tax, FX, or financial state."""
    cadence = value.cadence
    if value.rating_policy_version != FIXED_RATING_POLICY_VERSION:
        raise SubscriptionDataError(
            "rating.unsupported_policy_version",
            "The recorded rating policy has no installed replay implementation.",
        )
    if cadence.rate_basis is RateBasis.usage_metered:
        raise SubscriptionDataError(
            "rating.usage_not_owned",
            "Usage-metered intent requires the separate usage-rating owner.",
        )
    units = rate_units_in(cadence=cadence, period=value.period)
    factor = proration_factor(
        cadence=cadence,
        period=value.period,
        covered=value.coverage,
    )
    if cadence.rate_basis is RateBasis.fixed_per_service_period:
        raw = value.unit_price.amount * value.quantity
    elif cadence.rate_basis is RateBasis.per_rate_unit:
        raw = value.unit_price.amount * value.quantity * units / cadence.rate_quantity
    else:
        raw = value.unit_price.amount * value.quantity
    if cadence.proration_policy in {
        ProrationPolicy.actual_calendar_days,
        ProrationPolicy.actual_elapsed_time,
    }:
        raw *= factor
    quantum = Decimal(1).scaleb(-value.unit_price.scale)
    amount = raw.quantize(quantum, rounding=ROUND_HALF_UP)
    exact = ExactAmount(
        amount=amount,
        currency=value.unit_price.currency,
        scale=value.unit_price.scale,
    )
    fingerprint = rating_input_fingerprint(
        unit_price=value.unit_price,
        quantity=value.quantity,
        rate_basis=cadence.rate_basis,
        rate_unit=cadence.rate_unit,
        rate_quantity=cadence.rate_quantity,
        rate_units=units,
        proration_policy=cadence.proration_policy,
        proration_factor=factor,
        coverage_start=value.coverage.starts_at,
        coverage_end=value.coverage.ends_at,
        currency=value.unit_price.currency,
        timezone_name=cadence.timezone_name,
        rating_policy_version=value.rating_policy_version,
        offer_version_ref=value.offer_version_ref,
    )
    return RatingResult(exact, units, factor, fingerprint)


__all__ = [
    "FIXED_RATING_POLICY_VERSION",
    "RatingInput",
    "RatingResult",
    "rate_recurring_line",
]
