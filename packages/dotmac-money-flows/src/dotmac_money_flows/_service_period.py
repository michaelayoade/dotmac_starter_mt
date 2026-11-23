"""Verifier for the prepaid service period and its prorated price (GAP-5).

A verifier, not a second writer: Billing owns the ``period`` term and the invoice
period, the charge source builds the command, and F2 only checks that the command
says what the terms and the tenant policy say. Pure functions over ``datetime``
and ``zoneinfo``; no clock, no I/O beyond the tz database.

Periods are built from two units, day and month, as unit x count: the Billing
``period`` term maps to (day, 1), (month, 1), (month, 3) or (month, 12). The end is
counted from the period's own start in the billing time zone, at the same
wall-clock time, with a day that does not exist in the target month clamped to the
month end (``fold=0``). It is pinned by a parity test to the Subscriptions cadence
``service_period`` with ``contract_anniversary`` and ``clamp_to_month_end``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from dotmac_kernel.money import Money

_UNITS: dict[str, tuple[str, int]] = {
    "daily": ("day", 1),
    "monthly": ("month", 1),
    "quarterly": ("month", 3),
    "annually": ("month", 12),
}


def zone_of(name: object) -> ZoneInfo | None:
    """The IANA zone, or None when the name is not a known zone."""
    if not isinstance(name, str) or not name:
        return None
    try:
        return ZoneInfo(name)
    except (ValueError, OSError, KeyError):
        return None


def _add_months(day: date, months: int) -> date:
    total = day.month - 1 + months
    year = day.year + total // 12
    month = total % 12 + 1
    following = date(year + month // 12, month % 12 + 1, 1)
    return date(year, month, min(day.day, (following - timedelta(days=1)).day))


def period_end(start: datetime, period: str, zone: ZoneInfo) -> datetime | None:
    """The end of the period that starts at ``start``; None for an unknown period."""
    unit = _UNITS.get(period)
    if unit is None or start.tzinfo is None or start.utcoffset() is None:
        return None
    kind, count = unit
    local = start.astimezone(zone)
    if kind == "day":
        shifted = local + timedelta(days=count)
    else:
        shifted = datetime.combine(_add_months(local.date(), count), local.timetz())
    return shifted.replace(tzinfo=zone, fold=0).astimezone(UTC)


def day_span(start: datetime, end: datetime, zone: ZoneInfo) -> int:
    """Local calendar dates from ``start`` to ``end`` in ``zone``."""
    return (end.astimezone(zone).date() - start.astimezone(zone).date()).days


def prorated(total: Money, used_days: int, period_days: int) -> Money:
    """``total`` x used / period days, rounded half-up once to the minor units."""
    return Money.of(total.amount * used_days / period_days, total.currency)
