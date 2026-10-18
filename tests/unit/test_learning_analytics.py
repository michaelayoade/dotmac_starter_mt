"""Source item denominators, truthiness, shape failures and ordered facts."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning.analytics import _analysis_from
from dotmac_learning.progress_contracts import ScoreFact


def score(items):
    now = datetime.now(UTC)
    return ScoreFact(
        uuid4(), uuid4(), uuid4(), 1, 1, 1, True, items, "auto", None, now, now
    )


def test_first_seen_order_question_denominators_duplicate_ids_and_truthiness():
    result = _analysis_from(
        (
            score(
                [
                    {"id": "z", "correct": True},
                    {"id": "a", "correct": False},
                    {"id": "z", "correct": "truthy"},
                    {"correct": True},
                    {"id": None, "correct": True},
                ]
            ),
            score([{"id": "a", "correct": 1}, {"id": "b"}]),
            score(None),
        )
    )
    assert tuple(r.id for r in result) == ("z", "a", "b")
    assert tuple((r.responses, r.correct, r.p_value) for r in result) == (
        (2, 2, 1),
        (2, 1, 0.5),
        (1, 0, 0),
    )
    assert isinstance(result, tuple)
    with pytest.raises(FrozenInstanceError):
        result[0].responses = 0
    assert not hasattr(result[0], "_sa_instance_state")


def test_empty_payloads_and_ids_are_not_normalized():
    assert _analysis_from(()) == ()
    assert _analysis_from((score([]), score(None))) == ()
    # Source only excludes None IDs, and JSON scalars stay uncoerced.
    result = _analysis_from((score([{"id": "", "correct": 0}, {"id": 7}]),))
    assert tuple(r.id for r in result) == ("", 7)


@pytest.mark.parametrize("items", [["bad"], "bad", {"id": "bad"}])
def test_historical_invalid_item_shapes_propagate_source_error(items):
    with pytest.raises(AttributeError):
        _analysis_from((score(items),))
