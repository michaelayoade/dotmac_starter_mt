"""V5 contracts canaries authored first; hosted execution required."""

from dataclasses import MISSING, fields, replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning.delivery_models import DELIVERY_MODELS
from dotmac_learning.delivery_transfer_contracts import (
    AnnouncementRowV5,
    ClassSessionRowV5,
    LearningSnapshotV5,
    OfferingActivityRowV5,
    SuccessQueueEntryRowV5,
)
from dotmac_learning.transfer_contracts import (
    Float8Bits,
    JsonbText,
    SnapshotValidationError,
)

from tests.unit.test_learning_assessment_transfer_contracts import TABLES as V4_TABLES

TABLES = (
    *V4_TABLES,
    "offering_activities",
    "class_sessions",
    "announcements",
    "success_queue_entries",
)


def empty_snapshot(tenant):
    return LearningSnapshotV5(
        version=5, tenant_ids=(tenant,), **{name: () for name in TABLES}
    )


def test_every_new_field_and_snapshot_extent_is_mandatory():
    total = 0
    for row_type, model in zip(
        (
            OfferingActivityRowV5,
            ClassSessionRowV5,
            AnnouncementRowV5,
            SuccessQueueEntryRowV5,
        ),
        DELIVERY_MODELS,
        strict=True,
    ):
        row_fields = fields(row_type)
        assert {f.name for f in row_fields} == set(model.__table__.c.keys())
        assert all(
            f.default is MISSING and f.default_factory is MISSING for f in row_fields
        )
        total += len(row_fields)
    assert total == 48
    assert len(fields(LearningSnapshotV5)) == 29
    assert all(
        f.default is MISSING and f.default_factory is MISSING
        for f in fields(LearningSnapshotV5)
    )


def queue_row(tenant):
    now = datetime(2020, 1, 2, tzinfo=UTC)
    return SuccessQueueEntryRowV5(
        id=uuid4(),
        tenant_id=tenant,
        created_at=now,
        updated_at=now,
        person_id=uuid4(),
        cohort_id=None,
        reason_kind="raw",
        supporting_facts=JsonbText("null"),
        severity="historic",
        detected_at=now,
        assigned_to=None,
        recommended_action="  raw secret  ",
        status="historic",
        resolved_at=None,
        resolved_by=None,
        score_hint=Float8Bits("8000000000000000"),
    )


def test_strict_version_extent_keys_and_types_preserve_historical_rows():
    tenant = uuid4()
    snapshot = empty_snapshot(tenant)
    for version in (1, 2, 3, 4, True, 6):
        with pytest.raises(SnapshotValidationError):
            replace(snapshot, version=version)
    row = queue_row(tenant)
    for rows in ([row], (row, row), (replace(row, tenant_id=uuid4()),)):
        with pytest.raises(SnapshotValidationError):
            replace(snapshot, success_queue_entries=rows)
    with pytest.raises(SnapshotValidationError):
        replace(row, score_hint=0.0)
    assert "raw secret" not in repr(row)
    assert "raw secret" not in repr(replace(snapshot, success_queue_entries=(row,)))
    for value in ("null", "false", "-7", '"old"', "[]", "{}"):
        assert replace(row, supporting_facts=JsonbText(value)).supporting_facts == value
    for name in ("resolved_at", "cohort_id", "assigned_to", "resolved_by"):
        with pytest.raises(TypeError):
            SuccessQueueEntryRowV5(
                **{f.name: getattr(row, f.name) for f in fields(row) if f.name != name}
            )
