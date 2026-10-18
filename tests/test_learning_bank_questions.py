"""Lossless stored-question facts on migrated PostgreSQL; hosted CI only."""

import json
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
from uuid import uuid4

import dotmac_learning as learning
import pytest
from dotmac_learning.curriculum_models import Question, QuestionBank
from sqlalchemy import func, select, text

from tests import test_learning_results as results
from tests.test_learning_results import scoped

learning_database = results.learning_database
result_database = results.result_database


def _bank(db, tenant, course):
    bank = QuestionBank(tenant_id=tenant, course_id=course, kind="practice", version=1)
    db.add(bank)
    db.flush()
    return bank


def _question(db, tenant, bank, *, ext="Q1", kind="single", options=None, correct=None):
    question = Question(
        tenant_id=tenant,
        bank_id=bank,
        ext_id=ext,
        stem="Stored stem <with markup>",
        type=kind,
        options=deepcopy(options),
        correct=deepcopy(correct),
        rubric_category="application",
        category="numeracy",
        explanation="Stored explanation",
        weight=3,
    )
    db.add(question)
    return question


def test_bank_questions_none_missing_bank_and_tenant_isolation(result_database):
    _, app, tenant, _, course, _, other, *_ = result_database
    with scoped(app, tenant) as db:
        bank = _bank(db, tenant, course)
        _question(db, tenant, bank.id, options=["yes"], correct=["yes"])
        # The read retains source query autoflush for caller-staged questions.
        root = db.get_transaction()
        assert len(learning.bank_questions(db, tenant_id=tenant, bank_id=bank.id)) == 1
        assert db.get_transaction() is root
        assert learning.bank_questions(db, tenant_id=tenant, bank_id=None) == ()
        assert learning.bank_questions(db, tenant_id=tenant, bank_id=uuid4()) == ()
        assert learning.bank_questions(db, tenant_id=other, bank_id=bank.id) == ()
        bank_id = bank.id
    with scoped(app, other) as db:
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls FROM pg_roles "
                "WHERE rolname=current_user"
            )
        )
        assert learning.bank_questions(db, tenant_id=tenant, bank_id=bank_id) == ()


def test_bank_questions_all_types_fields_membership_and_valid_non_mcq_shapes(
    result_database,
):
    _, app, tenant, _, course, _, _, *_ = result_database
    with scoped(app, tenant) as db:
        bank = _bank(db, tenant, course)
        other_bank = _bank(db, tenant, course)
        specs = (
            ("single", ["yes", "no"], ["yes"]),
            ("multi", ["a", "b"], ["a", "b"]),
            ("truefalse", [True, False], [True]),
            ("numeric", {"tolerance": 0.5}, 42),
            ("short_text", {"regex": True}, ["go+al"]),
        )
        for kind, options, correct in specs:
            _question(
                db,
                tenant,
                bank.id,
                ext=kind,
                kind=kind,
                options=options,
                correct=correct,
            )
        _question(db, tenant, other_bank.id, ext="excluded", options=[], correct=[])
        db.flush()
        bank_id = bank.id
        db.expunge_all()
        stored = db.scalars(
            select(Question)
            .where(Question.tenant_id == tenant)
            .where(Question.bank_id == bank_id)
        ).all()
        root, nested = db.get_transaction(), db.get_nested_transaction()
        before_count = db.scalar(select(func.count()).select_from(Question))
        facts = learning.bank_questions(db, tenant_id=tenant, bank_id=bank_id)
        assert isinstance(facts, tuple)
        assert {fact.id for fact in facts} == {row.id for row in stored}
        assert {fact.type for fact in facts} == {kind for kind, _, _ in specs}
        by_id = {row.id: row for row in stored}
        for fact in facts:
            assert isinstance(fact, learning.QuestionFact)
            for field in fields(learning.QuestionFact):
                assert getattr(fact, field.name) == getattr(by_id[fact.id], field.name)
            assert fact.created_at is not None and fact.updated_at is not None
            with pytest.raises(FrozenInstanceError):
                fact.stem = "mutated"
        assert db.scalar(select(func.count()).select_from(Question)) == before_count
        assert db.get_transaction() is root and db.get_nested_transaction() is nested
        assert not db.new and not db.dirty and not db.deleted
        # Existing grading dictionaries remain the owner of source grading semantics.
        grading_questions = learning.questions_for(
            db, tenant_id=tenant, bank_id=bank_id
        )
        assert {q["ext_id"] for q in grading_questions} == {
            fact.ext_id for fact in facts
        }
        non_mcq = [
            q for q in grading_questions if q["type"] in {"numeric", "short_text"}
        ]
        graded = learning.grade_submission(
            {"numeric": [42.25], "short_text": ["goooal"]},
            non_mcq,
            1.0,
            missing_question_weight=1,
        )
        assert graded.passed and graded.fraction == 1.0
    assert {fact.ext_id for fact in facts} == {kind for kind, _, _ in specs}


@pytest.mark.parametrize(
    "value",
    [
        None,
        False,
        True,
        0,
        7,
        0.0,
        2.5,
        "",
        "legacy",
        [],
        [None, False, 0, 2.5, {"nested": [True]}],
        {},
        {"nested": [None, False, 0, 2.5, "", {"passed": True}]},
    ],
)
def test_bank_questions_preserve_json_types_on_real_storage(result_database, value):
    _, app, tenant, _, course, _, _, *_ = result_database
    with scoped(app, tenant) as db:
        bank = _bank(db, tenant, course)
        question = _question(
            db, tenant, bank.id, kind="numeric", options=value, correct=value
        )
        db.flush()
        bank_id, question_id = bank.id, question.id
        db.expunge_all()
        (fact,) = learning.bank_questions(db, tenant_id=tenant, bank_id=bank_id)
        assert fact.id == question_id
        for actual in (fact.options, fact.correct):
            assert type(actual) is type(value)
            assert json.dumps(actual, sort_keys=True) == json.dumps(
                value, sort_keys=True
            )
        row = db.get(Question, question_id)
        for name in ("options", "correct"):
            actual, persisted = getattr(fact, name), getattr(row, name)
            assert type(actual) is type(persisted)
            assert json.dumps(actual, sort_keys=True) == json.dumps(
                persisted, sort_keys=True
            )
            if isinstance(actual, list | dict):
                assert actual is not persisted


def test_bank_question_json_is_independent_of_rows_fields_and_repeated_reads(
    result_database,
):
    _, app, tenant, _, course, _, _, *_ = result_database
    original = {"nested": [None, {"flag": True}]}
    with scoped(app, tenant) as db:
        bank = _bank(db, tenant, course)
        for ext in ("a", "b"):
            _question(db, tenant, bank.id, ext=ext, options=original, correct=original)
        db.flush()
        bank_id = bank.id
        db.expunge_all()
        first = learning.bank_questions(db, tenant_id=tenant, bank_id=bank_id)
        first[0].options["nested"][1]["flag"] = False
        first[0].options["nested"].append("changed")
        assert first[0].correct == first[1].options == first[1].correct == original
        again = learning.bank_questions(db, tenant_id=tenant, bank_id=bank_id)
        assert all(fact.options == fact.correct == original for fact in again)
        stored = db.scalars(select(Question).where(Question.bank_id == bank_id)).all()
        assert all(row.options == row.correct == original for row in stored)
        assert not db.dirty
