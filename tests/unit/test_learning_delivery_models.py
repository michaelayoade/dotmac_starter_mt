"""Independent Academy 096 migration oracles for the final 48 learning fields."""

from dotmac_kernel.models import Party, PartyPerson, Tenant
from dotmac_learning.delivery_models import (
    DELIVERY_MODELS,
    DELIVERY_TABLES,
    SESSION_STATUSES,
    SESSION_TYPES,
    Announcement,
    ClassSession,
    OfferingActivity,
    SuccessQueueEntry,
)
from dotmac_learning.models import Activity
from dotmac_learning.participation_models import Cohort, CourseOffering
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateIndex, CreateTable

FIELDS = {
    "offering_activities": "offering_id activity_id release_at due_at",
    "class_sessions": (
        "cohort_id offering_id session_type title starts_at ends_at "
        "instructor_person_id location join_url notes status"
    ),
    "announcements": "cohort_id author_person_id title body_md body_html",
    "success_queue_entries": (
        "person_id cohort_id reason_kind supporting_facts severity detected_at "
        "assigned_to recommended_action status resolved_at resolved_by score_hint"
    ),
}
NULLABLE = {
    "offering_activities": {"release_at", "due_at"},
    "class_sessions": {
        "offering_id",
        "ends_at",
        "instructor_person_id",
        "location",
        "join_url",
        "notes",
    },
    "announcements": {"cohort_id"},
    "success_queue_entries": {"cohort_id", "assigned_to", "resolved_at", "resolved_by"},
}
INDEXES = {
    "offering_activities": {
        "ix_offering_activities_tenant_id": ("tenant_id",),
        "ix_offering_activities_offering_id": ("offering_id",),
        "ix_offering_activities_activity_id": ("activity_id",),
    },
    "class_sessions": {
        "ix_class_sessions_tenant_id": ("tenant_id",),
        "ix_class_sessions_cohort_start": ("tenant_id", "cohort_id", "starts_at"),
        "ix_class_sessions_instructor": ("tenant_id", "instructor_person_id"),
    },
    "announcements": {
        "ix_announcements_tenant_id": ("tenant_id",),
        "ix_announcements_cohort_id": ("cohort_id",),
    },
    "success_queue_entries": {
        "ix_success_queue_tenant_id": ("tenant_id",),
        "ix_success_queue_person_id": ("person_id",),
        "ix_success_queue_cohort_id": ("cohort_id",),
        "ix_success_queue_status": ("tenant_id", "status", "severity"),
        "uq_success_queue_open_person_reason": (
            "tenant_id",
            "person_id",
            "reason_kind",
        ),
    },
}
DEFAULTS = {
    "offering_activities": {},
    "class_sessions": {"session_type": "live_class", "status": "scheduled"},
    "announcements": {},
    "success_queue_entries": {
        "supporting_facts": "'{}'",
        "severity": "medium",
        "recommended_action": "",
        "status": "open",
        "score_hint": "0",
    },
}
LENGTHS = {
    "offering_activities": {},
    "class_sessions": {
        "session_type": 20,
        "title": 200,
        "location": 160,
        "join_url": 500,
        "status": 20,
    },
    "announcements": {"title": 200},
    "success_queue_entries": {"reason_kind": 30, "severity": 10, "status": 15},
}


def test_exact_source_fields_nullable_lengths_defaults_indexes_and_no_checks():
    assert DELIVERY_MODELS == (
        OfferingActivity,
        ClassSession,
        Announcement,
        SuccessQueueEntry,
    )
    assert DELIVERY_TABLES == tuple(FIELDS)
    assert [len(m.__table__.c) for m in DELIVERY_MODELS] == [8, 15, 9, 16]
    assert SESSION_TYPES == ("live_class", "lab", "lecture", "other")
    assert SESSION_STATUSES == ("scheduled", "cancelled", "completed")
    for model in DELIVERY_MODELS:
        table = model.__table__
        name = table.name
        assert table.schema == "mod_learning"
        assert set(table.c.keys()) == set(FIELDS[name].split()) | {
            "id",
            "tenant_id",
            "created_at",
            "updated_at",
        }
        assert {c.name for c in table.c if c.nullable} == NULLABLE[name]
        assert not any(isinstance(c, CheckConstraint) for c in table.constraints)
        uniques = {("tenant_id", "id")}
        if name == "offering_activities":
            uniques.add(("tenant_id", "offering_id", "activity_id"))
        assert {
            tuple(c.name for c in u.columns)
            for u in table.constraints
            if isinstance(u, UniqueConstraint)
        } == uniques
        assert {
            i.name: tuple(c.name for c in i.columns) for i in table.indexes
        } == INDEXES[name]
        assert {
            c.name: str(c.server_default.arg)
            for c in table.c
            if c.server_default is not None
            and c.name not in {"created_at", "updated_at"}
        } == DEFAULTS[name]
        assert {
            c.name: c.type.length for c in table.c if getattr(c.type, "length", None)
        } == LENGTHS[name]
        assert isinstance(table.c.id.type, Uuid)
        assert isinstance(table.c.tenant_id.type, Uuid)
        for column in table.c:
            if column.name.endswith("_id") or column.name in {
                "assigned_to",
                "resolved_by",
            }:
                assert isinstance(column.type, Uuid)
        assert table.c.id.primary_key and table.c.id.default is not None
        assert table.c.id.server_default is None
        assert table.c.updated_at.onupdate is not None
        for column in table.c:
            if column.name.endswith("_at"):
                assert isinstance(column.type, DateTime)
                assert column.type.timezone
        for field in ("created_at", "updated_at"):
            assert str(table.c[field].server_default.arg) == "now()"


def test_genuine_foreign_keys_and_preserved_logical_references():
    expected = {
        OfferingActivity: {
            ("tenant_id", "offering_id"): [
                CourseOffering.__table__.c.tenant_id,
                CourseOffering.__table__.c.id,
            ],
            ("tenant_id", "activity_id"): [
                Activity.__table__.c.tenant_id,
                Activity.__table__.c.id,
            ],
        },
        ClassSession: {
            ("tenant_id", "cohort_id"): [
                Cohort.__table__.c.tenant_id,
                Cohort.__table__.c.id,
            ]
        },
        Announcement: {
            ("tenant_id", "cohort_id"): [
                Cohort.__table__.c.tenant_id,
                Cohort.__table__.c.id,
            ],
            ("tenant_id", "author_person_id"): [
                Party.__table__.c.tenant_id,
                Party.__table__.c.id,
            ],
            ("author_person_id",): [PartyPerson.__table__.c.party_id],
        },
        SuccessQueueEntry: {
            ("tenant_id", "person_id"): [
                Party.__table__.c.tenant_id,
                Party.__table__.c.id,
            ],
            ("person_id",): [PartyPerson.__table__.c.party_id],
        },
    }
    for model, references in expected.items():
        references = {("tenant_id",): [Tenant.__table__.c.id], **references}
        fks = {
            tuple(fk.column_keys): fk for fk in model.__table__.foreign_key_constraints
        }
        assert set(fks) == set(references)
        for fields, columns in references.items():
            assert [e.column for e in fks[fields].elements] == columns
            assert fks[fields].ondelete == "CASCADE"
            assert fks[fields].match is None
    for field in ("offering_id", "instructor_person_id"):
        assert not ClassSession.__table__.c[field].foreign_keys
    for field in ("cohort_id", "assigned_to", "resolved_by"):
        assert not SuccessQueueEntry.__table__.c[field].foreign_keys


def test_queue_migrated_partial_uniqueness_and_python_defaults():
    table = SuccessQueueEntry.__table__
    partial = next(
        i for i in table.indexes if i.name == "uq_success_queue_open_person_reason"
    )
    assert partial.unique
    assert str(partial.dialect_options["postgresql"]["where"]) == "status = 'open'"
    assert not any(i.unique for i in table.indexes if i is not partial)
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        ddl = str(CreateIndex(partial).compile(dialect=dialect))
        assert "UNIQUE INDEX uq_success_queue_open_person_reason" in ddl
        assert "WHERE status = 'open'" in ddl  # acknowledged history may repeat.
    assert table.c.supporting_facts.default is not None
    assert isinstance(table.c.score_hint.type, Float)
    assert isinstance(table.c.recommended_action.type, Text)
    for field in ("body_md", "body_html"):
        assert isinstance(Announcement.__table__.c[field].type, Text)
    assert isinstance(ClassSession.__table__.c.notes.type, Text)
    for field, value in {
        "severity": "medium",
        "recommended_action": "",
        "status": "open",
        "score_hint": 0.0,
    }.items():
        assert table.c[field].default.arg == value
    for field in ("session_type", "status"):
        assert ClassSession.__table__.c[field].default is None


def test_native_jsonb_unrestricted_values_and_sqlite_ddl_compilation():
    column = SuccessQueueEntry.__table__.c.supporting_facts
    assert isinstance(column.type, JSONB)
    assert not column.nullable  # JSON null is allowed; SQL NULL is not.
    assert not column.type.none_as_null
    assert isinstance(column.type.dialect_impl(postgresql.dialect()), JSONB)
    assert isinstance(column.type.dialect_impl(sqlite.dialect()), JSON)
    assert column.type.compile(dialect=postgresql.dialect()) == "JSONB"
    assert column.type.compile(dialect=sqlite.dialect()) == "JSON"
    comparator = column.contains({"historical": True})
    assert "@>" in str(comparator.compile(dialect=postgresql.dialect()))
    for value in (None, 7, "scalar", [1, None], {"nested": [False]}):
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            processor = column.type.dialect_impl(dialect).bind_processor(dialect)
            assert processor is not None
            assert isinstance(processor(value), str)
    for model in DELIVERY_MODELS:
        for dialect in (postgresql.dialect(), sqlite.dialect()):
            ddl = str(CreateTable(model.__table__).compile(dialect=dialect))
            assert f"mod_learning.{model.__tablename__}" in ddl
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        ddl = str(CreateTable(SuccessQueueEntry.__table__).compile(dialect=dialect))
        assert "DEFAULT '{}'" in ddl
