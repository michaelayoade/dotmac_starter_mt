"""Product-first fixed-recurring rating parity from Dotmac Sub."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from dotmac_subscriptions import (
    FIXED_RATING_POLICY_VERSION,
    BillingCadence,
    CadenceAlignment,
    CollectionTiming,
    EndOfMonthRule,
    ExactAmount,
    Interval,
    IntervalUnit,
    ProrationPolicy,
    RateBasis,
    RatingInput,
    SubscriptionDataError,
    rate_recurring_line,
)

PERIOD = Interval(
    datetime(2026, 8, 1, tzinfo=UTC),
    datetime(2026, 9, 1, tzinfo=UTC),
)


def _cadence(
    *,
    rate_basis: RateBasis = RateBasis.fixed_per_service_period,
    rate_unit: IntervalUnit = IntervalUnit.month,
    proration_policy: ProrationPolicy = ProrationPolicy.none,
) -> BillingCadence:
    return BillingCadence(
        rate_basis=rate_basis,
        rate_unit=rate_unit,
        rate_quantity=Decimal("1"),
        service_interval_unit=IntervalUnit.month,
        service_interval_count=1,
        invoice_interval_unit=IntervalUnit.month,
        invoice_interval_count=1,
        collection_timing=CollectionTiming.advance,
        alignment=CadenceAlignment.contract_anniversary,
        timezone_name="Etc/UTC",
        end_of_month_rule=EndOfMonthRule.clamp_to_month_end,
        proration_policy=proration_policy,
    )


def _rating_input(
    *,
    cadence: BillingCadence | None = None,
    coverage: Interval = PERIOD,
    rating_policy_version: str = FIXED_RATING_POLICY_VERSION,
) -> RatingInput:
    return RatingInput(
        unit_price=ExactAmount(Decimal("100.00"), "EUR", 2),
        quantity=Decimal("2"),
        cadence=cadence or _cadence(),
        period=PERIOD,
        coverage=coverage,
        rating_policy_version=rating_policy_version,
        offer_version_ref="offer-version:1",
    )


def test_fixed_period_rating_is_the_exact_contracted_line_amount() -> None:
    result = rate_recurring_line(_rating_input())

    assert result.pre_tax_amount == ExactAmount(Decimal("200.00"), "EUR", 2)
    assert result.rate_units == Decimal("1")
    assert result.proration_factor == Decimal("1")


def test_rating_is_deterministic_for_the_same_recorded_inputs() -> None:
    first = rate_recurring_line(_rating_input())
    replay = rate_recurring_line(_rating_input())

    assert replay == first


def test_unknown_rating_policy_has_no_implicit_replay_implementation() -> None:
    with pytest.raises(SubscriptionDataError, match="no installed replay"):
        rate_recurring_line(
            _rating_input(rating_policy_version="fixed.future-uninstalled")
        )


def test_per_day_rate_aggregates_over_the_calendar_invoice_period() -> None:
    result = rate_recurring_line(
        _rating_input(
            cadence=_cadence(
                rate_basis=RateBasis.per_rate_unit,
                rate_unit=IntervalUnit.day,
            )
        )
    )

    assert result.rate_units == Decimal("31")
    assert result.pre_tax_amount == ExactAmount(Decimal("6200.00"), "EUR", 2)


def test_declared_calendar_day_proration_narrows_the_charge() -> None:
    result = rate_recurring_line(
        _rating_input(
            cadence=_cadence(proration_policy=ProrationPolicy.actual_calendar_days),
            coverage=Interval(
                datetime(2026, 8, 16, tzinfo=UTC),
                PERIOD.ends_at,
            ),
        )
    )

    assert result.proration_factor == Decimal("16") / Decimal("31")
    assert result.pre_tax_amount == ExactAmount(Decimal("103.23"), "EUR", 2)


def test_usage_metered_rating_without_an_observation_owner_fails_closed() -> None:
    with pytest.raises(SubscriptionDataError, match="separate usage-rating owner"):
        rate_recurring_line(
            _rating_input(cadence=_cadence(rate_basis=RateBasis.usage_metered))
        )
