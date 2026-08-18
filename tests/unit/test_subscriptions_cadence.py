"""Preserved Sub cadence parity for the reusable subscriptions owner.

Source: ``dotmac_sub:tests/test_billing_cadence.py`` at ``27c76aaeebb7``.
Only imports and the error namespace change; the calendar expectations are the
product-first behavior being extracted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from dotmac_subscriptions import (
    BillingCadence,
    CadenceAlignment,
    CadenceError,
    CollectionTiming,
    EndOfMonthRule,
    Interval,
    IntervalUnit,
    ProrationPolicy,
    RateBasis,
    invoice_period,
    period_containing,
    proration_factor,
    rate_units_in,
    service_period,
)

ZONE = "Africa/Lagos"


def _cadence(
    *,
    service_unit: IntervalUnit = IntervalUnit.month,
    service_count: int = 1,
    invoice_unit: IntervalUnit | None = None,
    invoice_count: int | None = None,
    rate_unit: IntervalUnit | None = None,
    proration: ProrationPolicy = ProrationPolicy.none,
    end_of_month: EndOfMonthRule = EndOfMonthRule.clamp_to_month_end,
    alignment: CadenceAlignment = CadenceAlignment.contract_anniversary,
    anchor_day: int | None = None,
    timezone_name: str = ZONE,
    collection_timing: CollectionTiming = CollectionTiming.advance,
) -> BillingCadence:
    return BillingCadence(
        rate_basis=RateBasis.fixed_per_service_period,
        rate_unit=rate_unit or service_unit,
        rate_quantity=Decimal("1"),
        service_interval_unit=service_unit,
        service_interval_count=service_count,
        invoice_interval_unit=invoice_unit or service_unit,
        invoice_interval_count=invoice_count or service_count,
        collection_timing=collection_timing,
        alignment=alignment,
        timezone_name=timezone_name,
        end_of_month_rule=end_of_month,
        proration_policy=proration,
        anchor_day=anchor_day,
    )


def test_quarterly_is_three_calendar_months_not_ninety_days() -> None:
    cadence = _cadence(service_unit=IntervalUnit.month, service_count=3)
    start = datetime(2026, 1, 15, tzinfo=UTC)

    period = service_period(cadence=cadence, contract_start=start, index=0)

    assert period.ends_at.astimezone(UTC).month == 4
    assert period.ends_at.astimezone(UTC).day == 15
    assert period.duration.days == 90
    next_period = service_period(cadence=cadence, contract_start=start, index=1)
    assert next_period.duration.days == 91


def test_annual_is_twelve_calendar_months_across_a_leap_year() -> None:
    cadence = _cadence(service_unit=IntervalUnit.year, service_count=1)
    period = service_period(
        cadence=cadence,
        contract_start=datetime(2027, 3, 1, tzinfo=UTC),
        index=0,
    )

    assert period.ends_at.astimezone(UTC).year == 2028
    assert period.ends_at.astimezone(UTC).month == 3
    assert period.ends_at.astimezone(UTC).day == 1
    assert period.duration.days == 366


def test_month_end_anniversary_clamps_under_the_declared_rule() -> None:
    cadence = _cadence(end_of_month=EndOfMonthRule.clamp_to_month_end)
    start = datetime(2026, 1, 31, 9, 0, tzinfo=UTC)

    february = service_period(cadence=cadence, contract_start=start, index=0)
    march = service_period(cadence=cadence, contract_start=start, index=1)

    assert february.ends_at.astimezone(UTC).month == 2
    assert february.ends_at.astimezone(UTC).day == 28
    assert march.ends_at.astimezone(UTC).day == 31


def test_strict_same_day_rule_fails_closed_instead_of_shifting() -> None:
    cadence = _cadence(end_of_month=EndOfMonthRule.strict_same_day_or_skip)

    with pytest.raises(CadenceError) as excinfo:
        service_period(
            cadence=cadence,
            contract_start=datetime(2026, 1, 31, tzinfo=UTC),
            index=0,
        )

    assert excinfo.value.code == "subscriptions.cadence.skipped_month_boundary"


def test_consecutive_periods_are_contiguous_and_half_open() -> None:
    cadence = _cadence()
    start = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)

    first = service_period(cadence=cadence, contract_start=start, index=0)
    second = service_period(cadence=cadence, contract_start=start, index=1)

    assert first.ends_at == second.starts_at
    assert first.contains(first.starts_at)
    assert not first.contains(first.ends_at)
    assert second.contains(second.starts_at)


def test_rate_unit_is_independent_of_invoice_interval() -> None:
    cadence = _cadence(
        service_unit=IntervalUnit.month,
        invoice_unit=IntervalUnit.month,
        rate_unit=IntervalUnit.day,
    )
    period = invoice_period(
        cadence=cadence,
        contract_start=datetime(2026, 6, 1, tzinfo=UTC),
        index=0,
    )

    assert rate_units_in(cadence=cadence, period=period) == Decimal(30)


def test_clamped_month_end_is_one_whole_rate_unit() -> None:
    cadence = _cadence(rate_unit=IntervalUnit.month)
    period = invoice_period(
        cadence=cadence,
        contract_start=datetime(2026, 1, 31, tzinfo=UTC),
        index=0,
    )

    assert period.ends_at == datetime(2026, 2, 28, tzinfo=UTC)
    assert rate_units_in(cadence=cadence, period=period) == Decimal("1")


def test_annual_service_period_can_be_invoiced_quarterly() -> None:
    cadence = _cadence(
        service_unit=IntervalUnit.year,
        invoice_unit=IntervalUnit.month,
        invoice_count=3,
    )
    start = datetime(2026, 1, 1, tzinfo=UTC)

    service = service_period(cadence=cadence, contract_start=start, index=0)
    first_invoice = invoice_period(cadence=cadence, contract_start=start, index=0)
    fourth_invoice = invoice_period(cadence=cadence, contract_start=start, index=3)

    assert service.ends_at.astimezone(UTC).year == 2027
    assert first_invoice.ends_at.astimezone(UTC).month == 4
    assert fourth_invoice.ends_at == service.ends_at


def test_period_containing_walks_calendar_periods() -> None:
    cadence = _cadence()
    index, interval = period_containing(
        cadence=cadence,
        contract_start=datetime(2026, 1, 31, tzinfo=UTC),
        moment=datetime(2026, 4, 2, tzinfo=UTC),
    )

    assert index == 2
    assert interval.contains(datetime(2026, 4, 2, tzinfo=UTC))


def test_moment_before_contract_start_fails_closed() -> None:
    with pytest.raises(CadenceError) as excinfo:
        period_containing(
            cadence=_cadence(),
            contract_start=datetime(2026, 2, 1, tzinfo=UTC),
            moment=datetime(2026, 1, 1, tzinfo=UTC),
        )

    assert excinfo.value.code == "subscriptions.cadence.moment_before_contract"


def test_calendar_day_proration_is_declared_not_inferred() -> None:
    cadence = _cadence(proration=ProrationPolicy.actual_calendar_days)
    period = service_period(
        cadence=cadence,
        contract_start=datetime(2026, 6, 1, tzinfo=UTC),
        index=0,
    )
    covered = Interval(
        starts_at=datetime(2026, 6, 16, tzinfo=UTC),
        ends_at=period.ends_at,
    )

    assert proration_factor(cadence=cadence, period=period, covered=covered) == (
        Decimal(15) / Decimal(30)
    )


def test_no_proration_policy_bills_the_full_period() -> None:
    cadence = _cadence(proration=ProrationPolicy.none)
    period = service_period(
        cadence=cadence,
        contract_start=datetime(2026, 6, 1, tzinfo=UTC),
        index=0,
    )
    covered = Interval(
        starts_at=datetime(2026, 6, 16, tzinfo=UTC),
        ends_at=period.ends_at,
    )

    assert proration_factor(cadence=cadence, period=period, covered=covered) == (
        Decimal("1")
    )


def test_covered_interval_outside_the_period_fails_closed() -> None:
    cadence = _cadence(proration=ProrationPolicy.actual_calendar_days)
    period = service_period(
        cadence=cadence,
        contract_start=datetime(2026, 6, 1, tzinfo=UTC),
        index=0,
    )

    with pytest.raises(CadenceError) as excinfo:
        proration_factor(
            cadence=cadence,
            period=period,
            covered=Interval(
                starts_at=datetime(2026, 5, 20, tzinfo=UTC),
                ends_at=period.ends_at,
            ),
        )

    assert excinfo.value.code == "subscriptions.cadence.covered_outside_period"


def test_calendar_alignment_snaps_to_the_month_boundary() -> None:
    cadence = _cadence(alignment=CadenceAlignment.calendar_period_start)
    period = service_period(
        cadence=cadence,
        contract_start=datetime(2026, 6, 17, 14, 30, tzinfo=UTC),
        index=0,
    )
    local_start = period.starts_at.astimezone(cadence.zone())

    assert (local_start.day, local_start.hour, local_start.minute) == (1, 0, 0)
    assert local_start.month == 6


def test_periods_are_computed_in_the_contract_timezone() -> None:
    cadence = _cadence(alignment=CadenceAlignment.calendar_period_start)
    period = service_period(
        cadence=cadence,
        contract_start=datetime(2026, 6, 17, tzinfo=UTC),
        index=0,
    )

    assert period.starts_at.astimezone(UTC).hour == 23
    assert period.starts_at.astimezone(UTC).day == 31


def test_naive_datetimes_are_refused() -> None:
    with pytest.raises(CadenceError) as excinfo:
        service_period(
            cadence=_cadence(),
            contract_start=datetime(2026, 1, 1),
        )

    assert excinfo.value.code == "subscriptions.cadence.naive_datetime"


def test_unknown_timezone_fails_at_construction() -> None:
    with pytest.raises(CadenceError) as excinfo:
        _cadence(timezone_name="Mars/Olympus_Mons")

    assert excinfo.value.code == "subscriptions.cadence.unknown_timezone"


def test_fixed_anchor_alignment_requires_an_anchor_day() -> None:
    with pytest.raises(CadenceError) as excinfo:
        _cadence(alignment=CadenceAlignment.fixed_anchor_day)

    assert excinfo.value.code == "subscriptions.cadence.missing_anchor_day"


def test_fixed_anchor_clamp_preserves_the_declared_day_across_a_short_month() -> None:
    cadence = _cadence(
        alignment=CadenceAlignment.fixed_anchor_day,
        anchor_day=31,
    )
    period = service_period(
        cadence=cadence,
        contract_start=datetime(2026, 2, 15, tzinfo=UTC),
    )

    local_start = period.starts_at.astimezone(cadence.zone())
    local_end = period.ends_at.astimezone(cadence.zone())
    assert (local_start.year, local_start.month, local_start.day, local_start.hour) == (
        2026,
        1,
        31,
        0,
    )
    assert (local_end.month, local_end.day, local_end.hour) == (2, 28, 0)


def test_interval_must_end_after_it_starts() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)

    with pytest.raises(CadenceError) as excinfo:
        Interval(starts_at=moment, ends_at=moment)

    assert excinfo.value.code == "subscriptions.cadence.invalid_interval"


def test_advance_and_arrears_share_the_same_calendar_path() -> None:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    advance = service_period(
        cadence=_cadence(collection_timing=CollectionTiming.advance),
        contract_start=start,
    )
    arrears = service_period(
        cadence=_cadence(collection_timing=CollectionTiming.arrears),
        contract_start=start,
    )

    assert advance == arrears
