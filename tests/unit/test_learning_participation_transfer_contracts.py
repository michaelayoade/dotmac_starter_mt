"""Full twenty-one-table storage protocol canaries, authored before its port."""

from dataclasses import MISSING, fields, replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning.curriculum_models import CURRICULUM_MODELS
from dotmac_learning.models import TENANT_MODELS
from dotmac_learning.participation_models import PARTICIPATION_MODELS
from dotmac_learning.participation_transfer_contracts import (
    CohortRowV3,
    CohortTrackRowV3,
    CourseOfferingRowV3,
    CoursePrerequisiteRowV3,
    EnrollmentRowV3,
    LearningSnapshotV3,
    TrackCourseRowV3,
    TrackRowV3,
)
from dotmac_learning.transfer_contracts import Float8Bits, SnapshotValidationError

from tests.unit.test_learning_curriculum_transfer_contracts import (
    ROW_TYPES as V2_ROW_TYPES,
)
from tests.unit.test_learning_curriculum_transfer_contracts import TABLES as V2_TABLES
from tests.unit.test_learning_transfer_contracts import ROW_TYPES as V1_ROW_TYPES

ROW_TYPES = (
    CohortRowV3,
    EnrollmentRowV3,
    TrackRowV3,
    TrackCourseRowV3,
    CohortTrackRowV3,
    CourseOfferingRowV3,
    CoursePrerequisiteRowV3,
)
TABLES = (
    *V2_TABLES,
    "cohorts",
    "enrollments",
    "tracks",
    "track_courses",
    "cohort_tracks",
    "course_offerings",
    "course_prerequisites",
)


def empty_snapshot(tenant):
    return LearningSnapshotV3(
        version=3, tenant_ids=(tenant,), **{table: () for table in TABLES}
    )


def test_v3_adds_all_59_persisted_fields_without_defaults():
    total = 0
    for row_type, model in zip(ROW_TYPES, PARTICIPATION_MODELS, strict=True):
        row_fields = fields(row_type)
        assert {field.name for field in row_fields} == set(model.__table__.c.keys())
        assert all(
            field.default is MISSING and field.default_factory is MISSING
            for field in row_fields
        )
        total += len(row_fields)
    assert total == 59
    assert len(fields(LearningSnapshotV3)) == 23
    assert all(
        field.default is MISSING and field.default_factory is MISSING
        for field in fields(LearningSnapshotV3)
    )


def test_v3_rejects_old_versions_wrong_extent_and_duplicate_storage_rows():
    tenant = uuid4()
    now = datetime(2020, 1, 2, tzinfo=UTC)
    track = TrackRowV3(
        id=uuid4(),
        tenant_id=tenant,
        created_at=now,
        updated_at=now,
        slug="legacy",
        name="  retained  ",
        status="historical",
    )
    snapshot = replace(empty_snapshot(tenant), tracks=(track,))
    assert snapshot.tracks[0].name == "  retained  "
    for version in (1, 2, True, 4):
        with pytest.raises(SnapshotValidationError):
            replace(snapshot, version=version)
    with pytest.raises(SnapshotValidationError):
        replace(empty_snapshot(uuid4()), tracks=(track,))
    with pytest.raises(SnapshotValidationError):
        replace(snapshot, tracks=(track, track))
    with pytest.raises(SnapshotValidationError):
        replace(snapshot, tracks=[track])
    for extent in ((), (tenant, tenant), (str(tenant),)):
        with pytest.raises(SnapshotValidationError):
            replace(snapshot, tenant_ids=extent)


def test_cohort_threshold_uses_nullable_exact_float8_bits():
    now = datetime(2020, 1, 2, tzinfo=UTC)
    row = CohortRowV3(
        id=uuid4(),
        tenant_id=uuid4(),
        created_at=now,
        updated_at=now,
        name="",
        discipline="",
        status="historical",
        delivery_mode="historical",
        entrance_bank_id=uuid4(),
        entrance_time_limit_minutes=-1,
        auto_accept_threshold=None,
    )
    for bits in ("8000000000000000", "7ff0000000000000", "7ff8000000000001"):
        assert replace(
            row, auto_accept_threshold=Float8Bits(bits)
        ).auto_accept_threshold == Float8Bits(bits)
    for value in (0.5, 0, True):
        with pytest.raises(SnapshotValidationError):
            replace(row, auto_accept_threshold=value)


def test_nullable_enrollment_history_and_raw_employee_reference_are_mandatory():
    now = datetime(2020, 1, 2, tzinfo=UTC)
    row = EnrollmentRowV3(
        id=uuid4(),
        tenant_id=uuid4(),
        created_at=now,
        updated_at=now,
        cohort_id=uuid4(),
        person_id=uuid4(),
        track_id=None,
        role_in_cohort="historical",
        status="historical",
        audience=None,
        employee_ref="  raw employee ref  ",
        access_ends_at=None,
    )
    assert row.employee_ref == "  raw employee ref  "
    with pytest.raises(TypeError):
        EnrollmentRowV3(
            **{
                field.name: getattr(row, field.name)
                for field in fields(row)
                if field.name != "access_ends_at"
            }
        )
    assert "raw employee" not in repr(row)
    assert "raw employee" not in repr(
        replace(empty_snapshot(row.tenant_id), enrollments=(row,))
    )


def test_all_21_tables_and_194_columns_are_closed_and_mandatory():
    assert len(TABLES) == len(set(TABLES)) == 21
    total = 0
    for row_type, model in zip(
        V1_ROW_TYPES + V2_ROW_TYPES + ROW_TYPES,
        TENANT_MODELS + CURRICULUM_MODELS + PARTICIPATION_MODELS,
        strict=True,
    ):
        row_fields = fields(row_type)
        assert {field.name for field in row_fields} == set(model.__table__.c.keys())
        assert all(
            field.default is MISSING and field.default_factory is MISSING
            for field in row_fields
        )
        total += len(row_fields)
    assert total == 194
