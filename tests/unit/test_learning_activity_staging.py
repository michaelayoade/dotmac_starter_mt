"""Owner command and projection-free surface canaries; no database substitute."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_learning import (
    ActivityCreate,
    create_activity_identity,
    references,
    stage_activity_patch,
)
from dotmac_learning.references import ActivityPatch, ActivityWrite
from pydantic import ValidationError


def _values():
    return {
        "tenant_id": uuid4(),
        "course_id": uuid4(),
        "title": "Lab",
        "type": "lab",
        "pass_threshold": 0.7,
        "assessment_mode": "graded",
        "grading": "auto",
        "weight": 1,
    }


def test_create_has_write_contract_without_caller_identity():
    values = _values()
    payload = ActivityCreate(**values)
    assert payload.model_dump() == ActivityWrite(id=uuid4(), **values).model_dump(
        exclude={"id"}
    )
    for name in ("type", "pass_threshold", "assessment_mode", "grading", "weight"):
        with pytest.raises(ValidationError):
            ActivityCreate(
                **{key: value for key, value in values.items() if key != name}
            )
    for name in ("id", "created_at", "updated_at", "unknown"):
        with pytest.raises(ValidationError):
            ActivityCreate(**values, **{name: uuid4()})
    payload.title = "Mutable"
    assert payload.title == "Mutable"


@pytest.mark.parametrize(
    "threshold", [0, -1, 2, "0.4", True, float("inf"), float("-inf"), float("nan")]
)
def test_create_keeps_plain_float_coercion_and_no_range_policy(threshold):
    values = _values() | {"pass_threshold": threshold, "weight": 0}
    payload = ActivityCreate(**values)
    expected = ActivityWrite(id=uuid4(), **values)
    assert repr(payload.pass_threshold) == repr(expected.pass_threshold)
    assert payload.weight == expected.weight == 0.0


def test_create_nullable_absence_only_and_nonnullable_null_rejection():
    payload = ActivityCreate(**_values())
    assert all(
        payload.model_dump()[name] is None
        for name in ("chapter_number", "bank_id", "max_attempts", "question_count")
    )
    for name in (
        "tenant_id",
        "course_id",
        "title",
        "type",
        "pass_threshold",
        "assessment_mode",
        "grading",
        "weight",
    ):
        with pytest.raises(ValidationError):
            ActivityCreate(**(_values() | {name: None}))


def test_identity_reads_only_id_after_entire_session_flush(monkeypatch):
    events = []
    identity = uuid4()

    class IdentityOnly:
        @property
        def id(self):
            assert events[-1] == "flush"
            return identity

        def __getattr__(self, name):
            raise AssertionError(f"unused Activity field read: {name}")

    row = IdentityOnly()
    db = SimpleNamespace(flush=lambda: events.append("flush"))

    def add(session, values):
        assert session is db and "id" not in values
        events.append("add")
        return row

    monkeypatch.setattr(references, "_add_activity", add)
    monkeypatch.setattr(
        references, "_activity_fact", lambda row: pytest.fail("fact projection")
    )
    assert create_activity_identity(db, ActivityCreate(**_values())) == identity
    assert events == ["add", "flush"]


def test_stage_missing_and_existing_use_one_select_no_fact_flush_or_refresh(
    monkeypatch,
):
    calls = []
    fields = []

    class AssignmentOnly:
        def __setattr__(self, name, value):
            fields.append((name, value))

        def __getattr__(self, name):
            raise AssertionError(f"unused Activity field read: {name}")

    row = AssignmentOnly()
    db = SimpleNamespace(scalar=lambda stmt: calls.append(stmt) or row)
    monkeypatch.setattr(
        references, "_activity_fact", lambda row: pytest.fail("fact projection")
    )
    patch = ActivityPatch(
        id=uuid4(),
        tenant_id=uuid4(),
        title="Changed",
        chapter_number=None,
        pass_threshold=0,
    )
    assert stage_activity_patch(db, patch) is True
    assert fields == [
        ("title", "Changed"),
        ("chapter_number", None),
        ("pass_threshold", 0.0),
    ]
    assert len(calls) == 1
    assert "populate_existing" not in calls[0].get_execution_options()
    db.scalar = lambda stmt: calls.append(stmt)
    assert stage_activity_patch(db, patch) is False
    assert len(calls) == 2 and len(fields) == 3


def test_existing_create_update_and_patch_keep_returns_flushes_and_field_order(
    monkeypatch,
):
    values = _values()
    identity = uuid4()
    payload = ActivityWrite(id=identity, **values)
    staged = []
    events = []
    fact = object()

    class AssignmentOnly:
        def __setattr__(self, name, value):
            staged.append((name, value))

    row = AssignmentOnly()
    db = SimpleNamespace(
        scalar=lambda stmt: row,
        flush=lambda: events.append("flush"),
    )

    def add(session, fields):
        assert session is db and fields == payload.model_dump()
        events.append("add")
        return row

    def project(selected):
        assert selected is row
        events.append("fact")
        return fact

    monkeypatch.setattr(references, "_add_activity", add)
    monkeypatch.setattr(references, "_activity_fact", project)
    assert references.create_activity(db, payload) is fact
    assert events == ["add", "flush", "fact"]
    events.clear()
    assert references.update_activity(db, payload) is fact
    assert staged == list(payload.model_dump(exclude={"id", "tenant_id"}).items())
    assert events == ["flush", "fact"]
    events.clear()
    staged.clear()
    patch = ActivityPatch(
        id=identity, tenant_id=values["tenant_id"], pass_threshold=0, title="Changed"
    )
    assert references.patch_activity(db, patch) is fact
    assert staged == [("title", "Changed"), ("pass_threshold", 0.0)]
    assert events == ["fact"]
    db.scalar = lambda stmt: None
    for operation, command in (
        (references.update_activity, payload),
        (references.patch_activity, patch),
    ):
        with pytest.raises(
            references.ReferenceNotFound, match="^activity not found for tenant$"
        ):
            operation(db, command)
    assert events == ["fact"]


def test_private_helpers_keep_raw_values_and_assignment_order(monkeypatch):
    raw = object()
    observed = []

    class RawActivity:
        def __init__(self, **values):
            observed.append(("construct", values))

        def __setattr__(self, name, value):
            observed.append((name, value))

    monkeypatch.setattr(references, "Activity", RawActivity)
    db = SimpleNamespace(add=lambda row: observed.append(("add", row)))
    row = references._add_activity(db, {"pass_threshold": raw, "assessment_mode": None})
    references._assign_activity(
        row, (("title", raw), ("pass_threshold", None), ("question_count", "raw"))
    )
    assert observed == [
        ("construct", {"pass_threshold": raw, "assessment_mode": None}),
        ("add", row),
        ("title", raw),
        ("pass_threshold", None),
        ("question_count", "raw"),
    ]
