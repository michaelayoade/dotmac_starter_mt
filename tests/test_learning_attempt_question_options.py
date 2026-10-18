"""Hosted-only real PostgreSQL mixed-JSON attempt open/reload/close/retake canary."""

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import dotmac_learning as learning
from dotmac_learning import exam_engine
from dotmac_learning.attempt_models import ActivityAttempt
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from tests import test_learning_results as result_tests
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database


def test_public_attempt_preserves_mixed_question_configs_and_source_variants(
    result_database,
    monkeypatch,
):
    _, app, tenant, person, _, activity, *_ = result_database
    configs = ({"tolerance": 0.5}, {"regex": True}, ["a", "b", "c", "d"])
    questions = tuple(
        learning.SelectionQuestion(
            ext_id=f"q{i}", category="skill", options=configs[i % 3]
        )
        for i in range(9)
    )
    originals = deepcopy([q.options for q in questions])
    scope = {"tenant_id": tenant, "person_id": person, "activity_id": activity}
    clock = datetime(2000, 1, 1, tzinfo=UTC)
    command = learning.OpenAttemptCommand(
        **scope,
        all_ext_ids=[q.ext_id for q in questions],
        count=9,
        questions=questions,
        now=clock,
    )
    source_select = exam_engine.select
    expected = {
        variant: [
            q.ext_id
            for q in source_select(
                SimpleNamespace(id=person),
                list(questions),
                learning.SelectionPolicy(total=9),
                variant=variant,
            )
        ]
        for variant in ("attempt0:", "attempt1:")
    }
    calls = []

    def observe(sitter, supplied, policy, variant=""):
        calls.append((variant, deepcopy([q.options for q in supplied])))
        assert all(
            q is not original for q, original in zip(supplied, questions, strict=False)
        )
        return source_select(sitter, supplied, policy, variant=variant)

    # Observe the real engine; the sitting writer and PostgreSQL are not substituted.
    monkeypatch.setattr(exam_engine, "select", observe)
    with scoped(app, tenant) as db:
        assert isinstance(db, Session)
        assert db.scalar(
            text(
                "SELECT NOT rolsuper AND NOT rolbypassrls "
                "FROM pg_roles WHERE rolname=current_user"
            )
        )
        root = db.get_transaction()
        first = learning.open_or_create_attempt(db, command)
        assert first.attempt.question_ext_ids == expected["attempt0:"]
        assert first.event.status == "recorded"
        db.flush()
        db.expunge_all()
        stored = db.get(ActivityAttempt, first.attempt.id)
        assert stored.question_ext_ids == expected["attempt0:"]
        assert stored.started_at == clock
        reused = learning.open_or_create_attempt(db, command)
        assert reused.attempt == first.attempt and reused.event is None
        closed = learning.close_open_attempt(db, learning.CloseAttemptCommand(**scope))
        assert closed.id == first.attempt.id and closed.submitted_at is not None
        assert closed.question_ext_ids == expected["attempt0:"]
        assert calls == [("attempt0:", originals)]
        assert db.get_transaction() is root
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        reloaded = db.get(ActivityAttempt, closed.id)
        assert reloaded.submitted_at == closed.submitted_at
        assert reloaded.question_ext_ids == expected["attempt0:"]
        retake = learning.open_or_create_attempt(db, command)
        assert retake.attempt.id != first.attempt.id
        assert retake.attempt.question_ext_ids == expected["attempt1:"]
        assert calls == [("attempt0:", originals), ("attempt1:", originals)]
        db.flush()
        db.expunge_all()
        assert (
            db.get(ActivityAttempt, retake.attempt.id).question_ext_ids
            == expected["attempt1:"]
        )
        assert len(db.scalars(select(ActivityAttempt)).all()) == 2
        assert db.get_transaction() is root
    assert [q.options for q in command.questions] == originals
    assert [type(q.options) for q in command.questions] == [type(o) for o in originals]
