"""Composable cadence and calendar arithmetic ported product-first from Sub.

The source is ``dotmac_sub:app/services/billing/cadence.py`` at
``27c76aaeebb7``. Product model imports were replaced by module-owned calendar
vocabularies; the half-open interval, timezone, month-anchor and proration
behavior is preserved.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotmac_subscriptions.errors import CadenceError


class RateBasis(str, Enum):
    fixed_per_service_period = "fixed_per_service_period"
    per_rate_unit = "per_rate_unit"
    per_quantity = "per_quantity"
    usage_metered = "usage_metered"


class IntervalUnit(str, Enum):
    """Closed calendar vocabulary; products cannot invent a unit of time."""

    day = "day"
    week = "week"
    month = "month"
    year = "year"


class CollectionTiming(str, Enum):
    advance = "advance"
    arrears = "arrears"


class CadenceAlignment(str, Enum):
    contract_anniversary = "contract_anniversary"
    calendar_period_start = "calendar_period_start"
    fixed_anchor_day = "fixed_anchor_day"


class EndOfMonthRule(str, Enum):
    clamp_to_month_end = "clamp_to_month_end"
    strict_same_day_or_skip = "strict_same_day_or_skip"


class ProrationPolicy(str, Enum):
    none = "none"
    full_period = "full_period"
    actual_calendar_days = "actual_calendar_days"
    actual_elapsed_time = "actual_elapsed_time"


@dataclass(frozen=True, slots=True)
class Interval:
    """A half-open ``[starts_at, ends_at)`` instant range."""

    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        _require_aware(self.starts_at, field="starts_at")
        _require_aware(self.ends_at, field="ends_at")
        if self.ends_at <= self.starts_at:
            raise CadenceError(
                "cadence.invalid_interval",
                "An interval must end strictly after it starts.",
                {
                    "starts_at": self.starts_at.isoformat(),
                    "ends_at": self.ends_at.isoformat(),
                },
            )

    def contains(self, moment: datetime) -> bool:
        _require_aware(moment, field="moment")
        return self.starts_at <= moment < self.ends_at

    @property
    def duration(self) -> timedelta:
        return self.ends_at - self.starts_at


@dataclass(frozen=True, slots=True)
class BillingCadence:
    """Typed, composable cadence rather than a growing preset list."""

    rate_basis: RateBasis
    rate_unit: IntervalUnit
    rate_quantity: Decimal
    service_interval_unit: IntervalUnit
    service_interval_count: int
    invoice_interval_unit: IntervalUnit
    invoice_interval_count: int
    collection_timing: CollectionTiming
    alignment: CadenceAlignment
    timezone_name: str
    end_of_month_rule: EndOfMonthRule
    proration_policy: ProrationPolicy
    anchor_day: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.rate_quantity, float) or not isinstance(
            self.rate_quantity, Decimal
        ):
            raise CadenceError(
                "cadence.invalid_rate_quantity",
                "Rate quantity must be an exact Decimal.",
            )
        if self.service_interval_count < 1 or self.invoice_interval_count < 1:
            raise CadenceError(
                "cadence.invalid_interval_count",
                "Service and invoice interval counts must be positive.",
            )
        if self.rate_quantity <= 0:
            raise CadenceError(
                "cadence.invalid_rate_quantity",
                "Rate quantity must be positive.",
            )
        if self.anchor_day is not None and not 1 <= self.anchor_day <= 31:
            raise CadenceError(
                "cadence.invalid_anchor_day",
                "Anchor day must fall between 1 and 31.",
                {"anchor_day": self.anchor_day},
            )
        if (
            self.alignment is CadenceAlignment.fixed_anchor_day
            and self.anchor_day is None
        ):
            raise CadenceError(
                "cadence.missing_anchor_day",
                "Fixed-anchor alignment requires an anchor day.",
            )
        self.zone()

    def zone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise CadenceError(
                "cadence.unknown_timezone",
                "Contract timezone is not a known IANA zone.",
                {"timezone": self.timezone_name},
            ) from exc


def _require_aware(moment: datetime, *, field: str) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise CadenceError(
            "cadence.naive_datetime",
            "Subscription instants must be timezone-aware.",
            {"field": field},
        )
    return moment


def _add_months(anchor: date, months: int, rule: EndOfMonthRule) -> date | None:
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    if anchor.day <= last_day:
        return date(year, month, anchor.day)
    if rule is EndOfMonthRule.clamp_to_month_end:
        return date(year, month, last_day)
    return None


def _anchor_date(
    *, year: int, month: int, anchor_day: int, rule: EndOfMonthRule
) -> date | None:
    last_day = calendar.monthrange(year, month)[1]
    if anchor_day <= last_day:
        return date(year, month, anchor_day)
    if rule is EndOfMonthRule.clamp_to_month_end:
        return date(year, month, last_day)
    return None


def _shift(
    anchor: datetime,
    *,
    unit: IntervalUnit,
    count: int,
    cadence: BillingCadence,
) -> datetime:
    zone = cadence.zone()
    local = anchor.astimezone(zone)
    if unit is IntervalUnit.day:
        shifted_local = local + timedelta(days=count)
    elif unit is IntervalUnit.week:
        shifted_local = local + timedelta(weeks=count)
    elif unit in (IntervalUnit.month, IntervalUnit.year):
        months = count * (12 if unit is IntervalUnit.year else 1)
        target = _add_months(local.date(), months, cadence.end_of_month_rule)
        if target is None:
            raise CadenceError(
                "cadence.skipped_month_boundary",
                "Strict same-day rule cannot place this boundary in the target month.",
                {
                    "anchor": anchor.isoformat(),
                    "months": months,
                    "rule": cadence.end_of_month_rule.value,
                },
            )
        shifted_local = datetime.combine(target, local.timetz())
    else:  # pragma: no cover
        raise CadenceError(
            "cadence.unsupported_unit",
            "Unsupported calendar interval unit.",
            {"unit": unit.value},
        )
    return shifted_local.replace(tzinfo=zone, fold=0).astimezone(UTC)


def _align_start(start: datetime, cadence: BillingCadence) -> datetime:
    if cadence.alignment is CadenceAlignment.contract_anniversary:
        return start
    zone = cadence.zone()
    local = start.astimezone(zone)
    if cadence.alignment is CadenceAlignment.calendar_period_start:
        unit = cadence.service_interval_unit
        if unit is IntervalUnit.day:
            aligned = local.replace(hour=0, minute=0, second=0, microsecond=0)
        elif unit is IntervalUnit.week:
            midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
            aligned = midnight - timedelta(days=midnight.weekday())
        elif unit is IntervalUnit.month:
            aligned = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            aligned = local.replace(
                month=1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        return aligned.replace(tzinfo=zone, fold=0).astimezone(UTC)
    anchor_day = cadence.anchor_day
    if anchor_day is None:  # defensive if construction-time validation is bypassed
        raise CadenceError(
            "cadence.missing_anchor_day",
            "Fixed-anchor alignment requires an anchor day.",
        )
    target = _anchor_date(
        year=local.year,
        month=local.month,
        anchor_day=anchor_day,
        rule=cadence.end_of_month_rule,
    )
    if target is None:
        raise CadenceError(
            "cadence.skipped_anchor_boundary",
            "Strict same-day rule cannot place the fixed anchor this month.",
        )
    anchored = datetime.combine(target, local.timetz())
    anchored = anchored.replace(hour=0, minute=0, second=0, microsecond=0)
    if anchored > local:
        previous_month = local.month - 1 or 12
        previous_year = local.year - 1 if local.month == 1 else local.year
        previous = _anchor_date(
            year=previous_year,
            month=previous_month,
            anchor_day=anchor_day,
            rule=cadence.end_of_month_rule,
        )
        if previous is None:
            raise CadenceError(
                "cadence.skipped_anchor_boundary",
                "Strict same-day rule cannot place the prior fixed anchor.",
            )
        anchored = datetime.combine(previous, anchored.timetz())
    return anchored.replace(tzinfo=zone, fold=0).astimezone(UTC)


def _nth_interval(
    *,
    aligned: datetime,
    unit: IntervalUnit,
    count: int,
    index: int,
    cadence: BillingCadence,
) -> Interval:
    start = (
        aligned
        if index == 0
        else _shift(aligned, unit=unit, count=count * index, cadence=cadence)
    )
    end = _shift(aligned, unit=unit, count=count * (index + 1), cadence=cadence)
    return Interval(starts_at=start, ends_at=end)


def service_period(
    *, cadence: BillingCadence, contract_start: datetime, index: int = 0
) -> Interval:
    _require_aware(contract_start, field="contract_start")
    if index < 0:
        raise CadenceError(
            "cadence.invalid_period_index",
            "Period index cannot be negative.",
            {"index": index},
        )
    return _nth_interval(
        aligned=_align_start(contract_start, cadence),
        unit=cadence.service_interval_unit,
        count=cadence.service_interval_count,
        index=index,
        cadence=cadence,
    )


def invoice_period(
    *, cadence: BillingCadence, contract_start: datetime, index: int = 0
) -> Interval:
    _require_aware(contract_start, field="contract_start")
    if index < 0:
        raise CadenceError(
            "cadence.invalid_period_index",
            "Period index cannot be negative.",
            {"index": index},
        )
    return _nth_interval(
        aligned=_align_start(contract_start, cadence),
        unit=cadence.invoice_interval_unit,
        count=cadence.invoice_interval_count,
        index=index,
        cadence=cadence,
    )


def period_containing(
    *,
    cadence: BillingCadence,
    contract_start: datetime,
    moment: datetime,
    max_periods: int = 4096,
) -> tuple[int, Interval]:
    _require_aware(moment, field="moment")
    aligned = _align_start(contract_start, cadence)
    if moment < aligned:
        raise CadenceError(
            "cadence.moment_before_contract",
            "Moment precedes the aligned contract start.",
            {"moment": moment.isoformat(), "aligned_start": aligned.isoformat()},
        )
    for index in range(max_periods):
        interval = service_period(
            cadence=cadence,
            contract_start=contract_start,
            index=index,
        )
        if interval.contains(moment):
            return index, interval
    raise CadenceError(
        "cadence.period_walk_exhausted",
        "Period walk exceeded its bound; check the contract anchor.",
        {"max_periods": max_periods, "moment": moment.isoformat()},
    )


def proration_factor(
    *, cadence: BillingCadence, period: Interval, covered: Interval
) -> Decimal:
    if not (
        covered.starts_at >= period.starts_at and covered.ends_at <= period.ends_at
    ):
        raise CadenceError(
            "cadence.covered_outside_period",
            "Covered interval must fall inside the subscription period.",
            {
                "period_start": period.starts_at.isoformat(),
                "period_end": period.ends_at.isoformat(),
                "covered_start": covered.starts_at.isoformat(),
                "covered_end": covered.ends_at.isoformat(),
            },
        )
    policy = cadence.proration_policy
    if policy in (ProrationPolicy.none, ProrationPolicy.full_period):
        return Decimal("1")
    if policy is ProrationPolicy.actual_elapsed_time:
        whole = Decimal(str(period.duration.total_seconds()))
        part = Decimal(str(covered.duration.total_seconds()))
        return part / whole if whole > 0 else Decimal("0")
    zone = cadence.zone()
    period_days = _calendar_day_span(period, zone)
    covered_days = _calendar_day_span(covered, zone)
    if period_days <= 0:
        return Decimal("0")
    return Decimal(covered_days) / Decimal(period_days)


def _calendar_day_span(interval: Interval, zone: ZoneInfo) -> int:
    start_local = interval.starts_at.astimezone(zone).date()
    end_local = interval.ends_at.astimezone(zone).date()
    return (end_local - start_local).days


def rate_units_in(*, cadence: BillingCadence, period: Interval) -> Decimal:
    zone = cadence.zone()
    unit = cadence.rate_unit
    if unit is IntervalUnit.day:
        return Decimal(_calendar_day_span(period, zone))
    if unit is IntervalUnit.week:
        return Decimal(_calendar_day_span(period, zone)) / Decimal(7)
    start_local = period.starts_at.astimezone(zone).date()
    end_local = period.ends_at.astimezone(zone).date()
    months = (end_local.year - start_local.year) * 12 + (
        end_local.month - start_local.month
    )
    # Compare the real calendar anniversary rather than day numbers. A
    # clamped 31 January -> 28 February interval is one whole month even
    # though 28 < 31. The source implementation's day-only comparison rated
    # that valid period as zero units.
    anniversary = _add_months(start_local, months, cadence.end_of_month_rule)
    if anniversary is None or anniversary > end_local:
        months -= 1
    if unit is IntervalUnit.month:
        return Decimal(months)
    return Decimal(months) / Decimal(12)


__all__ = [
    "BillingCadence",
    "CadenceAlignment",
    "CollectionTiming",
    "EndOfMonthRule",
    "Interval",
    "IntervalUnit",
    "ProrationPolicy",
    "RateBasis",
    "invoice_period",
    "period_containing",
    "proration_factor",
    "rate_units_in",
    "service_period",
]
