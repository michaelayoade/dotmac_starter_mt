"""Source-preserving learning contracts; metadata only, no database runtime."""

import pytest
from dotmac_kernel.models import Party, PartyPerson, Tenant
from dotmac_kernel.modules import ModuleRegistry
from dotmac_kernel.namespaces import module_schema
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
from sqlalchemy import CheckConstraint, UniqueConstraint


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
        }
    )
    assert registry.namespaces().declared_tables("mod_learning") == frozenset(
        module.tables
    )
    assert registry.namespaces().declared_platform_tables("mod_learning") == frozenset()


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
        assert table.c.tenant_id.index
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
        (Course, {"version": 1, "status": "published", "listed": False}),
        (
            Activity,
            {
                "type": "mcq_test",
                "pass_threshold": 0.0,
                "assessment_mode": "graded",
                "grading": "auto",
                "weight": 1.0,
            },
        ),
        (Submission, {"attempt_no": 1}),
        (Score, {"source": "auto"}),
        (CourseCompletion, {"status": "in_progress", "pct": 0.0}),
    ):
        for name, value in expected.items():
            assert model.__table__.c[name].default.arg == value


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
