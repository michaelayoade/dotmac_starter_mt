"""Source bank options through real public writers and PostgreSQL reloads."""

import dotmac_learning as learning
import pytest
from dotmac_learning.curriculum_models import Question
from dotmac_learning.models import Activity
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from tests import test_learning_curriculum as curriculum
from tests.test_learning_catalog import _scope

learning_database = curriculum.learning_database
curriculum_case = curriculum.curriculum_case


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


@pytest.mark.parametrize("path", ["load_bank", "combined_bank_activity"])
def test_numeric_and_regex_options_round_trip_and_drive_existing_grader(
    curriculum_case, path
):
    engine, (tenant, _) = curriculum_case
    document = _document()
    assert learning.lint_bank(document) == []
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        course = learning.create_draft_course(
            db,
            learning.CourseCreate(
                tenant_id=tenant, slug=document.course, title="Options", discipline="x"
            ),
        )
        course_id = course.id
        root = db.get_transaction()
        if path == "load_bank":
            bank_id = learning.load_bank(
                db, tenant_id=tenant, course_id=course_id, doc=document
            ).id
        else:
            command = learning.ImportBankWithActivityCommand(
                tenant_id=tenant, doc=document
            )
            assert [q.options for q in command.doc.questions] == [
                q.options for q in document.questions
            ]
            result = learning.import_bank_with_activity(db, command)
            assert result is not None and result.created
            bank_id = result.bank_id
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
        assert rebuilt.version == document.version
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
        accepted = learning.grade_submission(answers, grading, 1.0)
        assert accepted.passed and accepted.fraction == 1.0
        rejected_answers = {
            q["ext_id"]: [43]
            if q["type"] == "numeric"
            else ["miss"]
            if q["type"] == "short_text"
            else ["DDDD"]
            for q in grading
        }
        rejected = learning.grade_submission(rejected_answers, grading, 1.0)
        assert not rejected.passed and rejected.fraction == 0.0
        activities = db.scalars(
            select(Activity).where(Activity.bank_id == bank_id)
        ).all()
        assert len(activities) == (0 if path == "load_bank" else 1)
        if activities:
            assert activities[0].course_id == course_id
