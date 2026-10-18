"""Hosted sitting parity and persistence canaries; never run with local Docker."""

from datetime import UTC, datetime

import pytest
from dotmac_learning import attempts
from dotmac_learning.attempt_contracts import CloseAttemptCommand, OpenAttemptCommand
from dotmac_learning.attempt_models import ActivityAttempt
from dotmac_learning.models import LearningEvent
from sqlalchemy import select

from tests import test_learning_results as result_tests
from tests.test_learning_results import scoped

learning_database = result_tests.learning_database
result_database = result_tests.result_database


def test_open_reuse_persisted_draw_reopen_variant_close_none_and_clock(result_database):
    _, app, tenant, person, _, activity, *_ = result_database
    clock = datetime(2000, 1, 1, tzinfo=UTC)
    scope = {"tenant_id": tenant, "person_id": person, "activity_id": activity}
    with scoped(app, tenant) as db:
        root = db.get_transaction()
        with db.begin_nested():
            nested = db.get_nested_transaction()
            opened = attempts.open_or_create_attempt(
                db,
                OpenAttemptCommand(
                    **scope, all_ext_ids=[str(i) for i in range(20)], count=5, now=clock
                ),
            )
            assert len(opened.attempt.question_ext_ids) == 5
            assert opened.attempt.started_at == clock
            assert opened.event.status == "recorded"
            assert opened.event.event.detail == {"attempt": 5}
            assert opened.event.event.occurred_at > clock
            again = attempts.open_or_create_attempt(
                db, OpenAttemptCommand(**scope, all_ext_ids=["replaced"], count=1)
            )
            assert again.attempt == opened.attempt and again.event is None
            closed = attempts.close_open_attempt(
                db, CloseAttemptCommand(**scope, now=clock)
            )
            assert closed.submitted_at == clock
            assert attempts.close_open_attempt(db, CloseAttemptCommand(**scope)) is None
            fresh = attempts.open_or_create_attempt(
                db,
                OpenAttemptCommand(
                    **scope, all_ext_ids=[str(i) for i in range(20)], count=5
                ),
            )
            assert fresh.attempt.id != opened.attempt.id
            assert fresh.attempt.question_ext_ids != opened.attempt.question_ext_ids
            assert (
                db.get_transaction() is root and db.get_nested_transaction() is nested
            )
        db.rollback()
    with scoped(app, tenant) as db:
        assert db.scalar(select(ActivityAttempt)) is None
        assert db.scalar(select(LearningEvent)) is None


@pytest.mark.parametrize("count", [0, -1])
def test_empty_draw_source_nonpositive_count(result_database, count):
    _, app, tenant, person, _, activity, *_ = result_database
    with scoped(app, tenant) as db:
        result = attempts.open_or_create_attempt(
            db,
            OpenAttemptCommand(
                tenant_id=tenant,
                person_id=person,
                activity_id=activity,
                all_ext_ids=["a"],
                count=count,
            ),
        )
        assert result.attempt.question_ext_ids == []


def test_engine_provided_questions_and_exact_attempt_variant(
    result_database, monkeypatch
):
    from dotmac_learning import exam_engine
    from dotmac_learning.attempt_contracts import SelectionQuestion

    _, app, tenant, person, _, activity, *_ = result_database
    calls = []
    original = exam_engine.select

    def observe(sitter, questions, policy, *, variant=""):
        calls.append((sitter.id, questions, policy.total, variant))
        return original(sitter, questions, policy, variant=variant)

    monkeypatch.setattr(exam_engine, "select", observe)
    scope = {"tenant_id": tenant, "person_id": person, "activity_id": activity}
    questions = (
        SelectionQuestion(ext_id="from-question", category="skill", options=["x", "y"]),
    )
    with scoped(app, tenant) as db:
        first = attempts.open_or_create_attempt(
            db,
            OpenAttemptCommand(
                **scope, all_ext_ids=["shim-a", "shim-b"], count=9, questions=questions
            ),
        )
        assert first.attempt.question_ext_ids == ["from-question"]
        assert calls[0] == (person, list(questions), 2, "attempt0:")
        attempts.close_open_attempt(db, CloseAttemptCommand(**scope))
        attempts.open_or_create_attempt(
            db, OpenAttemptCommand(**scope, all_ext_ids=["shim-a"], count=9)
        )
        assert calls[1][2:] == (1, "attempt1:")
        assert calls[1][1][0].ext_id == "shim-a"


def test_sitting_preserves_missing_identity_source_behavior_optional_event_failure(
    result_database,
):
    from uuid import uuid4

    _, app, tenant, _, _, activity, *_ = result_database
    with scoped(app, tenant) as db:
        result = attempts.open_or_create_attempt(
            db,
            OpenAttemptCommand(
                tenant_id=tenant,
                person_id=uuid4(),
                activity_id=activity,
                all_ext_ids=["a"],
                count=1,
            ),
        )
        assert result.attempt.question_ext_ids == ["a"]
        assert result.event.status == "failed"
        assert (
            db.scalar(
                select(ActivityAttempt).where(ActivityAttempt.id == result.attempt.id)
            )
            is not None
        )


def test_required_attempt_flush_failure_propagates_before_optional_event(
    result_database, monkeypatch
):
    from uuid import uuid4

    from dotmac_learning import events
    from sqlalchemy.exc import IntegrityError

    _, app, tenant, person, *_ = result_database

    def forbidden(*args, **kwargs):
        raise AssertionError("required failure must precede optional emission")

    monkeypatch.setattr(events, "emit", forbidden)
    with scoped(app, tenant) as db:
        with pytest.raises(IntegrityError) as caught:
            attempts.open_or_create_attempt(
                db,
                OpenAttemptCommand(
                    tenant_id=tenant,
                    person_id=person,
                    activity_id=uuid4(),
                    all_ext_ids=[],
                    count=0,
                ),
            )
        assert caught.value.orig.sqlstate == "23503"
        db.rollback()


def test_two_connections_reuse_one_current_sitting_under_real_learner_lock(
    result_database,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from sqlalchemy import text

    _, app, tenant, person, _, activity, *_ = result_database
    opened, entered, release = Event(), Event(), Event()
    pids = {}
    payload = OpenAttemptCommand(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        all_ext_ids=["a", "b"],
        count=1,
    )

    def first():
        with scoped(app, tenant) as db:
            pids["winner"] = db.scalar(text("SELECT pg_backend_pid()"))
            value = attempts.open_or_create_attempt(db, payload)
            opened.set()
            assert release.wait(20)
            return value

    def second():
        assert opened.wait(10)
        with scoped(app, tenant) as db:
            pids["contender"] = db.scalar(text("SELECT pg_backend_pid()"))
            entered.set()
            return attempts.open_or_create_attempt(db, payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(first), pool.submit(second)
        try:
            assert entered.wait(10)
            with app.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as observer:
                deadline = monotonic() + 5
                blocked = False
                while monotonic() < deadline:
                    blockers = observer.scalar(
                        text("SELECT pg_blocking_pids(:pid)"),
                        {"pid": pids["contender"]},
                    )
                    if pids["winner"] in blockers:
                        blocked = True
                        break
                    sleep(0.02)
                assert blocked
        finally:
            release.set()
        first_value, second_value = one.result(timeout=20), two.result(timeout=20)
    assert first_value.attempt == second_value.attempt
    assert first_value.event.status == "recorded" and second_value.event is None
    with scoped(app, tenant) as db:
        assert len(db.scalars(select(ActivityAttempt)).all()) == 1


def test_unlocked_missing_identity_race_preserves_partial_unique_23505(
    result_database, monkeypatch
):
    """The source missing-row lock is a no-op; the unique error must propagate."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from uuid import uuid4

    from sqlalchemy.exc import IntegrityError

    _, app, tenant, _, _, activity, *_ = result_database
    person = uuid4()
    both_read = Barrier(2)
    original = attempts._open_attempt

    def synchronized(db, **scope):
        existing = original(db, **scope)
        assert existing is None
        both_read.wait(timeout=10)
        return existing

    monkeypatch.setattr(attempts, "_open_attempt", synchronized)
    payload = OpenAttemptCommand(
        tenant_id=tenant,
        person_id=person,
        activity_id=activity,
        all_ext_ids=["a"],
        count=1,
    )

    def sit():
        try:
            with scoped(app, tenant) as db:
                attempts.open_or_create_attempt(db, payload)
            return "opened"
        except IntegrityError as exc:
            assert exc.orig.sqlstate == "23505"
            assert exc.orig.diag.constraint_name == "uq_activity_attempts_open"
            return "unique_error"

    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = pool.submit(sit), pool.submit(sit)
        outcomes = [one.result(timeout=20), two.result(timeout=20)]
    assert sorted(outcomes) == ["opened", "unique_error"]


@pytest.mark.parametrize(
    "historical", [None, 7, 1.25, True, "legacy", {"paper": ["b", "a"]}, ["b", "a"]]
)
def test_historical_json_shape_open_reuse_and_close_preserve_storage(
    result_database, monkeypatch, historical
):
    import json
    from copy import deepcopy
    from uuid import uuid4

    from dotmac_learning import exam_engine
    from sqlalchemy import text

    _, app, tenant, person, _, activity, *_ = result_database
    scope = {"tenant_id": tenant, "person_id": person, "activity_id": activity}
    clock = datetime(2000, 1, 1, tzinfo=UTC)
    with scoped(app, tenant) as db:
        # Assign through raw JSONB SQL so JSON null is not mapped to SQL NULL.
        db.execute(
            text(
                "INSERT INTO mod_learning.activity_attempts "
                "(id, tenant_id, activity_id, person_id, question_ext_ids, started_at) "
                "VALUES (:id, :tenant, :activity, :person, "
                "CAST(:paper AS jsonb), :started)"
            ),
            {
                "id": uuid4(),
                "tenant": tenant,
                "activity": activity,
                "person": person,
                "paper": json.dumps(historical),
                "started": clock,
            },
        )

        def forbidden(*args, **kwargs):
            raise AssertionError("an existing historical sitting must not be redrawn")

        monkeypatch.setattr(exam_engine, "select", forbidden)
        opened = attempts.open_or_create_attempt(
            db, OpenAttemptCommand(**scope, all_ext_ids=["replacement"], count=1)
        )
        assert opened.event is None
        assert type(opened.attempt.question_ext_ids) is type(historical)
        assert opened.attempt.question_ext_ids == historical
        closed = attempts.close_open_attempt(
            db, CloseAttemptCommand(**scope, now=clock)
        )
        assert type(closed.question_ext_ids) is type(historical)
        assert closed.question_ext_ids == historical and closed.submitted_at == clock
        stored = db.scalar(
            select(ActivityAttempt).where(ActivityAttempt.id == closed.id)
        )
        assert type(stored.question_ext_ids) is type(historical)
        assert stored.question_ext_ids == historical
        expected = deepcopy(historical)
        if isinstance(closed.question_ext_ids, list):
            closed.question_ext_ids.append("detached")
        elif isinstance(closed.question_ext_ids, dict):
            closed.question_ext_ids["paper"].append("detached")
        assert stored.question_ext_ids == expected
