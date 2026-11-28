"""Pure next-due computation for declared jobs (no database, no clock)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from dotmac_durable_timers.cadence import CronSchedule, next_due
from dotmac_kernel.jobs import JobSpec, JobSpecError

NEW_YORK = ZoneInfo("America/New_York")
UTC_TZ = ZoneInfo("UTC")


def _spec(**kwargs: object) -> JobSpec:
    return JobSpec(
        code="job",
        handler="dotmac_durable_timers.builtin:purge_timer_history",
        plane="tenant",
        **kwargs,  # type: ignore[arg-type]
    )


def test_interval_counts_from_the_completion_instant() -> None:
    after = datetime(2029, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert next_due(_spec(interval_seconds=90), after=after, tz=UTC_TZ) == datetime(
        2029, 1, 2, 3, 5, 35, tzinfo=UTC
    )


def test_cron_is_strictly_after_and_never_catches_up() -> None:
    cron = CronSchedule("0 * * * *")
    exactly = datetime(2029, 1, 2, 3, 0, tzinfo=UTC)
    assert cron.next_after(exactly, UTC_TZ) == datetime(2029, 1, 2, 4, 0, tzinfo=UTC)
    # Hours late: the missed ticks are skipped, only the next one is returned.
    late = datetime(2029, 1, 2, 9, 30, tzinfo=UTC)
    assert cron.next_after(late, UTC_TZ) == datetime(2029, 1, 2, 10, 0, tzinfo=UTC)


def test_cron_steps_ranges_lists_and_sunday_as_seven() -> None:
    cron = CronSchedule("*/15 9-10 * * 7")  # Sundays 09:00-10:45 every 15 minutes
    after = datetime(2029, 1, 2, 12, 0, tzinfo=UTC)  # a Tuesday
    first = cron.next_after(after, UTC_TZ)
    assert first == datetime(2029, 1, 7, 9, 0, tzinfo=UTC)
    assert cron.next_after(first, UTC_TZ) == datetime(2029, 1, 7, 9, 15, tzinfo=UTC)


def test_day_of_month_and_day_of_week_combine_with_or_when_both_restricted() -> None:
    cron = CronSchedule("0 0 13 * 5")  # the 13th OR any Friday
    after = datetime(2029, 1, 1, 0, 0, tzinfo=UTC)  # Monday
    assert cron.next_after(after, UTC_TZ) == datetime(2029, 1, 5, 0, 0, tzinfo=UTC)


def test_the_tenant_timezone_moves_the_instant() -> None:
    cron = CronSchedule("30 2 * * *")
    after = datetime(2029, 1, 10, 0, 0, tzinfo=UTC)
    assert cron.next_after(after, NEW_YORK) == datetime(2029, 1, 10, 7, 30, tzinfo=UTC)
    summer = datetime(2029, 7, 10, 0, 0, tzinfo=UTC)
    assert cron.next_after(summer, NEW_YORK) == datetime(2029, 7, 10, 6, 30, tzinfo=UTC)


def test_a_nonexistent_spring_forward_time_runs_once_shifted() -> None:
    cron = CronSchedule("30 2 * * *")
    after = datetime(2029, 3, 11, 6, 0, tzinfo=UTC)  # 01:00 EST, DST begins at 02:00
    due = cron.next_after(after, NEW_YORK)
    assert due == datetime(2029, 3, 11, 7, 30, tzinfo=UTC)  # 03:30 EDT
    assert cron.next_after(due, NEW_YORK) == datetime(2029, 3, 12, 6, 30, tzinfo=UTC)


def test_an_ambiguous_fall_back_time_runs_only_at_its_first_occurrence() -> None:
    cron = CronSchedule("30 1 * * *")
    after = datetime(2029, 11, 4, 4, 0, tzinfo=UTC)  # 00:00 EDT
    first = cron.next_after(after, NEW_YORK)
    assert first == datetime(2029, 11, 4, 5, 30, tzinfo=UTC)  # 01:30 EDT
    # 06:30 UTC is the repeated 01:30 EST; it must not fire again.
    assert cron.next_after(first, NEW_YORK) == datetime(2029, 11, 5, 6, 30, tzinfo=UTC)
    repeated = datetime(2029, 11, 4, 6, 0, tzinfo=UTC)  # 01:00 EST, second pass
    assert cron.next_after(repeated, NEW_YORK) == datetime(
        2029, 11, 5, 6, 30, tzinfo=UTC
    )


def test_an_expression_that_never_matches_fails_closed() -> None:
    with pytest.raises(JobSpecError):
        CronSchedule("0 0 31 2 *").next_after(datetime(2029, 1, 1, tzinfo=UTC), UTC_TZ)


def test_a_naive_instant_is_refused() -> None:
    with pytest.raises(JobSpecError):
        next_due(_spec(interval_seconds=60), after=datetime(2029, 1, 1), tz=UTC_TZ)


def test_a_wildcard_hour_expression_runs_in_both_passes_of_the_repeated_hour() -> None:
    cron = CronSchedule("*/15 * * * *")
    # 05:50Z is 01:50 EDT. The 01:00-01:59 wall hour then repeats as EST, so the
    # next quarter hour is the SECOND pass of 01:00 (06:00Z), not 02:00 EST.
    after = datetime(2029, 11, 4, 5, 50, tzinfo=UTC)
    assert cron.next_after(after, NEW_YORK) == datetime(2029, 11, 4, 6, 0, tzinfo=UTC)
    # No hole: every quarter hour from 05:45Z to 07:00Z fires exactly once.
    seen = []
    cursor = datetime(2029, 11, 4, 5, 40, tzinfo=UTC)
    while True:
        cursor = cron.next_after(cursor, NEW_YORK)
        if cursor > datetime(2029, 11, 4, 7, 0, tzinfo=UTC):
            break
        seen.append(cursor)
    assert [t.strftime("%H:%M") for t in seen] == [
        "05:45",
        "06:00",
        "06:15",
        "06:30",
        "06:45",
        "07:00",
    ]
