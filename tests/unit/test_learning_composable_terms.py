"""S3 composable terms: validation, immutability and default drift goldens."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from dotmac_learning import attempt_policy
from dotmac_learning.assessment_reads import FeedbackRevealTerms, reveal_feedback
from dotmac_learning.insight_contracts import CohortInsightTerms, ProgressTrendTerms
from dotmac_learning.learner_dashboard import _trend
from dotmac_learning.success_contracts import SuccessTerms

DEFAULT_REVEAL = {
    "practice": "immediate",
    "graded": "on_pass_or_exhausted",
    "exam": "never",
}
FEEDBACK = FeedbackRevealTerms(DEFAULT_REVEAL)
TREND = ProgressTrendTerms(8, 3, 0.03)
ALL_RULES = (
    "inactivity",
    "overdue_work",
    "below_passing",
    "failed_final",
    "certificate_blocked",
)
DEFAULT_SEVERITY = {"failed_final": "high", "certificate_blocked": "medium"}


def _activity(mode, max_attempts=1):
    return SimpleNamespace(
        assessment_mode=mode,
        max_attempts=max_attempts,
        min_attempt_interval_seconds=0,
    )


def _legacy_reveal(activity, *, passed, attempts_used, attempts_granted=0):
    if activity.assessment_mode == "practice":
        return True
    if activity.assessment_mode == "exam":
        return False
    exhausted = attempt_policy.entitlement(
        activity, used=attempts_used, granted=attempts_granted
    ).exhausted
    return passed or exhausted


@pytest.mark.parametrize("mode", ["practice", "graded", "exam", "other"])
@pytest.mark.parametrize("passed", [False, True])
@pytest.mark.parametrize(
    "maximum,used,granted", [(None, 99, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1), (2, 1, 0)]
)
def test_reveal_truth_table_identical_at_default_map(
    mode, passed, maximum, used, granted
):
    act = _activity(mode, maximum)
    assert reveal_feedback(
        act,
        terms=FEEDBACK,
        passed=passed,
        attempts_used=used,
        attempts_granted=granted,
    ) == _legacy_reveal(
        act, passed=passed, attempts_used=used, attempts_granted=granted
    )


def test_reveal_custom_map_changes_only_mapped_mode():
    custom = FeedbackRevealTerms({**DEFAULT_REVEAL, "practice": "never"})
    assert (
        reveal_feedback(
            _activity("practice"), terms=custom, passed=True, attempts_used=0
        )
        is False
    )
    assert (
        reveal_feedback(_activity("graded"), terms=custom, passed=True, attempts_used=0)
        is True
    )
    assert (
        reveal_feedback(_activity("exam"), terms=custom, passed=True, attempts_used=1)
        is False
    )


@pytest.mark.parametrize(
    "mapping",
    [
        {"practice": "immediate", "graded": "never"},
        {**DEFAULT_REVEAL, "quiz": "never"},
        {**DEFAULT_REVEAL, "graded": "sometimes"},
        {**DEFAULT_REVEAL, "exam": "immediate"},
        "not-a-mapping",
    ],
)
def test_feedback_reveal_terms_fail_closed(mapping):
    with pytest.raises(ValueError):
        FeedbackRevealTerms(mapping)


def test_feedback_reveal_terms_copy_is_immutable_and_frozen():
    source = dict(DEFAULT_REVEAL)
    terms = FeedbackRevealTerms(source)
    source["exam"] = "immediate"
    assert terms.reveal_by_mode["exam"] == "never"
    with pytest.raises(TypeError):
        terms.reveal_by_mode["exam"] = "immediate"
    with pytest.raises(FrozenInstanceError):
        terms.reveal_by_mode = DEFAULT_REVEAL


def _legacy_trend(points):
    if len(points) < 3:
        return "flat"
    half = len(points) // 2
    earlier = sum(points[:half]) / half
    later = sum(points[half:]) / (len(points) - half)
    if later - earlier > 3:
        return "up"
    if earlier - later > 3:
        return "down"
    return "flat"


@pytest.mark.parametrize(
    "points",
    [
        [],
        [50],
        [0, 100],
        [0, 3, 3],
        [0, 3, 4],
        [4, 0, 1],
        [100, 20, 30],
        [10, 20, 30, 40, 50, 60, 70, 80],
        [80, 70, 60, 50, 40, 30, 20, 10],
        [50, 50, 53, 53],
        [50, 50, 54, 54],
        [0, 0, 0, 7],
        [33, 66, 99, 0, 50],
    ],
)
def test_progress_trend_identical_to_legacy_at_defaults(points):
    assert _trend(points, TREND) == _legacy_trend(points)


@pytest.mark.parametrize(
    "args",
    [(1, 2, 0.1), (4, 5, 0.1), (4, 1, 0.1), (4, 2, float("nan")), (True, 2, 0.1)],
)
def test_progress_trend_terms_mirror_grade_trend_validation(args):
    with pytest.raises(ValueError):
        ProgressTrendTerms(*args)


@pytest.mark.parametrize("fraction", [-0.1, 1.1, float("nan"), True])
def test_cohort_half_fraction_is_required_and_bounded(fraction):
    with pytest.raises(ValueError):
        CohortInsightTerms(7, 4, fraction)
    with pytest.raises(TypeError):
        CohortInsightTerms(7, 4)


def _success(**overrides):
    values = {
        "inactive_days": 7,
        "min_grade_pct": 60,
        "certificate_grace_hours": 6,
        "inactivity_high_factor": 2,
        "overdue_high_count": 3,
        "overdue_high_days": 7,
        "overdue_title_limit": 5,
        "below_passing_min_graded": 2,
        "below_passing_min_weight_fraction": 0.20,
        "below_passing_high_grade_factor": 0.5,
        "almost_complete_fraction": 0.8,
        "enabled_rules": ALL_RULES,
        "severity_by_rule": dict(DEFAULT_SEVERITY),
    }
    values.update(overrides)
    return SuccessTerms(**values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"enabled_rules": ("inactivity", "unknown_rule")},
        {"enabled_rules": ("inactivity", "inactivity")},
        {"enabled_rules": ["inactivity"]},
        {"severity_by_rule": {"unknown_rule": "high"}},
        {"severity_by_rule": {"inactivity": "critical"}},
        {"severity_by_rule": "not-a-mapping"},
    ],
)
def test_success_terms_reject_invalid_rule_policy(overrides):
    with pytest.raises(ValueError):
        _success(**overrides)


def test_success_terms_rule_policy_is_copied_immutable():
    severity = dict(DEFAULT_SEVERITY)
    terms = _success(severity_by_rule=severity)
    severity["failed_final"] = "low"
    assert terms.severity_by_rule["failed_final"] == "high"
    with pytest.raises(TypeError):
        terms.severity_by_rule["failed_final"] = "low"
    assert terms.enabled_rules == ALL_RULES
    assert _success(enabled_rules=()).enabled_rules == ()
