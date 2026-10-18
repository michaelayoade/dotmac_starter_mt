"""Typed event commands and optional-failure reporting (hosted CI only)."""

import json
from datetime import (
    UTC,
    datetime,
    timedelta,
    timezone,
)
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from dotmac_learning.event_contracts import EventWrite
from dotmac_learning.events import _local_day_start_utc, emit, record
from dotmac_learning.models import KIND_ACTIVITY_STARTED, KIND_COURSE_VIEWED
from pydantic import ValidationError


def test_event_write_preserves_json_nulls_and_rejects_non_json_detail() -> None:
    payload = EventWrite(
        tenant_id=uuid4(),
        person_id=uuid4(),
        kind=KIND_ACTIVITY_STARTED,
        detail={"nested": [None, {"passed": True, "score": 2.5}]},
    )
    assert payload.detail == {"nested": [None, {"passed": True, "score": 2.5}]}
    assert payload.subject_id is None and payload.occurred_at is None
    with pytest.raises(ValidationError):
        EventWrite(
            tenant_id=uuid4(),
            person_id=uuid4(),
            kind=KIND_ACTIVITY_STARTED,
            detail={"unsupported": object()},
        )


def test_daily_zone_is_explicit_and_boundary_uses_injected_zone() -> None:
    db = MagicMock()
    payload = EventWrite(tenant_id=uuid4(), person_id=uuid4(), kind=KIND_COURSE_VIEWED)
    with pytest.raises(ValueError, match="local_day_zone"):
        record(db, payload)
    db.execute.assert_not_called()
    now = datetime(2026, 10, 17, 22, 30, tzinfo=UTC)
    assert _local_day_start_utc(now, timezone(timedelta(hours=3))) == datetime(
        2026, 10, 17, 21, tzinfo=UTC
    )
    assert _local_day_start_utc(now, timezone(timedelta(hours=-4))) == datetime(
        2026, 10, 17, 4, tzinfo=UTC
    )


def test_required_record_propagates_and_optional_emit_reports_failure() -> None:
    db = MagicMock()
    payload = EventWrite(tenant_id=uuid4(), person_id=uuid4(), kind="unknown")
    with pytest.raises(ValueError):
        record(db, payload)
    outcome = emit(db, payload)
    assert outcome.status == "failed" and outcome.event is None
    assert "unknown" not in repr(outcome)
    db.begin_nested.assert_called_once_with()


def test_emit_catches_after_savepoint_exit_and_reports_suppression() -> None:
    db = MagicMock()
    payload = EventWrite(
        tenant_id=uuid4(), person_id=uuid4(), kind=KIND_ACTIVITY_STARTED
    )
    with patch("dotmac_learning.events.record", return_value=None):
        assert emit(db, payload).status == "suppressed"
    db.begin_nested.return_value.__exit__.side_effect = RuntimeError("private data")
    with patch("dotmac_learning.events.record", return_value=None):
        outcome = emit(db, payload)
    assert outcome.status == "failed" and "private data" not in repr(outcome)


def test_emit_propagates_required_pending_flush_failure() -> None:
    db = MagicMock()
    db.flush.side_effect = RuntimeError("required host state failed")
    payload = EventWrite(
        tenant_id=uuid4(), person_id=uuid4(), kind=KIND_ACTIVITY_STARTED
    )
    with pytest.raises(RuntimeError, match="required host state failed"):
        emit(db, payload)
    db.begin_nested.assert_not_called()


@pytest.mark.parametrize("detail", [None, "legacy", 7, [None, {"legacy": True}]])
def test_event_fact_preserves_historical_json_shapes(detail) -> None:
    from dotmac_learning.events import _fact

    event = MagicMock(detail=detail)
    fact = _fact(event)
    assert fact.detail == detail
    if isinstance(detail, list):
        assert fact.detail is not detail
        assert fact.detail[1] is not detail[1]


@pytest.mark.parametrize(
    "detail",
    [
        None,
        False,
        True,
        0,
        7,
        0.0,
        2.5,
        "",
        "legacy",
        [],
        [None, False, 0, 2.5, "legacy", {"nested": [True]}],
        {},
        {"nested": [None, False, 0, 2.5, "", {"passed": True}]},
    ],
)
def test_event_write_accepts_json_values_without_type_loss(detail) -> None:
    payload = EventWrite(
        tenant_id=uuid4(), person_id=uuid4(), kind=KIND_ACTIVITY_STARTED, detail=detail
    )
    assert type(payload.detail) is type(detail)
    # Serialized equality also distinguishes nested booleans, integers and floats.
    assert json.dumps(payload.detail, sort_keys=True) == json.dumps(
        detail, sort_keys=True
    )


def test_event_write_omitted_detail_defaults_to_independent_objects() -> None:
    first = EventWrite(tenant_id=uuid4(), person_id=uuid4(), kind=KIND_ACTIVITY_STARTED)
    second = EventWrite(
        tenant_id=uuid4(), person_id=uuid4(), kind=KIND_ACTIVITY_STARTED
    )
    assert first.detail == second.detail == {}
    assert first.detail is not second.detail
