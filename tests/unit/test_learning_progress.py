"""Pure source-preserving score-of-record examples."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from dotmac_learning.progress_contracts import ScoreFact
from dotmac_learning.projections import best_rank


def _score() -> ScoreFact:
    return ScoreFact(
        id=uuid4(),
        tenant_id=uuid4(),
        submission_id=uuid4(),
        score=1,
        max_score=3,
        fraction=1 / 3,
        passed=False,
        per_item=[],
        source="auto",
        override_reason=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_fraction_precedes_pass_override_and_recency():
    lower = replace(_score(), fraction=0.5, passed=True, source="override")
    higher = replace(
        lower,
        fraction=0.6,
        passed=False,
        source="auto",
        created_at=lower.created_at - timedelta(days=1),
    )
    assert best_rank(higher) > best_rank(lower)


def test_equal_fraction_pass_then_override_then_recency():
    auto = _score()
    passing = replace(auto, passed=True)
    override = replace(passing, source="override")
    latest = replace(override, created_at=override.created_at + timedelta(seconds=1))
    assert (
        best_rank(auto) < best_rank(passing) < best_rank(override) < best_rank(latest)
    )
    assert best_rank(replace(auto, id=uuid4())) == best_rank(auto)
    assert auto.fraction == 1 / 3
