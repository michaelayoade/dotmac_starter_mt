"""Source arithmetic and strict-tie characterization; hosted execution only."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning.gradebook import attempted_from, grade_from
from dotmac_learning.progress_contracts import ScoreFact
from dotmac_learning.references import ActivityFact
from dotmac_learning.reports import _fraction_winner


def activity(weight=1.0):
    return ActivityFact(
        uuid4(),
        uuid4(),
        uuid4(),
        "A",
        None,
        "mcq_test",
        None,
        0.7,
        None,
        "graded",
        "auto",
        weight,
        None,
        datetime.now(UTC),
        datetime.now(UTC),
    )


def score(fraction, *, passed=False, source="auto"):
    now = datetime.now(UTC)
    return ScoreFact(
        uuid4(),
        uuid4(),
        uuid4(),
        fraction,
        1,
        fraction,
        passed,
        [],
        source,
        None,
        now,
        now,
    )


def test_missing_weight_real_zero_and_attempted_volume():
    done, missing, zero = activity(2), activity(6), activity(2)
    grade = grade_from((done, missing, zero), {done.id: score(1), zero.id: score(0)})
    assert grade.pct == 20
    assert tuple(c.graded for c in grade.per_activity) == (True, False, True)
    assert tuple(c.fraction for c in grade.per_activity) == (1, 0, 0)
    attempted = attempted_from(grade)
    assert (
        attempted.pct,
        attempted.graded_count,
        attempted.graded_weight_fraction,
    ) == (50, 2, 0.4)
    with pytest.raises(FrozenInstanceError):
        grade.pct = 99
    assert isinstance(grade.per_activity, tuple)


def test_empty_zero_weight_and_python_rounding():
    assert grade_from((), {}).pct == 0
    unweighted = activity(0)
    grade = grade_from((unweighted,), {unweighted.id: score(1)})
    assert grade.pct == 0 and grade.per_activity[0].graded
    assert attempted_from(grade).graded_count == 1
    assert attempted_from(grade).graded_weight_fraction == 0
    a = activity()
    assert grade_from((a,), {a.id: score(0.125)}).pct == 12


def test_transcript_strict_fraction_retains_first_tie():
    first = score(0.5)
    tied_override = score(0.5, passed=True, source="override")
    better = score(0.6)
    assert _fraction_winner(first, tied_override) is first
    assert _fraction_winner(first, better) is better
    assert _fraction_winner(None, first) is first
