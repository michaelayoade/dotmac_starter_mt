"""Characterize source insight thresholds, weighting and recommendation codes."""

from dataclasses import FrozenInstanceError

import pytest
from dotmac_learning.insights import _discipline_summary, _recommendation, _trend_from


def test_trend_minimum_strict_threshold_split_and_rounding():
    assert _trend_from((0, 1, 1)).direction == "flat"
    assert _trend_from((0, 0, 0.05, 0.05)).direction == "flat"
    assert _trend_from((0, 0, 0.051, 0.051)).direction == "up"
    assert _trend_from((0.8, 0.8, 0.2, 0.2, 0.2)).direction == "down"
    assert _trend_from((0.125,)).points == (12,)
    with pytest.raises(FrozenInstanceError):
        _trend_from(()).direction = "up"


def test_discipline_equal_course_weight_round_before_first_tie_selection():
    summary = _discipline_summary({"first": [0.5, 1], "second": [0.75], "third": [0.2]})
    assert summary.strong == "first" and summary.weak == "third"
    assert tuple((r.discipline, r.pct) for r in summary.all) == (
        ("first", 75),
        ("second", 75),
        ("third", 20),
    )
    one = _discipline_summary({"only": [0.5]})
    assert one.strong == "only" and one.weak is None
    empty = _discipline_summary({})
    assert empty.strong is empty.weak is None and empty.all == ()


def test_recommendation_priority_and_exact_seven_day_boundary():
    assert _recommendation("overdue", None) == "overdue"
    assert _recommendation("attempts_exhausted", 20) == "attempts_exhausted"
    assert _recommendation(None, None) == "start_first_chapter"
    assert _recommendation(None, 7) == "resume_after_week"
    assert _recommendation(None, 6) == "continue_unfinished"
