"""Unit proofs for the GAP-5 period verifier and the prorated amount.

``period_end`` is pinned to the Subscriptions cadence ``service_period``
(contract anniversary, clamp to month end) for every unit and count; the prorated
amount is pinned to ``proration_factor(actual_calendar_days)`` and to the rounding
rule (half-up, once). This file may import Subscriptions; the flows module may not.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from dotmac_kernel.money import Currency, Money
from dotmac_money_flows import FundedChargePolicyV1, RatedChargeKeyV1, RatedChargeV1
from dotmac_money_flows._service_period import (
    day_span,
    period_end,
    prorated,
    zone_of,
)
from dotmac_money_flows.funded_charge import _amount_at, _elapsed
from dotmac_subscriptions.cadence import (
    BillingCadence,
    CadenceAlignment,
    CollectionTiming,
    EndOfMonthRule,
    Interval,
    IntervalUnit,
    ProrationPolicy,
    RateBasis,
    proration_factor,
    service_period,
)

NGN = Currency("NGN", 2)
UNITS = {
    "daily": (IntervalUnit.day, 1),
    "monthly": (IntervalUnit.month, 1),
    "quarterly": (IntervalUnit.month, 3),
    "annually": (IntervalUnit.month, 12),
}
ZONES = ("UTC", "Africa/Lagos", "America/New_York", "Europe/London", "Asia/Kolkata")


def _cadence(period: str, zone: str, proration=ProrationPolicy.actual_calendar_days):
    unit, count = UNITS[period]
    return BillingCadence(
        rate_basis=RateBasis.fixed_per_service_period,
        rate_unit=unit,
        rate_quantity=Decimal(1),
        service_interval_unit=unit,
        service_interval_count=count,
        invoice_interval_unit=unit,
        invoice_interval_count=count,
        collection_timing=CollectionTiming.advance,
        alignment=CadenceAlignment.contract_anniversary,
        timezone_name=zone,
        end_of_month_rule=EndOfMonthRule.clamp_to_month_end,
        proration_policy=proration,
    )


def _utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


STARTS = [
    _utc(2027, 1, 28, 9, 0),
    _utc(2027, 1, 29, 9, 0),
    _utc(2027, 1, 30, 9, 0),
    _utc(2027, 1, 31, 9, 0),
    _utc(2027, 1, 30, 23, 30),  # 31 Jan 00:30 in Lagos: a different local day
    _utc(2027, 1, 31, 23, 30),
    _utc(2028, 1, 31, 9, 0),  # leap year
    _utc(2028, 2, 29, 9, 0),
    _utc(2027, 2, 28, 9, 0),
    _utc(2027, 3, 14, 6, 30),  # New York spring-forward day
    _utc(2027, 3, 13, 7, 30),  # the eve of it: +1 day lands in the gap
    _utc(2027, 10, 30, 0, 30),  # London autumn: the clock repeats an hour
    _utc(2027, 5, 31, 22, 0),
    _utc(2027, 8, 31, 12, 0),
    _utc(2027, 12, 31, 23, 59),
]


@pytest.mark.parametrize("period", sorted(UNITS))
@pytest.mark.parametrize("zone", ZONES)
@pytest.mark.parametrize("start", STARTS)
def test_period_end_equals_the_subscriptions_service_period(period, zone, start):
    expected = service_period(
        cadence=_cadence(period, zone), contract_start=start
    ).ends_at
    assert period_end(start, period, ZoneInfo(zone)) == expected


def test_period_end_counts_each_period_from_its_own_start_and_clamps():
    lagos = ZoneInfo("Africa/Lagos")
    first = period_end(_utc(2027, 1, 31, 9, 0), "monthly", lagos)
    assert first == _utc(2027, 2, 28, 9, 0)
    assert period_end(first, "monthly", lagos) == _utc(2027, 3, 28, 9, 0)
    assert period_end(_utc(2028, 1, 31, 9, 0), "monthly", lagos) == _utc(
        2028, 2, 29, 9, 0
    )
    assert period_end(_utc(2027, 1, 31, 9, 0), "quarterly", lagos) == _utc(
        2027, 4, 30, 9, 0
    )
    assert period_end(_utc(2028, 2, 29, 9, 0), "annually", lagos) == _utc(
        2029, 2, 28, 9, 0
    )
    assert period_end(_utc(2027, 3, 10, 9, 0), "daily", lagos) == _utc(
        2027, 3, 11, 9, 0
    )


def test_the_time_zone_decides_the_local_day():
    # 30 Jan 23:30Z is 31 Jan 00:30 in Lagos: a month later is 28 Feb 00:30 Lagos.
    start = _utc(2027, 1, 30, 23, 30)
    assert period_end(start, "monthly", ZoneInfo("Africa/Lagos")) == _utc(
        2027, 2, 27, 23, 30
    )
    assert period_end(start, "monthly", ZoneInfo("UTC")) == _utc(2027, 2, 28, 23, 30)


def test_an_unknown_period_or_a_naive_start_has_no_end():
    lagos = ZoneInfo("Africa/Lagos")
    assert period_end(_utc(2027, 1, 1), "weekly", lagos) is None
    assert period_end(datetime(2027, 1, 1), "monthly", lagos) is None


@pytest.mark.parametrize("name", ["Mars/Olympus", "", "Africa", "../etc/passwd", None])
def test_an_unknown_zone_is_none(name):
    assert zone_of(name) is None


def test_a_known_zone_is_found():
    assert zone_of("Africa/Lagos") == ZoneInfo("Africa/Lagos")


# --------------------------------------------------------------------------
# The amount due
# --------------------------------------------------------------------------
def _charge(total: str, starts=None, ends=None) -> RatedChargeV1:
    return RatedChargeV1(
        key=RatedChargeKeyV1("subscriptions", "recurring_charge_occurrence", "x", "1"),
        billing_account_id=uuid4(),
        service_ref="service:one",
        total=Money.of(total, NGN),
        due_at=_utc(2027, 4, 15),
        period_starts_at=starts,
        period_ends_at=ends,
    )


UTC_POLICY = FundedChargePolicyV1("p1")
LAGOS_POLICY = FundedChargePolicyV1("p1", billing_timezone="Africa/Lagos")
APRIL = (_utc(2027, 4, 1, 0, 0), _utc(2027, 5, 1, 0, 0))


def test_the_policy_defaults_are_calendar_day_proration_in_utc():
    assert UTC_POLICY.proration == "actual_calendar_days"
    assert UTC_POLICY.billing_timezone == "UTC"
    assert UTC_POLICY.reevaluation_batch_cap == 20


def test_an_ordinary_charge_is_due_in_full_whenever_it_is_paid():
    charge = _charge("100.01")
    assert _amount_at(charge, _utc(2027, 4, 30), UTC_POLICY) == charge.total


def test_rounding_is_half_up_once_to_the_minor_units():
    # 100.01 x 15/30 = 50.005: half-up gives 50.01, banker's would give 50.00.
    charge = _charge("100.01", *APRIL)
    amount = _amount_at(charge, _utc(2027, 4, 16, 10, 0), UTC_POLICY)
    assert amount == Money.of("50.01", NGN)
    bankers = (Decimal("100.01") * 15 / 30).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_EVEN
    )
    assert bankers == Decimal("50.00") != amount.amount
    assert prorated(Money.of("100.01", NGN), 15, 30) == Money.of("50.01", NGN)


def test_rounding_follows_the_currency_minor_units():
    yen = Currency("JPY", 0)
    assert prorated(Money.of("1001", yen), 15, 30).amount == Decimal("501")
    assert prorated(Money.of("1000", yen), 1, 3).amount == Decimal("333")


@pytest.mark.parametrize("total", ["100.01", "300.00", "107.50", "999.99", "0.05"])
@pytest.mark.parametrize(
    ("period", "start"),
    [
        ("monthly", _utc(2027, 2, 1, 0, 0)),  # 28 days
        ("monthly", _utc(2027, 3, 1, 0, 0)),  # 31 days
        ("monthly", _utc(2028, 2, 1, 0, 0)),  # 29 days
        ("monthly", _utc(2027, 4, 1, 0, 0)),  # 30 days
        ("quarterly", _utc(2027, 1, 1, 0, 0)),  # 90
        ("quarterly", _utc(2027, 4, 1, 0, 0)),  # 91
        ("quarterly", _utc(2027, 7, 1, 0, 0)),  # 92
        ("quarterly", _utc(2028, 1, 1, 0, 0)),  # 91 (leap)
        ("annually", _utc(2027, 1, 1, 0, 0)),  # 365
        ("annually", _utc(2027, 7, 1, 0, 0)),  # 366 (spans Feb 2028)
    ],
)
def test_the_amount_matches_the_subscriptions_actual_calendar_days_factor(
    total, period, start
):
    zone = ZoneInfo("UTC")
    end = period_end(start, period, zone)
    assert end is not None
    whole = Interval(start, end)
    charge = _charge(total, start, end)
    for offset in range(0, day_span(start, end, zone), 7):
        issue_at = start + timedelta(days=offset, hours=5)
        factor = proration_factor(
            cadence=_cadence(period, "UTC"),
            period=whole,
            covered=Interval(issue_at, end),
        )
        expected = Money.of(Decimal(total) * factor, NGN)
        assert _amount_at(charge, issue_at, UTC_POLICY) == expected


def test_the_days_are_those_of_the_actual_enclosing_period_never_a_fixed_30():
    last_day = _utc(2027, 2, 28, 10, 0), _utc(2027, 3, 31, 10, 0)
    feb = _charge("300.00", _utc(2027, 2, 1), _utc(2027, 3, 1))
    mar = _charge("300.00", _utc(2027, 3, 1), _utc(2027, 4, 1))
    assert _amount_at(feb, last_day[0], UTC_POLICY) == Money.of("10.71", NGN)
    assert _amount_at(mar, last_day[1], UTC_POLICY) == Money.of("9.68", NGN)


def test_the_days_are_counted_in_the_billing_time_zone():
    # The period is 1 Apr 00:00 to 1 May 00:00 Lagos (30 local days).
    start, end = _utc(2027, 3, 31, 23, 0), _utc(2027, 4, 30, 23, 0)
    charge = _charge("300.00", start, end)
    paid = _utc(2027, 4, 30, 22, 30)  # 30 Apr 23:30 in Lagos: one local day left
    assert _amount_at(charge, paid, LAGOS_POLICY) == Money.of("10.00", NGN)
    # The same instants counted in UTC: the end falls on the payment's own date.
    assert _amount_at(charge, paid, UTC_POLICY) is None
    assert _amount_at(charge, _utc(2027, 4, 14, 23, 30), LAGOS_POLICY) == Money.of(
        "160.00", NGN
    )  # 15 Apr 00:30 Lagos: 16 of 30 local days


def test_a_charge_that_cannot_be_priced_has_no_amount():
    charge = _charge("300.00", *APRIL)
    inside = _utc(2027, 4, 15)
    off = FundedChargePolicyV1("p", proration="none")
    assert _amount_at(charge, inside, off) is None
    assert _amount_at(charge, APRIL[1], UTC_POLICY) is None  # elapsed
    assert _amount_at(charge, _utc(2027, 3, 31), UTC_POLICY) is None  # not begun
    # An unknown zone is refused when the policy is read (``_read_policy``); the
    # pure helper never prices against it.
    with pytest.raises(ValueError):
        _amount_at(charge, inside, FundedChargePolicyV1("p", billing_timezone="Mars"))
    one_sided = _charge("300.00", APRIL[0], None)
    assert _amount_at(one_sided, inside, UTC_POLICY) is None
    reversed_ = _charge("300.00", APRIL[1], APRIL[0])
    assert _amount_at(reversed_, inside, UTC_POLICY) is None
    naive = _charge("300.00", datetime(2027, 4, 1), datetime(2027, 5, 1))
    assert _amount_at(naive, inside, UTC_POLICY) is None


def test_proration_off_has_no_amount_for_a_partial_period():
    charge = _charge("300.00", *APRIL)
    off = FundedChargePolicyV1("p", proration="none")
    assert _amount_at(charge, _utc(2027, 4, 15), off) is None
    assert _amount_at(_charge("300.00"), _utc(2027, 4, 15), off) == charge.total


def test_nothing_left_to_bill_is_elapsed():
    charge = _charge("300.00", *APRIL)
    assert not _elapsed(charge, _utc(2027, 4, 30, 23, 0), UTC_POLICY)
    assert _elapsed(charge, APRIL[1], UTC_POLICY)
    assert _elapsed(charge, _utc(2027, 5, 2), UTC_POLICY)
    # Time left but on the end's own local date: zero days.
    noon = (_utc(2027, 4, 1, 12, 0), _utc(2027, 5, 1, 12, 0))
    assert _elapsed(_charge("300.00", *noon), _utc(2027, 5, 1, 9, 0), UTC_POLICY)
    assert not _elapsed(_charge("300.00"), _utc(2030, 1, 1), UTC_POLICY)
    off = FundedChargePolicyV1("p", proration="none")
    assert _elapsed(charge, APRIL[1], off)
