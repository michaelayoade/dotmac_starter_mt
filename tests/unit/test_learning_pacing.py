"""Source pacing decision boundaries without a database or product adapter."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from dotmac_learning import pacing
from dotmac_learning.delivery_models import OfferingActivity
from dotmac_learning.pacing_contracts import PacingFact, PacingForbidden

CLOCK = datetime(2001, 1, 1, tzinfo=UTC)
SCOPE = {
    "tenant_id": uuid4(),
    "person_id": uuid4(),
    "course_id": uuid4(),
    "activity_id": uuid4(),
}


@pytest.mark.parametrize(
    "windows,expected",
    [
        ([], PacingFact(True, True, False)),
        ([(None, None)], PacingFact(True, True, True)),
        ([(CLOCK, CLOCK)], PacingFact(True, True, True)),
        ([(CLOCK + timedelta(microseconds=1), None)], PacingFact(False, False, True)),
        ([(None, CLOCK - timedelta(microseconds=1))], PacingFact(True, False, True)),
        (
            [(CLOCK + timedelta(days=1), None), (None, CLOCK - timedelta(days=1))],
            PacingFact(True, False, True),
        ),
        (
            [(None, CLOCK - timedelta(days=1)), (CLOCK, CLOCK)],
            PacingFact(True, True, True),
        ),
        (
            [(CLOCK + timedelta(days=1), CLOCK - timedelta(days=1))],
            PacingFact(False, False, True),
        ),
    ],
)
def test_source_or_nullable_inclusive_and_inverted_windows(
    monkeypatch, windows, expected
):
    rows = [
        OfferingActivity(release_at=release, due_at=due) for release, due in windows
    ]
    monkeypatch.setattr(pacing, "_applicable_overrides", lambda *args, **kwargs: rows)
    assert pacing.activity_pacing(Mock(), **SCOPE, now=CLOCK) == expected


def test_fact_is_frozen_and_contains_no_orm_capability():
    fact = PacingFact(True, False, True)
    with pytest.raises(FrozenInstanceError):
        fact.readable = False
    for name in ("tenant_id", "offering_id", "_sa_instance_state", "commit", "execute"):
        assert not hasattr(fact, name)


def test_default_clock_is_utc_and_sampled_before_query(monkeypatch):
    calls = []

    class Clock:
        @staticmethod
        def now(zone):
            assert zone is UTC
            calls.append("clock")
            return CLOCK

    def overrides(*args, **kwargs):
        calls.append("query")
        return [OfferingActivity(release_at=CLOCK, due_at=CLOCK)]

    monkeypatch.setattr(pacing, "datetime", Clock)
    monkeypatch.setattr(pacing, "_applicable_overrides", overrides)
    assert pacing.activity_pacing(Mock(), **SCOPE) == PacingFact(True, True, True)
    assert calls == ["clock", "query"]


@pytest.mark.parametrize(
    "function,fact,detail",
    [
        (
            pacing.require_activity_readable,
            PacingFact(False, False, True),
            "Activity not yet released",
        ),
        (
            pacing.require_activity_submittable,
            PacingFact(True, False, True),
            "Activity is not open for submission (not released or past due)",
        ),
    ],
)
def test_enforcement_preserves_forbidden_details(monkeypatch, function, fact, detail):
    monkeypatch.setattr(pacing, "activity_pacing", lambda *args, **kwargs: fact)
    with pytest.raises(PacingForbidden) as caught:
        function(Mock(), **SCOPE, now=CLOCK)
    assert isinstance(caught.value, PermissionError)
    assert str(caught.value) == detail


def test_enforcement_success_returns_none_and_query_failures_propagate(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(
            pacing,
            "activity_pacing",
            lambda *args, **kwargs: PacingFact(True, True, False),
        )
        assert pacing.require_activity_readable(Mock(), **SCOPE) is None
        assert pacing.require_activity_submittable(Mock(), **SCOPE) is None
    failure = RuntimeError("query failed")

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(pacing, "_applicable_overrides", fail)
    with pytest.raises(RuntimeError) as caught:
        pacing.activity_pacing(Mock(), **SCOPE)
    assert caught.value is failure
