"""Closed fourteen-table storage protocol canaries, authored before implementation."""

from dataclasses import MISSING, fields, replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning.curriculum_models import CURRICULUM_MODELS
from dotmac_learning.curriculum_transfer_contracts import (
    ChapterBookmarkRowV2,
    ChapterNoteRowV2,
    ChapterReadRowV2,
    ChapterRowV2,
    LearningSnapshotV2,
    QuestionBankRowV2,
    QuestionRowV2,
    SubtopicReadRowV2,
)
from dotmac_learning.models import TENANT_MODELS
from dotmac_learning.transfer_contracts import (
    Float8Bits,
    JsonbText,
    LearningSnapshotV1,
    SnapshotValidationError,
)

from tests.unit.test_learning_transfer_contracts import (
    ROW_TYPES as FIRST_ROW_TYPES,
)
from tests.unit.test_learning_transfer_contracts import carried_column_keys

ROW_TYPES = (
    ChapterRowV2,
    QuestionBankRowV2,
    QuestionRowV2,
    ChapterReadRowV2,
    SubtopicReadRowV2,
    ChapterBookmarkRowV2,
    ChapterNoteRowV2,
)
TABLES = (
    "courses",
    "activities",
    "submissions",
    "scores",
    "course_completions",
    "certificates",
    "learning_events",
    "chapters",
    "question_banks",
    "questions",
    "chapter_reads",
    "subtopic_reads",
    "chapter_bookmarks",
    "chapter_notes",
)


def empty_snapshot(tenant):
    return LearningSnapshotV2(
        version=2, tenant_ids=(tenant,), **{table: () for table in TABLES}
    )


def test_all_135_persisted_columns_are_mandatory_in_v2():
    total = 0
    for row_type, model in zip(
        FIRST_ROW_TYPES + ROW_TYPES, TENANT_MODELS + CURRICULUM_MODELS, strict=True
    ):
        row_fields = fields(row_type)
        assert {field.name for field in row_fields} == carried_column_keys(model)
        assert all(
            field.default is MISSING and field.default_factory is MISSING
            for field in row_fields
        )
        total += len(row_fields)
    assert total == 135
    assert len(fields(LearningSnapshotV2)) == 16
    assert all(
        field.default is MISSING and field.default_factory is MISSING
        for field in fields(LearningSnapshotV2)
    )


def test_v2_does_not_redefine_the_seven_table_v1_protocol():
    assert {field.name for field in fields(LearningSnapshotV1)} == set(TABLES[:7]) | {
        "version",
        "tenant_ids",
    }
    assert empty_snapshot(uuid4()).version == 2
    for version in (1, True, 3):
        with pytest.raises(SnapshotValidationError):
            replace(empty_snapshot(uuid4()), version=version)


def test_new_rows_reject_missing_history_wrong_extent_and_duplicate_keys():
    tenant = uuid4()
    now = datetime(2020, 1, 2, 3, 4, 5, 123456, tzinfo=UTC)
    note = ChapterNoteRowV2(
        id=uuid4(),
        tenant_id=tenant,
        person_id=uuid4(),
        course_id=uuid4(),
        chapter_id=uuid4(),
        body="  <raw>\n ",
        created_at=now,
        updated_at=now,
    )
    assert (
        replace(empty_snapshot(tenant), chapter_notes=(note,)).chapter_notes[0].body
        == "  <raw>\n "
    )
    with pytest.raises(SnapshotValidationError):
        replace(empty_snapshot(uuid4()), chapter_notes=(note,))
    with pytest.raises(SnapshotValidationError):
        replace(empty_snapshot(tenant), chapter_notes=(note, note))
    with pytest.raises(TypeError):
        ChapterNoteRowV2(
            **{
                field.name: getattr(note, field.name)
                for field in fields(note)
                if field.name != "updated_at"
            }
        )
    with pytest.raises(SnapshotValidationError):
        replace(empty_snapshot(tenant), tenant_ids=())


def test_question_storage_keeps_integer_weight_and_all_historical_json_kinds():
    now = datetime(2020, 1, 1, tzinfo=UTC)
    question = QuestionRowV2(
        id=uuid4(),
        tenant_id=uuid4(),
        bank_id=uuid4(),
        ext_id="",
        stem="",
        type="historic",
        options=JsonbText("null"),
        correct=JsonbText("42"),
        rubric_category="historic",
        category=None,
        explanation="",
        weight=-7,
        created_at=now,
        updated_at=now,
    )
    assert question.weight == -7 and question.category is None
    for value in (
        "null",
        '"null"',
        "[1, null]",
        '{"precise": 12345678901234567890.1234567890}',
    ):
        assert replace(question, options=JsonbText(value)).options == JsonbText(value)
    for value in (True, 1.0, Float8Bits("3ff0000000000000")):
        with pytest.raises(SnapshotValidationError):
            replace(question, weight=value)


def test_v2_extent_is_nonempty_unique_and_native_uuid_only():
    tenant = uuid4()
    snapshot = empty_snapshot(tenant)
    for extent in ((), (tenant, tenant), [tenant], (str(tenant),)):
        with pytest.raises(SnapshotValidationError):
            replace(snapshot, tenant_ids=extent)
