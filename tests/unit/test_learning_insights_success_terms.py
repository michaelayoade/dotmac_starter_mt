"""Configured policy and strict boundary canaries; hosted execution only."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from dotmac_learning import events, insights, success_queue
from dotmac_learning.insight_contracts import (
    CohortInsightTerms,
    GradeTrendTerms,
    LearnerInsightTerms,
)
from dotmac_learning.success_contracts import SuccessTerms

LEGACY = SuccessTerms(7, 60, 6, 2, 3, 7, 5, 2, 0.20, 0.5, 0.8)
NOW = datetime(2026, 10, 18, tzinfo=UTC)


@pytest.mark.parametrize(
    "field,value",
    [
        ("inactive_days", True),
        ("certificate_grace_hours", 1.5),
        ("inactivity_high_factor", float("inf")),
        ("inactivity_high_factor", 0.9),
        ("overdue_title_limit", -1),
        ("below_passing_min_weight_fraction", float("nan")),
        ("below_passing_high_grade_factor", -0.1),
        ("almost_complete_fraction", 1.1),
    ],
)
def test_success_terms_reject_invalid_policy_before_sweep_effects(field, value):
    db = MagicMock()
    with pytest.raises(ValueError):
        success_queue.sweep(
            db,
            tenant_id=uuid4(),
            terms=replace(LEGACY, **{field: value}),
            accounts=object(),
        )
    assert db.mock_calls == []


@pytest.mark.parametrize(
    "args",
    [(1, 2, 0.1), (4, 5, 0.1), (4, 1, 0.1), (4, 2, float("nan")), (True, 2, 0.1)],
)
def test_grade_policy_rejects_invalid_sample_and_fraction(args):
    with pytest.raises(ValueError):
        GradeTrendTerms(*args)


def test_terms_are_required_frozen_and_zero_values_are_preserved():
    with pytest.raises(TypeError):
        SuccessTerms()
    with pytest.raises(TypeError):
        GradeTrendTerms()
    zero = replace(
        LEGACY, inactive_days=0, overdue_title_limit=0, almost_complete_fraction=0
    )
    assert (
        zero.inactive_days
        == zero.overdue_title_limit
        == zero.almost_complete_fraction
        == 0
    )
    with pytest.raises(FrozenInstanceError):
        zero.inactive_days = 9
    assert LearnerInsightTerms(0, 0, 0, GradeTrendTerms(2, 2, 0)).chart_weeks == 0
    with pytest.raises(ValueError):
        CohortInsightTerms(0, 0)


def test_configured_trend_and_resume_threshold_preserve_exact_operators():
    terms = GradeTrendTerms(6, 2, 0.25)
    assert insights._trend_from((0, 0.25), terms).direction == "flat"
    assert insights._trend_from((0, 0.251), terms).direction == "up"
    assert insights._recommendation(None, 2, 3) == "continue_unfinished"
    assert insights._recommendation(None, 3, 3) == "resume_after_week"
    assert insights._recommendation("overdue", 100, 3) == "overdue"


def test_learner_passes_configured_windows_and_grade_terms(monkeypatch):
    terms = LearnerInsightTerms(3, 2, 11, GradeTrendTerms(6, 2, 0.25))
    calls = []
    monkeypatch.setattr(
        insights.learning_events,
        "weekly_buckets",
        lambda db, **kw: calls.append(kw) or [],
    )
    monkeypatch.setattr(
        insights.learning_events,
        "last_activity_at",
        lambda *args, **kw: NOW - timedelta(days=11),
    )
    monkeypatch.setattr(insights, "_current_blocker", lambda *args: None)
    trend = insights.GradeTrend((), "flat")
    monkeypatch.setattr(
        insights,
        "_grade_trend",
        lambda db, tenant, person, policy: trend
        if policy is terms.grade_trend
        else None,
    )
    monkeypatch.setattr(
        insights,
        "_discipline_strengths",
        lambda *args: insights.DisciplineStrengths(None, None, ()),
    )
    result = insights.learner_activity(
        object(), tenant_id=uuid4(), person_id=uuid4(), terms=terms, now=NOW
    )
    assert [call["weeks"] for call in calls] == [3, 2]
    assert result.grade_trend is trend
    assert result.recommendation == "resume_after_week"


def test_configured_inactivity_high_factor(monkeypatch):
    monkeypatch.setattr(
        success_queue.learning_events, "last_activity_at", lambda *args, **kw: None
    )
    terms = replace(LEGACY, inactive_days=3, inactivity_high_factor=3)
    common = {"tenant_id": uuid4(), "person_id": uuid4(), "now": NOW, "terms": terms}
    assert (
        success_queue._rule_inactivity(
            object(), enrolled_since=NOW - timedelta(days=2), **common
        )
        is None
    )
    assert (
        success_queue._rule_inactivity(
            object(), enrolled_since=NOW - timedelta(days=8), **common
        )[1]
        == "medium"
    )
    assert (
        success_queue._rule_inactivity(
            object(), enrolled_since=NOW - timedelta(days=9), **common
        )[1]
        == "high"
    )


def test_configured_overdue_criteria_title_cap_and_strict_lateness(monkeypatch):
    rows = [
        SimpleNamespace(
            due_at=NOW - timedelta(days=4), activity=SimpleNamespace(title=str(i))
        )
        for i in range(3)
    ]
    monkeypatch.setattr(
        success_queue.todo_service, "deadline_rows", lambda *args, **kw: rows
    )
    terms = replace(
        LEGACY, overdue_high_count=4, overdue_high_days=4, overdue_title_limit=1
    )
    common = {"tenant_id": uuid4(), "person_id": uuid4(), "now": NOW}
    hit = success_queue._rule_overdue(object(), terms=terms, **common)
    assert hit[0]["titles"] == ["0"] and hit[1] == "medium"
    assert (
        success_queue._rule_overdue(
            object(), terms=replace(terms, overdue_high_count=3), **common
        )[1]
        == "high"
    )
    hit = success_queue._rule_overdue(
        object(),
        terms=replace(terms, overdue_high_days=3, overdue_title_limit=0),
        **common,
    )
    assert hit[1] == "high" and hit[0]["titles"] == []


def test_configured_below_passing_volume_gates_and_odd_grade_floor(monkeypatch):
    grade = SimpleNamespace(pct=30, graded_count=3, graded_weight_fraction=0.4)
    monkeypatch.setattr(success_queue, "attempted_grade", lambda *args, **kw: grade)
    db = MagicMock()
    db.get.return_value = SimpleNamespace(title="Course")
    terms = replace(
        LEGACY,
        min_grade_pct=61,
        below_passing_min_graded=3,
        below_passing_min_weight_fraction=0.4,
    )
    common = {"tenant_id": uuid4(), "person_id": uuid4(), "course_ids": [uuid4()]}
    # 61*.5 floors to 30: 30 stays medium, matching legacy 61//2.
    assert success_queue._rule_below_passing(db, terms=terms, **common)[1] == "medium"
    grade.pct = 29
    assert success_queue._rule_below_passing(db, terms=terms, **common)[1] == "high"
    assert (
        success_queue._rule_below_passing(
            db, terms=replace(terms, below_passing_min_graded=4), **common
        )
        is None
    )
    assert (
        success_queue._rule_below_passing(
            db, terms=replace(terms, below_passing_min_weight_fraction=0.41), **common
        )
        is None
    )
    grade.pct = 35
    assert (
        success_queue._rule_below_passing(
            db, terms=replace(terms, below_passing_high_grade_factor=0.6), **common
        )[1]
        == "high"
    )


@pytest.mark.parametrize("weeks", [-1, True, 2.5])
def test_weekly_bucket_invalid_window_fails_before_queries(weeks):
    db = MagicMock()
    with pytest.raises(ValueError):
        events.weekly_buckets(
            db, tenant_id=uuid4(), person_id=uuid4(), weeks=weeks, now=NOW
        )
    assert db.mock_calls == []
