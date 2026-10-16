"""Hosted canaries for analytics primitive computation and Python time seams."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from dotmac_web_analytics import _native, projections
from dotmac_web_analytics.models import EventObservation


def _event(
    *,
    visitor: str = "a",
    at: int = 0,
    received: int = 0,
    identity: str = "01",
    code: str = "start",
    version: int = 1,
    key_version: int = 1,
    bucket: int = 0,
    route: str = "/",
) -> _native.Event:
    return (
        visitor,
        at,
        received,
        identity,
        code,
        version,
        key_version,
        bucket,
        [("route", route)],
    )


def test_session_timeout_is_inclusive_at_microsecond_precision() -> None:
    # Absolute values beyond float's exact microsecond range expose lossy epochs.
    base = 100_000_000_000_000_000
    events = [
        _event(at=base, identity="01"),
        _event(at=base + 60_000_000, identity="02"),
        _event(at=base + 120_000_001, identity="03"),
    ]
    visitors, sessions, metrics, funnels = _native.project_batch(
        events=events, inactivity_seconds=60, funnels=[]
    )
    assert visitors == [("a", 1, 0, 2)]
    assert sessions == [[0, 1], [2]]
    assert metrics == [(0, "route", "/", 3, 1, 2)]
    assert funnels == []


def test_shared_order_uses_visitor_time_receipt_then_uuid_string() -> None:
    events = [
        _event(visitor="z", identity="00"),
        _event(at=1, received=1, identity="02", code="finish"),
        _event(at=1, received=1, identity="01", code="start", key_version=3),
        _event(at=1, received=2, identity="00", code="finish"),
    ]
    visitors, sessions, _, counts = _native.project_batch(
        events=events,
        inactivity_seconds=60,
        funnels=[(1, [("start", 1), ("finish", 1)])],
    )
    assert visitors == [("a", 3, 2, 3), ("z", 1, 0, 0)]
    assert sessions == [[2, 1, 3], [0]]
    assert counts == [(2, [2, 1])]


@pytest.mark.parametrize(("delta", "completed"), [(1_000_000, 1), (1_000_001, 0)])
def test_funnel_window_is_inclusive_and_schema_specific(
    delta: int, completed: int
) -> None:
    events = [
        _event(identity="01"),
        _event(at=1, identity="02", code="finish", version=2),
        _event(at=delta, identity="03", code="finish"),
    ]
    *_, counts = _native.project_batch(
        events=events,
        inactivity_seconds=60,
        funnels=[(1, [("start", 1), ("finish", 1)])],
    )
    assert counts == [(1, [1, completed])]


def test_expired_funnel_does_not_restart_and_funnels_are_independent() -> None:
    events = [
        _event(),
        _event(at=2_000_000, identity="02", code="finish"),
        _event(at=3_000_000, identity="03"),
        _event(at=3_500_000, identity="04", code="finish"),
    ]
    *_, counts = _native.project_batch(
        events=events,
        inactivity_seconds=60,
        funnels=[
            (1, [("start", 1), ("finish", 1)]),
            (4, [("start", 1), ("finish", 1)]),
        ],
    )
    assert counts == [(1, [1, 0]), (1, [1, 1])]


def test_metric_cells_count_distinct_visitors_sessions_per_bucket() -> None:
    events = [
        _event(identity="01"),
        _event(identity="02", at=1),
        _event(visitor="b", identity="03"),
        _event(identity="04", at=61_000_002, bucket=1),
    ]
    _, sessions, metrics, _ = _native.project_batch(
        events=events, inactivity_seconds=60, funnels=[]
    )
    assert sessions == [[0, 1], [3], [2]]
    assert metrics == [(0, "route", "/", 3, 2, 2), (1, "route", "/", 1, 1, 1)]


def test_empty_batch_and_invalid_empty_funnel() -> None:
    assert _native.project_batch(events=[], inactivity_seconds=60, funnels=[]) == (
        [],
        [],
        [],
        [],
    )
    with pytest.raises(ValueError, match="steps"):
        _native.project_batch(events=[], inactivity_seconds=60, funnels=[(1, [])])


def test_python_snapshot_uses_exact_utc_microseconds_and_dst_buckets() -> None:
    before = datetime(2026, 3, 8, 6, 59, 59, 999999, tzinfo=UTC)
    after = before + timedelta(microseconds=1)
    observations = tuple(
        EventObservation(
            id=UUID(int=i + 1),
            visitor_digest="a",
            occurred_at=at,
            received_at=at,
            event_code="start",
            event_schema_version=1,
            pseudonym_key_version=1,
            device_class="desktop",
        )
        for i, at in enumerate((before, after))
    )
    events, buckets = projections._native_snapshot(observations, "America/New_York")
    assert events[1][1] - events[0][1] == 1
    assert events[0][7] == events[1][7]
    assert list(buckets.values()) == [datetime(2026, 3, 8, 5, tzinfo=UTC)]
    assert events[0][8] == [
        ("route", "(none)"),
        ("source", "(direct)"),
        ("campaign_marker", "(none)"),
        ("device", "desktop"),
        ("event", "start"),
    ]
