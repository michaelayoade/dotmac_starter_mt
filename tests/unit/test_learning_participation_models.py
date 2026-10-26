"""Academy 096 participation storage; explicit fresh Kernel identity translation."""

from dotmac_kernel.models import Party, PartyPerson, Tenant
from dotmac_learning.participation_models import (
    PARTICIPATION_MODELS,
    PARTICIPATION_TABLES,
    Cohort,
    Enrollment,
    Track,
    TrackCourse,
)
from sqlalchemy import CheckConstraint, UniqueConstraint

FIELDS = {
    "cohorts": (
        "name discipline status delivery_mode entrance_bank_id "
        "entrance_time_limit_minutes auto_accept_threshold "
        "auto_accept_below_action"
    ),
    "enrollments": (
        "cohort_id person_id track_id role_in_cohort status audience employee_ref "
        "access_ends_at"
    ),
    "tracks": "slug name status gating_mode",
    "track_courses": "track_id course_id order_index",
    "cohort_tracks": "cohort_id track_id status",
    "course_offerings": (
        "cohort_id course_id status starts_at ends_at pacing_default_open"
    ),
    "course_prerequisites": (
        "course_id requires_course_id condition_kind params group_key"
    ),
}
NULLABLE = {
    "cohorts": {
        "entrance_bank_id",
        "entrance_time_limit_minutes",
        "auto_accept_threshold",
    },
    "enrollments": {"track_id", "audience", "employee_ref", "access_ends_at"},
    "course_offerings": {"starts_at", "ends_at"},
}
UNIQUES = {
    "cohorts": {("tenant_id", "id")},
    "enrollments": {("tenant_id", "cohort_id", "person_id")},
    "tracks": {("tenant_id", "id"), ("tenant_id", "slug")},
    "track_courses": {("tenant_id", "track_id", "course_id")},
    "cohort_tracks": {("tenant_id", "id"), ("tenant_id", "cohort_id", "track_id")},
    "course_offerings": {("tenant_id", "id"), ("tenant_id", "cohort_id", "course_id")},
    "course_prerequisites": {
        ("tenant_id", "id"),
        (
            "tenant_id",
            "course_id",
            "requires_course_id",
            "condition_kind",
            "group_key",
        ),
    },
}
INDEXES = {
    "cohorts": {("tenant_id",)},
    "enrollments": {
        ("tenant_id",),
        ("cohort_id",),
        ("person_id",),
        ("track_id",),
        ("tenant_id", "audience"),
        ("tenant_id", "access_ends_at"),
    },
    "tracks": {("tenant_id",)},
    "track_courses": {("tenant_id",), ("track_id",), ("course_id",)},
    "cohort_tracks": {("tenant_id",), ("cohort_id",), ("track_id",)},
    "course_offerings": {("tenant_id",), ("cohort_id",), ("course_id",)},
    "course_prerequisites": {("tenant_id",), ("course_id",), ("requires_course_id",)},
}
LENGTHS = {
    "cohorts": {
        "name": 120,
        "discipline": 40,
        "status": 20,
        "delivery_mode": 20,
        "auto_accept_below_action": 16,
    },
    "enrollments": {
        "role_in_cohort": 20,
        "status": 20,
        "audience": 20,
        "employee_ref": 64,
    },
    "tracks": {"slug": 80, "name": 160, "status": 20, "gating_mode": 16},
    "course_prerequisites": {"condition_kind": 32, "group_key": 32},
    "cohort_tracks": {"status": 20},
    "course_offerings": {"status": 20},
}
DEFAULTS = {
    "cohorts": {
        "status": "active",
        "delivery_mode": "self_paced",
        "auto_accept_below_action": "waitlisted",
    },
    "tracks": {"status": "active", "gating_mode": "previous"},
    "track_courses": {"order_index": "0"},
    "cohort_tracks": {"status": "active"},
    "course_offerings": {"status": "active"},
}


def test_source_65_persisted_fields_and_migrated_contracts():
    assert set(PARTICIPATION_TABLES) == set(FIELDS)
    assert len(PARTICIPATION_MODELS) == 7
    assert sum(len(m.__table__.c) for m in PARTICIPATION_MODELS) == 65
    for model in PARTICIPATION_MODELS:
        table = model.__table__
        name = table.name
        assert table.schema == "mod_learning"
        assert set(table.c.keys()) == set(FIELDS[name].split()) | {
            "id",
            "tenant_id",
            "created_at",
            "updated_at",
        }
        assert {c.name for c in table.c if c.nullable} == NULLABLE.get(name, set())
        assert any(
            fk.column is Tenant.__table__.c.id and fk.ondelete == "CASCADE"
            for fk in table.c.tenant_id.foreign_keys
        )
        assert {tuple(c.name for c in ix.columns) for ix in table.indexes} == INDEXES[
            name
        ]
        assert {
            tuple(c.name for c in uq.columns)
            for uq in table.constraints
            if isinstance(uq, UniqueConstraint)
        } == UNIQUES[name]
        assert {
            c.name for c in table.constraints if isinstance(c, CheckConstraint)
        } == {
            "enrollments": {
                "ck_enrollments_audience",
                "ck_enrollments_staff_has_employee_ref",
            },
            "tracks": {"ck_tracks_gating_mode"},
            "cohorts": {"ck_cohorts_auto_accept_below_action"},
            "course_prerequisites": {"ck_course_prerequisites_condition_kind"},
        }.get(name, set())
        for field, length in LENGTHS.get(name, {}).items():
            assert table.c[field].type.length == length
        for field, value in DEFAULTS.get(name, {}).items():
            assert str(table.c[field].server_default.arg) == value
        assert table.c.id.default is not None
        assert table.c.updated_at.onupdate is not None
        for field in ("created_at", "updated_at"):
            assert table.c[field].type.timezone
            assert table.c[field].server_default is not None


def test_enrollment_identity_translation_targets_genuine_kernel_and_cascades():
    fks = {
        tuple(fk.column_keys): fk for fk in Enrollment.__table__.foreign_key_constraints
    }
    party = fks[("tenant_id", "person_id")]
    assert [e.column for e in party.elements] == [
        Party.__table__.c.tenant_id,
        Party.__table__.c.id,
    ]
    profile = fks[("person_id",)]
    assert [e.column for e in profile.elements] == [PartyPerson.__table__.c.party_id]
    assert party.ondelete == profile.ondelete == "CASCADE"
    membership = fks[("tenant_id", "cohort_id", "track_id")]
    assert membership.ondelete is None
    assert membership.match is None  # PostgreSQL default MATCH SIMPLE.
    assert Enrollment.__table__.c.track_id.nullable
    assert not Cohort.__table__.c.entrance_bank_id.foreign_keys
    assert TrackCourse.__table__.c.order_index.default.arg == 0
    assert Track.__table__.c.status.default.arg == "active"


def test_enrollment_state_requires_explicit_orm_and_server_facts():
    for name in ("role_in_cohort", "status"):
        column = Enrollment.__table__.c[name]
        assert column.default is None
        assert column.server_default is None
        assert column.nullable is False


def test_prerequisite_condition_defaults_materialize_legacy_rows():
    table = next(
        m.__table__
        for m in PARTICIPATION_MODELS
        if m.__tablename__ == "course_prerequisites"
    )
    assert table.c.condition_kind.default.arg == "course_completed"
    assert str(table.c.condition_kind.server_default.arg) == "course_completed"
    assert table.c.group_key.default.arg == "g0"
    assert str(table.c.group_key.server_default.arg) == "g0"
    assert table.c.params.default.arg(None) == {}
    assert table.c.params.default.arg(None) is not table.c.params.default.arg(None)
    assert str(table.c.params.server_default.arg) == "'{}'"
    assert not table.c.params.nullable
