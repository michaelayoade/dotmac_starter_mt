"""Next-due computation for declared jobs (pure; no clock, no I/O).

``next_due`` takes the instant to count from as an ARGUMENT: this module never
reads a clock.

Cron semantics (five fields, evaluated on the tenant's wall clock):

* day-of-month and day-of-week combine as Vixie cron does: if BOTH are
  restricted a day matches when EITHER matches, otherwise both must match;
* day-of-week 7 is Sunday (same as 0);
* a wall time that does not exist (spring-forward gap) runs once, shifted
  forward by the length of the gap (02:30 becomes 03:30);
* a wall time that happens twice (fall-back overlap) runs once, at its first
  occurrence; the repeated pass never fires it again;
* a job never catches up: ``after`` is the completion instant, and the next due
  is strictly later than it, so missed ticks are skipped, not replayed.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from dotmac_kernel.jobs import JobSpec, JobSpecError

# Far enough for "29 Feb" style expressions, bounded so a never-matching
# expression (for example 31 Feb) fails closed instead of looping.
_MAX_DAYS = 366 * 9


def _expand(text: str, low: int, high: int) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        base, _, step_text = part.partition("/")
        step = int(step_text) if step_text else 1
        if base == "*":
            start, end = low, high
        else:
            first, dash, last = base.partition("-")
            start = int(first)
            end = int(last) if dash else (high if step_text else start)
        values.update(range(start, end + 1, step))
    return frozenset(values)


class CronSchedule:
    """A parsed five-field expression."""

    __slots__ = (
        "_dom",
        "_dom_star",
        "_dow",
        "_dow_star",
        "_hours",
        "_minutes",
        "_months",
    )

    def __init__(self, expression: str) -> None:
        fields = expression.split()
        if len(fields) != 5:
            raise JobSpecError(f"cron {expression!r} must have 5 fields")
        minute, hour, dom, month, dow = fields
        self._minutes = sorted(_expand(minute, 0, 59))
        self._hours = sorted(_expand(hour, 0, 23))
        self._dom = _expand(dom, 1, 31)
        self._months = _expand(month, 1, 12)
        self._dow = frozenset(v % 7 for v in _expand(dow, 0, 7))
        self._dom_star = dom.startswith("*")
        self._dow_star = dow.startswith("*")

    def _day_matches(self, day: date) -> bool:
        if day.month not in self._months:
            return False
        dom_ok = day.day in self._dom
        dow_ok = (day.isoweekday() % 7) in self._dow
        if self._dom_star or self._dow_star:
            return dom_ok and dow_ok
        return dom_ok or dow_ok

    def next_after(self, after: datetime, tz: ZoneInfo) -> datetime:
        """The first matching instant strictly after ``after`` (UTC, aware)."""
        if after.tzinfo is None or after.utcoffset() is None:
            raise JobSpecError("after must be timezone-aware")
        local_after = after.astimezone(tz)
        day = local_after.date()
        for offset in range(_MAX_DAYS):
            current = day + timedelta(days=offset)
            if not self._day_matches(current):
                continue
            for hour in self._hours:
                for minute in self._minutes:
                    candidate = _resolve(current, hour, minute, tz)
                    if candidate > after:
                        return candidate
        raise JobSpecError("cron expression never matches within nine years")


def _resolve(day: date, hour: int, minute: int, tz: ZoneInfo) -> datetime:
    """Wall time to a UTC instant: gap -> shifted forward, overlap -> first pass."""
    naive = datetime.combine(day, time(hour, minute))
    # fold=0 picks the first pass of an overlap; for a nonexistent wall time it
    # applies the pre-transition offset, which lands the gap length later.
    return naive.replace(tzinfo=tz, fold=0).astimezone(UTC)


def next_due(spec: JobSpec, *, after: datetime, tz: ZoneInfo) -> datetime:
    """When ``spec`` is next due, counting from ``after`` (aware)."""
    if after.tzinfo is None or after.utcoffset() is None:
        raise JobSpecError("after must be timezone-aware")
    if spec.interval_seconds is not None:
        return after.astimezone(UTC) + timedelta(seconds=spec.interval_seconds)
    assert spec.cron is not None
    return CronSchedule(spec.cron).next_after(after, tz)


__all__ = ["CronSchedule", "next_due"]
