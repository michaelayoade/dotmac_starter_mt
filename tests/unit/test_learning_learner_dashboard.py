"""Source dashboard pure resume, trend and strict-deadline characterizations."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from dotmac_learning.curriculum_contracts import ChapterFact
from dotmac_learning.learner_dashboard import _next_deadline_from, _resume_from, _trend
from dotmac_learning.progress_contracts import ScoreFact
from dotmac_learning.references import ActivityFact

NOW = datetime(2026, 8, 1, tzinfo=UTC)


def chapter(number):
    return ChapterFact(
        uuid4(), uuid4(), uuid4(), number, str(number), "", "", "", "", number
    )


def activity(number):
    return ActivityFact(
        uuid4(),
        uuid4(),
        uuid4(),
        "A",
        number,
        "mcq_test",
        None,
        0.7,
        1,
        "graded",
        "auto",
        1,
        None,
        NOW,
        NOW,
    )


def passed():
    return ScoreFact(
        uuid4(), uuid4(), uuid4(), 1, 1, 1, True, [], "auto", None, NOW, NOW
    )


def test_resume_checks_all_activities_and_reads_wraps_after_touch():
    chapters = [chapter(i) for i in (1, 2, 3)]
    first, second = activity(1), activity(1)
    assert (
        _resume_from(chapters, [first, second], {first.id: passed()}, {1, 2}, None)
        == chapters[0]
    )
    assert (
        _resume_from(chapters, [first, second], {first.id: passed()}, {1, 2}, (NOW, 2))
        == chapters[2]
    )
    assert (
        _resume_from(
            chapters, [first, second], {first.id: passed()}, {1, 2, 3}, (NOW, 2)
        )
        == chapters[0]
    )
    assert _resume_from(chapters, [], {}, {1, 2, 3}, None) is None
    assert _resume_from([], [], {}, set(), None) is None


def test_trend_half_split_strict_three_point_threshold_and_short_flat():
    assert _trend([0, 100]) == "flat"
    assert _trend([0, 3, 3]) == "flat"
    assert _trend([0, 3, 4]) == "up"
    assert _trend([4, 0, 1]) == "down"
    assert _trend([4, 1, 1]) == "flat"


def test_next_deadline_excludes_now_and_keeps_passed_activity_due_candidates():
    later = NOW + timedelta(days=2)
    assert _next_deadline_from([(uuid4(), NOW), (uuid4(), None)], NOW, NOW) is None
    assert _next_deadline_from(
        [(uuid4(), later)], NOW + timedelta(days=1), NOW
    ) == NOW + timedelta(days=1)
    assert _next_deadline_from([(uuid4(), later)], None, NOW) == later
