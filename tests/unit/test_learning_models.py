"""Source-preserving learning contracts; metadata only, no database runtime."""

from pathlib import Path

import pytest
from dotmac_kernel.models import Party, PartyPerson, Tenant
from dotmac_kernel.modules import ModuleRegistry
from dotmac_kernel.namespaces import module_schema
from dotmac_learning import migrations, versions_dir
from dotmac_learning.attempt_models import ATTEMPT_MODELS
from dotmac_learning.curriculum_models import CURRICULUM_MODELS
from dotmac_learning.delivery_models import DELIVERY_MODELS
from dotmac_learning.manifest import module
from dotmac_learning.models import (
    SCHEMA,
    TENANT_MODELS,
    TENANT_TABLES,
    Activity,
    Certificate,
    Course,
    CourseCompletion,
    LearningEvent,
    Score,
    Submission,
)
from dotmac_learning.participation_models import PARTICIPATION_MODELS
from sqlalchemy import JSON, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable


def test_learning_registration_uses_the_shipped_allocation_and_exact_tables():
    """Check current-tree composition only; this does not prove a published floor.

    A published floor needs a separate isolated installed positive canary and,
    after actual Kernel publication, a previous published
    UnallocatedNamespaceError negative canary.
    """
    registry = ModuleRegistry((module,))
    owner = registry.namespaces().get("learning")
    assert owner is not None
    assert (owner.owner, owner.db_schema, owner.prefix, owner.branch_label) == (
        "learning",
        "mod_learning",
        "lr",
        "learning",
    )
    assert registry.get("learning") is module
    assert frozenset(module.tables) == frozenset(
        {
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
            "cohorts",
            "enrollments",
            "tracks",
            "track_courses",
            "cohort_tracks",
            "course_offerings",
            "course_prerequisites",
            "activity_attempts",
            "attempt_grants",
            "offering_activities",
            "class_sessions",
            "announcements",
            "success_queue_entries",
        }
    )
    assert registry.namespaces().declared_tables("mod_learning") == frozenset(
        module.tables
    )
    assert registry.namespaces().declared_platform_tables("mod_learning") == frozenset()
    assert module.audit_actions == (
        "attempt_granted",
        "announcement.created",
        "scheduling.update_session",
        "success_queue.acknowledge",
        "success_queue.assign",
        "success_queue.resolve",
        "success_queue.message_segment",
    )


def unique_keys(model):
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_first_slice_has_seven_tenant_tables_and_no_product_delivery_state():
    assert SCHEMA == module_schema("learning")
    assert set(TENANT_TABLES) == {
        "courses",
        "activities",
        "submissions",
        "scores",
        "course_completions",
        "certificates",
        "learning_events",
    }
    assert len(TENANT_MODELS) == len(TENANT_TABLES) == 7
    for model in TENANT_MODELS:
        table = model.__table__
        assert table.schema == SCHEMA
        assert not table.c.tenant_id.nullable
        tenant_index = next(
            index
            for index in table.indexes
            if index.name == f"ix_{table.name}_tenant_id"
        )
        assert tuple(tenant_index.columns) == (table.c.tenant_id,)
        assert tenant_index.table is table
        assert not tenant_index.unique
        assert any(
            fk.column is Tenant.__table__.c.id and fk.ondelete == "CASCADE"
            for fk in table.c.tenant_id.foreign_keys
        )
        assert not any(isinstance(c, CheckConstraint) for c in table.constraints)
        for name in ("created_at", "updated_at"):
            assert not table.c[name].nullable
            assert table.c[name].type.timezone
            assert table.c[name].server_default is not None
        assert table.c.updated_at.onupdate is not None
    assert "erp_synced_at" not in CourseCompletion.__table__.c


def test_events_require_tenant_membership_and_a_real_person_profile():
    constraints = LearningEvent.__table__.foreign_key_constraints
    edges = {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.column for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in constraints
    }
    assert edges == {
        (("tenant_id",), (Tenant.__table__.c.id,), "CASCADE"),
        (
            ("tenant_id", "person_id"),
            (Party.__table__.c.tenant_id, Party.__table__.c.id),
            "CASCADE",
        ),
        (("person_id",), (PartyPerson.__table__.c.party_id,), "CASCADE"),
    }
    assert not LearningEvent.__table__.c.person_id.nullable
    assert LearningEvent.__table__.c.course_id.nullable
    assert LearningEvent.__table__.c.subject_id.nullable
    assert {
        index.name: tuple(column.name for column in index.columns)
        for index in LearningEvent.__table__.indexes
        if len(index.columns) > 1
    } == {
        "ix_learning_events_person_time": ("tenant_id", "person_id", "occurred_at"),
        "ix_learning_events_course_kind": ("tenant_id", "course_id", "kind"),
        "ix_learning_events_kind_time": ("tenant_id", "kind", "occurred_at"),
        "uq_learning_events_once_per_subject": (
            "tenant_id",
            "person_id",
            "kind",
            "subject_id",
        ),
    }
    once_per_subject = next(
        index
        for index in LearningEvent.__table__.indexes
        if index.name == "uq_learning_events_once_per_subject"
    )
    assert once_per_subject.unique
    assert str(once_per_subject.dialect_options["postgresql"]["where"]) == (
        "kind IN ('chapter_completed', 'certificate_earned')"
    )


@pytest.mark.parametrize(
    ("model", "column_name"),
    [
        (Activity, "course_id"),
        (Activity, "bank_id"),
        (Submission, "person_id"),
        (CourseCompletion, "person_id"),
        (Certificate, "person_id"),
        (LearningEvent, "course_id"),
        (LearningEvent, "subject_id"),
    ],
)
def test_source_plain_uuid_references_remain_unconstrained(model, column_name):
    assert not model.__table__.c[column_name].foreign_keys


@pytest.mark.parametrize(
    ("model", "parent", "reference", "constraint_name"),
    [
        (Submission, Activity, "activity_id", "fk_submissions_tenant_activity"),
        (Score, Submission, "submission_id", "fk_scores_tenant_submission"),
        (CourseCompletion, Course, "course_id", "fk_course_completions_tenant_course"),
        (Certificate, Course, "course_id", "fk_certificates_tenant_course"),
    ],
)
def test_aggregate_references_retain_tenant_composite_cascades(
    model, parent, reference, constraint_name
):
    constraint = next(
        fk
        for fk in model.__table__.foreign_key_constraints
        if fk.name == constraint_name
    )
    assert tuple(c.name for c in constraint.columns) == ("tenant_id", reference)
    assert tuple(e.column for e in constraint.elements) == (
        parent.__table__.c.tenant_id,
        parent.__table__.c.id,
    )
    assert constraint.ondelete == "CASCADE"


def test_attempt_ledger_allows_multiple_scores_and_retains_uniqueness_boundaries():
    assert unique_keys(Submission) == {
        ("tenant_id", "id"),
        ("tenant_id", "activity_id", "person_id", "attempt_no"),
    }
    assert unique_keys(Score) == set()
    assert unique_keys(CourseCompletion) == {
        ("tenant_id", "id"),
        ("tenant_id", "person_id", "course_id"),
    }
    assert unique_keys(Certificate) == {
        ("tenant_id", "id"),
        ("tenant_id", "person_id", "course_id"),
        ("tenant_id", "serial"),
    }
    assert unique_keys(Course) == {("tenant_id", "slug"), ("tenant_id", "id")}
    assert unique_keys(Activity) == {("tenant_id", "id")}
    # View-event dedupe is an application query, not a unique ledger key.
    assert unique_keys(LearningEvent) == set()


def test_nullable_policy_fields_and_source_defaults_keep_their_meaning():
    for name in ("chapter_number", "bank_id", "max_attempts", "question_count"):
        assert Activity.__table__.c[name].nullable
    assert Activity.__table__.c.max_attempts.default is None  # Unlimited attempts.
    assert CourseCompletion.__table__.c.completed_at.nullable
    assert Score.__table__.c.override_reason.nullable
    assert not Certificate.__table__.c.issued_at.nullable
    for model, expected in (
        (Course, {"version": 1}),
        (Submission, {"attempt_no": 1}),
        (Score, {"source": "auto"}),
        (CourseCompletion, {"status": "in_progress", "pct": 0.0}),
    ):
        for name, value in expected.items():
            assert model.__table__.c[name].default.arg == value
    # New writes require explicit policy; historical migration defaults remain.
    for model, names in (
        (Course, ("status", "listed")),
        (Activity, ("type", "pass_threshold", "assessment_mode", "grading", "weight")),
    ):
        for name in names:
            assert model.__table__.c[name].default is None


def test_json_payload_defaults_are_independent_per_record():
    for model, name, empty in (
        (Submission, "answers", {}),
        (Score, "per_item", []),
        (LearningEvent, "detail", {}),
    ):
        column = model.__table__.c[name]
        assert not column.nullable
        assert column.type.__class__.__name__ == "JSONB"
        first = column.default.arg(None)
        second = column.default.arg(None)
        assert first == second == empty
        assert first is not second


@pytest.mark.parametrize(
    ("model", "column_name"),
    [(Submission, "answers"), (Score, "per_item"), (LearningEvent, "detail")],
)
def test_json_payloads_compile_on_sqlite_and_retain_postgresql_jsonb(
    model, column_name
):
    column = model.__table__.c[column_name]
    postgres = postgresql.dialect()
    sqlite_dialect = sqlite.dialect()
    assert isinstance(column.type, JSONB)
    assert isinstance(column.type.dialect_impl(postgres), JSONB)
    sqlite_type = column.type.dialect_impl(sqlite_dialect)
    assert isinstance(sqlite_type, JSON)
    assert not isinstance(sqlite_type, JSONB)
    assert column.type.compile(dialect=postgres) == "JSONB"
    assert column.type.compile(dialect=sqlite_dialect) == "JSON"
    assert " @> " in str(column.contains({}).compile(dialect=postgres))
    assert " ? " in str(column.has_key("key").compile(dialect=postgres))


@pytest.mark.parametrize("model", TENANT_MODELS)
def test_learning_table_ddl_compiles_for_shared_sqlite_metadata(model):
    ddl = str(CreateTable(model.__table__).compile(dialect=sqlite.dialect()))
    assert f"CREATE TABLE {SCHEMA}.{model.__tablename__}" in ddl


def test_root_migration_locator_reexports_the_canonical_callable():
    assert versions_dir is migrations.versions_dir
    assert versions_dir() == Path(migrations.__file__).resolve().parent / "versions"
    assert (versions_dir() / "lr_0001_learning.py").is_file()


# Physical index contract from lr_0001 through lr_0006; schema relocation
# preserves these names, including the source queue naming exception.
MIGRATED_INDEXES = {
    "courses": {"ix_courses_tenant_id": (("tenant_id",), False, None)},
    "activities": {
        "ix_activities_tenant_id": (("tenant_id",), False, None),
        "ix_activities_course_id": (("course_id",), False, None),
        "ix_activities_bank_id": (("bank_id",), False, None),
    },
    "submissions": {
        "ix_submissions_tenant_id": (("tenant_id",), False, None),
        "ix_submissions_activity_id": (("activity_id",), False, None),
        "ix_submissions_person_id": (("person_id",), False, None),
    },
    "scores": {
        "ix_scores_tenant_id": (("tenant_id",), False, None),
        "ix_scores_submission_id": (("submission_id",), False, None),
    },
    "course_completions": {
        "ix_course_completions_tenant_id": (("tenant_id",), False, None),
        "ix_course_completions_person_id": (("person_id",), False, None),
        "ix_course_completions_course_id": (("course_id",), False, None),
    },
    "certificates": {
        "ix_certificates_tenant_id": (("tenant_id",), False, None),
        "ix_certificates_person_id": (("person_id",), False, None),
        "ix_certificates_course_id": (("course_id",), False, None),
    },
    "learning_events": {
        "ix_learning_events_tenant_id": (("tenant_id",), False, None),
        "ix_learning_events_person_id": (("person_id",), False, None),
        "ix_learning_events_person_time": (
            ("tenant_id", "person_id", "occurred_at"),
            False,
            None,
        ),
        "ix_learning_events_course_kind": (
            ("tenant_id", "course_id", "kind"),
            False,
            None,
        ),
        "ix_learning_events_kind_time": (
            ("tenant_id", "kind", "occurred_at"),
            False,
            None,
        ),
        "uq_learning_events_once_per_subject": (
            ("tenant_id", "person_id", "kind", "subject_id"),
            True,
            "kind IN " "('chapter_completed', " "'certificate_earned')",
        ),
    },
    "chapters": {
        "ix_chapters_tenant_id": (("tenant_id",), False, None),
        "ix_chapters_course_id": (("course_id",), False, None),
    },
    "question_banks": {
        "ix_question_banks_tenant_id": (("tenant_id",), False, None),
        "ix_question_banks_course_id": (("course_id",), False, None),
    },
    "questions": {
        "ix_questions_tenant_id": (("tenant_id",), False, None),
        "ix_questions_bank_id": (("bank_id",), False, None),
    },
    "chapter_reads": {
        "ix_chapter_reads_tenant_id": (("tenant_id",), False, None),
        "ix_chapter_reads_person_id": (("person_id",), False, None),
        "ix_chapter_reads_chapter_id": (("chapter_id",), False, None),
    },
    "subtopic_reads": {
        "ix_subtopic_reads_tenant_id": (("tenant_id",), False, None),
        "ix_subtopic_reads_person_id": (("person_id",), False, None),
        "ix_subtopic_reads_chapter_id": (("chapter_id",), False, None),
    },
    "chapter_bookmarks": {
        "ix_chapter_bookmarks_tenant_id": (("tenant_id",), False, None),
        "ix_chapter_bookmarks_person_id": (("person_id",), False, None),
        "ix_chapter_bookmarks_course_id": (("course_id",), False, None),
        "ix_chapter_bookmarks_chapter_id": (("chapter_id",), False, None),
    },
    "chapter_notes": {
        "ix_chapter_notes_tenant_id": (("tenant_id",), False, None),
        "ix_chapter_notes_person_id": (("person_id",), False, None),
        "ix_chapter_notes_course_id": (("course_id",), False, None),
        "ix_chapter_notes_chapter_id": (("chapter_id",), False, None),
    },
    "cohorts": {"ix_cohorts_tenant_id": (("tenant_id",), False, None)},
    "tracks": {"ix_tracks_tenant_id": (("tenant_id",), False, None)},
    "cohort_tracks": {
        "ix_cohort_tracks_tenant_id": (("tenant_id",), False, None),
        "ix_cohort_tracks_cohort_id": (("cohort_id",), False, None),
        "ix_cohort_tracks_track_id": (("track_id",), False, None),
    },
    "track_courses": {
        "ix_track_courses_tenant_id": (("tenant_id",), False, None),
        "ix_track_courses_track_id": (("track_id",), False, None),
        "ix_track_courses_course_id": (("course_id",), False, None),
    },
    "enrollments": {
        "ix_enrollments_tenant_id": (("tenant_id",), False, None),
        "ix_enrollments_cohort_id": (("cohort_id",), False, None),
        "ix_enrollments_person_id": (("person_id",), False, None),
        "ix_enrollments_track_id": (("track_id",), False, None),
        "ix_enrollments_audience": (("tenant_id", "audience"), False, None),
        "ix_enrollments_access_ends_at": (("tenant_id", "access_ends_at"), False, None),
    },
    "course_offerings": {
        "ix_course_offerings_tenant_id": (("tenant_id",), False, None),
        "ix_course_offerings_cohort_id": (("cohort_id",), False, None),
        "ix_course_offerings_course_id": (("course_id",), False, None),
    },
    "course_prerequisites": {
        "ix_course_prerequisites_tenant_id": (("tenant_id",), False, None),
        "ix_course_prerequisites_course_id": (("course_id",), False, None),
        "ix_course_prerequisites_requires_course_id": (
            ("requires_course_id",),
            False,
            None,
        ),
    },
    "activity_attempts": {
        "uq_activity_attempts_open": (
            ("tenant_id", "activity_id", "person_id"),
            True,
            "submitted_at IS NULL",
        ),
        "ix_activity_attempts_tenant_id": (("tenant_id",), False, None),
        "ix_activity_attempts_activity_id": (("activity_id",), False, None),
        "ix_activity_attempts_person_id": (("person_id",), False, None),
    },
    "attempt_grants": {
        "ix_attempt_grants_lookup": (
            ("tenant_id", "activity_id", "person_id"),
            False,
            None,
        ),
        "ix_attempt_grants_tenant_id": (("tenant_id",), False, None),
        "ix_attempt_grants_activity_id": (("activity_id",), False, None),
        "ix_attempt_grants_person_id": (("person_id",), False, None),
    },
    "offering_activities": {
        "ix_offering_activities_tenant_id": (("tenant_id",), False, None),
        "ix_offering_activities_offering_id": (("offering_id",), False, None),
        "ix_offering_activities_activity_id": (("activity_id",), False, None),
    },
    "class_sessions": {
        "ix_class_sessions_tenant_id": (("tenant_id",), False, None),
        "ix_class_sessions_cohort_start": (
            ("tenant_id", "cohort_id", "starts_at"),
            False,
            None,
        ),
        "ix_class_sessions_instructor": (
            ("tenant_id", "instructor_person_id"),
            False,
            None,
        ),
    },
    "announcements": {
        "ix_announcements_tenant_id": (("tenant_id",), False, None),
        "ix_announcements_cohort_id": (("cohort_id",), False, None),
    },
    "success_queue_entries": {
        "ix_success_queue_tenant_id": (("tenant_id",), False, None),
        "ix_success_queue_person_id": (("person_id",), False, None),
        "ix_success_queue_cohort_id": (("cohort_id",), False, None),
        "ix_success_queue_status": (("tenant_id", "status", "severity"), False, None),
        "uq_success_queue_open_person_reason": (
            ("tenant_id", "person_id", "reason_kind"),
            True,
            "status = 'open'",
        ),
    },
}


def test_all_learning_indexes_preserve_migrated_names_columns_uniqueness_and_scope():
    models = (
        *TENANT_MODELS,
        *CURRICULUM_MODELS,
        *PARTICIPATION_MODELS,
        *ATTEMPT_MODELS,
        *DELIVERY_MODELS,
    )
    assert len(models) == len(MIGRATED_INDEXES) == 27
    assert {model.__table__.name for model in models} == set(MIGRATED_INDEXES)
    for model in models:
        table = model.__table__
        assert table.schema == "mod_learning"
        assert all(index.table is table for index in table.indexes)
        assert {
            index.name: (
                tuple(column.name for column in index.columns),
                index.unique,
                str(index.dialect_options["postgresql"]["where"])
                if index.dialect_options["postgresql"]["where"] is not None
                else None,
            )
            for index in table.indexes
        } == MIGRATED_INDEXES[table.name]
