"""Source wall-clock boundary characterization; hosted execution only."""

from datetime import UTC, datetime, timedelta, timezone

from dotmac_learning.todo import _local_day_end


def test_day_end_uses_supplied_wall_clock_zone_and_truncates_microseconds():
    now = datetime(2026, 8, 1, 22, 30, 0, 123, tzinfo=UTC)
    assert _local_day_end(now, timezone(timedelta(hours=2))) == datetime(
        2026, 8, 2, 21, 59, 59, tzinfo=UTC
    )
    assert _local_day_end(now, timezone(timedelta(hours=-5))) == datetime(
        2026, 8, 2, 4, 59, 59, tzinfo=UTC
    )
