"""Source bank options through the bank-version lifecycle and PG reloads."""

import dotmac_learning as learning
import pytest
from dotmac_kernel import audit_actions
from dotmac_learning.bank_activity_contracts import BankActivityTerms
from dotmac_learning.curriculum_models import Question
from dotmac_learning.manifest import module
from dotmac_learning.models import Activity
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from tests import test_learning_curriculum as curriculum
from tests.test_learning_catalog import _scope

learning_database = curriculum.learning_database
curriculum_case = curriculum.curriculum_case


@pytest.fixture(autouse=True)
def declared_audit(monkeypatch):
    monkeypatch.setattr(
        audit_actions,
        "_active_registry",
        audit_actions.AuditActionRegistry.from_manifests([module]),
    )


def _document():
    configs = (
        (
            "numeric",
            {"tolerance": 0.5, "metadata": [None, False, 0, {"value": 2.5}]},
            42,
        ),
        (
            "short_text",
            {"regex": True, "metadata": {"nested": [True, None]}},
            ["go+al"],
        ),
        ("single", ["AAAA", "BBBB", "CCCC", "DDDD"], ["AAAA"]),
    )
    return learning.BankDocument(
        course="options",
        chapter=2,
        kind="chapter",
        version=3,
        questions=tuple(
            learning.BankQuestion(
                id=f"Q{i}",
                stem="Question",
                type=configs[i % 3][0],
                rubric_category="recall"
                if i < 2
                else "application"
                if i < 7
                else "analysis",
                options=configs[i % 3][1],
                correct=configs[i % 3][2],
                explanation="Stored explanation",
                weight=i + 1,
            )
            for i in range(10)
        ),
    )


def _audit(tenant):
    return learning.BankAuditContext(
        tenant_id=tenant, actor_type="system", source_application="tests"
    )


def test_legacy_bank_writers_are_fail_closed(curriculum_case):
    engine, (tenant, _) = curriculum_case
    document = _document()
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        with pytest.raises(learning.LegacyBankWriterRetired):
            learning.load_bank(
                db,
                tenant_id=tenant,
                course_id=tenant,
                doc=document,
                missing_question_weight=1,
            )
        with pytest.raises(learning.LegacyBankWriterRetired):
            learning.import_bank_with_activity(
                db,
                learning.ImportBankWithActivityCommand(
                    tenant_id=tenant,
                    doc=document,
                    terms=BANK_ACTIVITY_TERMS,
                    missing_question_weight=1,
                ),
            )
        assert db.scalars(select(Question)).all() == []
        assert db.scalars(select(Activity)).all() == []


def test_numeric_and_regex_options_round_trip_and_drive_existing_grader(
    curriculum_case,
):
    engine, (tenant, _) = curriculum_case
    document = _document()
    assert learning.lint_bank(document) == []
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = learning.create_draft_course(
            db,
            learning.CourseCreate(
                tenant_id=tenant,
                slug=document.course,
                title="Options",
                discipline="x",
                listed=False,
            ),
        )
        course_id = course.id
        root = db.get_transaction()
        report = learning.import_bank_document_as_draft(
            db,
            learning.ImportBankDocumentAsDraftCommand(
                tenant_id=tenant,
                course_id=course_id,
                doc=document,
                missing_question_weight=1,
            ),
            audit_context=_audit(tenant),
        )
        assert report.lint_violations == ()
        published = learning.publish_bank_version(
            db,
            learning.PublishBankVersionCommand(
                tenant_id=tenant, version_id=report.version_id
            ),
            audit_context=_audit(tenant),
        )
        assert published.published and published.version.version_no == 1
        bank_id = report.bank_id
        db.flush()
        assert db.get_transaction() is root
    # A new Session after commit proves physical JSONB reload through both APIs.
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        assert db.scalar(text("SELECT current_user")) == "app_user"
        assert not db.scalar(
            text("SELECT rolbypassrls FROM pg_roles WHERE rolname=current_user")
        )
        rebuilt = learning.doc_from_db(
            db, tenant_id=tenant, bank_id=bank_id, course_slug=document.course
        )
        assert learning.lint_bank(rebuilt) == []
        # doc.version (3) is transport metadata; Learning allocated v1.
        assert rebuilt.version == 1
        expected = {q.id: q for q in document.questions}
        assert {q.id for q in rebuilt.questions} == set(expected)
        stored = db.scalars(select(Question).where(Question.bank_id == bank_id)).all()
        assert len(stored) == len(document.questions)
        for question in rebuilt.questions:
            original = expected[question.id]
            assert type(question.options) is type(original.options)
            assert question.options == original.options
            assert type(question.correct) is type(original.correct)
            assert question.correct == original.correct
            assert question.type == original.type and question.weight == original.weight
        for row in stored:
            original = expected[row.ext_id]
            assert row.options == original.options and row.correct == original.correct
            assert db.scalar(
                select(func.jsonb_typeof(Question.options)).where(Question.id == row.id)
            ) == ("array" if row.type == "single" else "object")
        grading = learning.questions_for(db, tenant_id=tenant, bank_id=bank_id)
        answers = {
            q["ext_id"]: [42.25]
            if q["type"] == "numeric"
            else ["goooal"]
            if q["type"] == "short_text"
            else ["AAAA"]
            for q in grading
        }
        accepted = learning.grade_submission(
            answers, grading, 1.0, missing_question_weight=1
        )
        assert accepted.passed and accepted.fraction == 1.0
        rejected_answers = {
            q["ext_id"]: [43]
            if q["type"] == "numeric"
            else ["miss"]
            if q["type"] == "short_text"
            else ["DDDD"]
            for q in grading
        }
        rejected = learning.grade_submission(
            rejected_answers, grading, 1.0, missing_question_weight=1
        )
        assert not rejected.passed and rejected.fraction == 0.0
        activities = db.scalars(
            select(Activity).where(Activity.bank_id == bank_id)
        ).all()
        # Import-as-draft + publish never create or touch an Activity.
        assert activities == []


BANK_ACTIVITY_TERMS = BankActivityTerms(
    pass_threshold_by_kind={"chapter": 0.0, "mid": 0.60, "final": 0.70},
    fallback_pass_threshold=0.0,
    new_activity_assessment_mode="graded",
    new_activity_grading="auto",
    new_activity_weight=1.0,
    new_activity_scoring_policy="best",
    new_activity_min_attempt_interval_seconds=0,
)
