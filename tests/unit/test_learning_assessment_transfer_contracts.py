"""Closed twenty-three-table storage canaries, authored before the V4 port."""

from dataclasses import MISSING, fields, replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning.assessment_transfer_contracts import (
    ActivityAttemptRowV4,
    AttemptGrantRowV4,
    LearningSnapshotV4,
)
from dotmac_learning.attempt_models import ATTEMPT_MODELS
from dotmac_learning.participation_transfer_contracts import LearningSnapshotV3
from dotmac_learning.transfer_contracts import JsonbText, SnapshotValidationError

from tests.unit.test_learning_participation_transfer_contracts import (
    TABLES as V3_TABLES,
)
from tests.unit.test_learning_transfer_contracts import carried_column_keys

TABLES = (*V3_TABLES, "activity_attempts", "attempt_grants")


def empty_snapshot(tenant):
    return LearningSnapshotV4(
        version=4, tenant_ids=(tenant,), **{table: () for table in TABLES}
    )


def test_v4_adds_all_eighteen_persisted_fields_without_defaults():
    total = 0
    for row_type, model in zip(
        (ActivityAttemptRowV4, AttemptGrantRowV4), ATTEMPT_MODELS, strict=True
    ):
        row_fields = fields(row_type)
        assert {field.name for field in row_fields} == carried_column_keys(model)
        assert all(
            field.default is MISSING and field.default_factory is MISSING
            for field in row_fields
        )
        total += len(row_fields)
    assert total == 18
    assert len(fields(LearningSnapshotV4)) == 25
    assert all(
        field.default is MISSING and field.default_factory is MISSING
        for field in fields(LearningSnapshotV4)
    )


def test_v4_preserves_v3_scope_and_rejects_wrong_extent_versions_and_keys():
    tenant = uuid4()
    snapshot = empty_snapshot(tenant)
    assert len(fields(LearningSnapshotV3)) == 23
    assert {field.name for field in fields(LearningSnapshotV3)} == set(V3_TABLES) | {
        "version",
        "tenant_ids",
    }
    for version in (1, 2, 3, True, 5):
        with pytest.raises(SnapshotValidationError):
            replace(snapshot, version=version)
    now = datetime(2020, 1, 2, tzinfo=UTC)
    row = AttemptGrantRowV4(
        id=uuid4(),
        tenant_id=tenant,
        activity_id=uuid4(),
        person_id=uuid4(),
        extra_attempts=-7,
        reason="  raw historical reason  ",
        granted_by=None,
        created_at=now,
        updated_at=now,
    )
    assert (
        replace(snapshot, attempt_grants=(row,)).attempt_grants[0].extra_attempts == -7
    )
    with pytest.raises(SnapshotValidationError):
        replace(empty_snapshot(uuid4()), attempt_grants=(row,))
    with pytest.raises(SnapshotValidationError):
        replace(snapshot, attempt_grants=(row, row))
    with pytest.raises(SnapshotValidationError):
        replace(snapshot, attempt_grants=[row])
    assert "raw historical reason" not in repr(row)
    assert "raw historical reason" not in repr(replace(snapshot, attempt_grants=(row,)))


def test_sitting_history_keeps_every_jsonb_kind_and_nullable_submitted_time():
    now = datetime(2020, 1, 2, tzinfo=UTC)
    row = ActivityAttemptRowV4(
        id=uuid4(),
        tenant_id=uuid4(),
        activity_id=uuid4(),
        person_id=uuid4(),
        question_ext_ids=JsonbText("null"),
        started_at=now,
        submitted_at=None,
        created_at=now,
        updated_at=now,
    )
    for value in ("null", '"old"', "-7", '["q2", "q1"]', '{"raw": true}'):
        assert replace(row, question_ext_ids=JsonbText(value)).question_ext_ids == value
    with pytest.raises(SnapshotValidationError):
        replace(row, question_ext_ids=["q1"])
    with pytest.raises(TypeError):
        ActivityAttemptRowV4(
            **{
                field.name: getattr(row, field.name)
                for field in fields(row)
                if field.name != "submitted_at"
            }
        )
