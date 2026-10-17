"""Hosted PostgreSQL domain tests using actual migrated learning storage."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event
from uuid import uuid4

import pytest
from dotmac_learning.certificates import CertificateNotEligible, issue_certificate
from dotmac_learning.completion import recompute_completion
from dotmac_learning.models import (
    Activity,
    Certificate,
    CourseCompletion,
    LearningEvent,
    Score,
    Submission,
)
from dotmac_learning.progress_contracts import CertificateCommand, CompletionCommand
from dotmac_learning.projections import (
    best_scores_by_course_for_person,
    best_scores_for,
    best_scores_for_person,
)
from sqlalchemy import (
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import (
    _course,
    _person,
    _scope,
    _tenant,
)

learning_database = learning_catalog.learning_database


@pytest.fixture
def progress_case(learning_database):
    admin = create_engine(learning_database[0])
    actor = create_engine(learning_database[1], isolation_level="READ COMMITTED")
    tenant = uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
        with actor.begin() as conn:
            _scope(conn, tenant)
            course = _course(conn, tenant)
        yield actor, tenant, person, course
    finally:
        actor.dispose()
        admin.dispose()


def _activity(db, tenant, course, *, kind="mcq_test", chapter=None):
    activity = Activity(
        tenant_id=tenant,
        course_id=course,
        title="Activity",
        type=kind,
        chapter_number=chapter,
    )
    db.add(activity)
    db.flush()
    return activity


def _score(
    db,
    tenant,
    person,
    activity,
    *,
    attempt=1,
    fraction=1.0,
    passed=True,
    source="auto",
    at=None,
):
    sub = Submission(
        tenant_id=tenant, person_id=person, activity_id=activity.id, attempt_no=attempt
    )
    db.add(sub)
    db.flush()
    score = Score(
        tenant_id=tenant,
        submission_id=sub.id,
        score=fraction,
        max_score=1,
        fraction=fraction,
        passed=passed,
        source=source,
        created_at=at or datetime.now(UTC),
    )
    db.add(score)
    db.flush()
    return score


def test_all_activities_manual_pending_labs_and_regression(progress_case):
    engine, tenant, person, course = progress_case
    at = datetime(2026, 1, 1, tzinfo=UTC)
    command = CompletionCommand(
        tenant_id=tenant, person_id=person, course_id=course, now=at
    )
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        empty = recompute_completion(db, command)
        assert empty.completion.pct == 0 and empty.completion.status == "in_progress"
        ordinary = _activity(db, tenant, course, chapter=1)
        lab = _activity(db, tenant, course, kind="lab")
        manual = _activity(db, tenant, course)
        db.add(Submission(tenant_id=tenant, person_id=person, activity_id=manual.id))
        _score(db, tenant, person, ordinary)
        partial = recompute_completion(db, command)
        assert partial.completion.pct == 1 / 3
        assert not partial.first_completion and partial.certificate is None
        _score(db, tenant, person, manual, attempt=2)
        before_lab = recompute_completion(db, command)
        assert before_lab.completion.pct == 2 / 3
        assert not before_lab.first_completion and before_lab.certificate is None
        _score(db, tenant, person, lab)
        done = recompute_completion(db, command)
        assert done.first_completion and done.certificate.created
        assert done.certificate.event is not None
        assert done.certificate.event.status == "recorded"
        event = done.certificate.event.event
        assert event is not None
        assert (
            event.tenant_id,
            event.person_id,
            event.course_id,
            event.subject_id,
        ) == (tenant, person, course, course)
        assert event.kind == "certificate_earned"
        assert event.detail == {"serial": done.certificate.certificate.serial}
        persisted = db.scalars(
            select(LearningEvent).where(
                LearningEvent.tenant_id == tenant,
                LearningEvent.person_id == person,
                LearningEvent.course_id == course,
                LearningEvent.kind == "certificate_earned",
                LearningEvent.subject_id == course,
            )
        ).one()
        assert persisted.id == event.id
        assert persisted.detail == {"serial": done.certificate.certificate.serial}
        assert done.completion.completed_at == at
        repeat = recompute_completion(
            db, command.model_copy(update={"now": at + timedelta(days=1)})
        )
        assert not repeat.first_completion and repeat.completion.completed_at == at
        serial = done.certificate.certificate.serial
        _activity(db, tenant, course)
        regressed = recompute_completion(db, command)
        assert (
            regressed.completion.completed_at is None
            and regressed.completion.pct == 3 / 4
        )
        existing = issue_certificate(
            db, CertificateCommand(tenant_id=tenant, person_id=person, course_id=course)
        )
        assert not existing.created and existing.certificate.serial == serial
        assert existing.event is None
        assert existing.certificate.issued_at == at


def test_best_result_query_ties_and_bulk_parity(progress_case):
    engine, tenant, person, course = progress_case
    at = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        a = _activity(db, tenant, course)
        first = _score(db, tenant, person, a, fraction=1 / 3, at=at)
        tied = _score(db, tenant, person, a, attempt=2, fraction=1 / 3, at=at)
        assert first.id != tied.id
        kwargs = {"tenant_id": tenant, "person_id": person}
        assert best_scores_for(db, course_id=course, **kwargs)[a.id].id == first.id
        higher = _score(
            db, tenant, person, a, attempt=3, fraction=0.6, passed=False, at=at
        )
        projected = best_scores_for(db, course_id=course, **kwargs)
        assert projected[a.id].id == higher.id and not projected[a.id].passed
        assert best_scores_for_person(db, course_ids=[course], **kwargs) == projected
        assert best_scores_by_course_for_person(db, course_ids=[course], **kwargs) == {
            course: projected
        }
        assert best_scores_for_person(db, course_ids=[], **kwargs) == {}


def test_refusal_and_missing_course_do_not_fabricate_completion(progress_case):
    engine, tenant, person, course = progress_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        with pytest.raises(CertificateNotEligible):
            issue_certificate(
                db,
                CertificateCommand(
                    tenant_id=tenant, person_id=person, course_id=course
                ),
            )
        assert db.scalar(select(func.count()).select_from(CourseCompletion)) == 0
        with pytest.raises(DBAPIError), db.begin_nested():
            recompute_completion(
                db,
                CompletionCommand(
                    tenant_id=tenant, person_id=person, course_id=uuid4()
                ),
            )
        assert db.scalar(select(func.count()).select_from(CourseCompletion)) == 0


def test_required_certificate_failure_and_owner_rollback(progress_case, monkeypatch):
    engine, tenant, person, course = progress_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        a = _activity(db, tenant, course)
        _score(db, tenant, person, a)
    from dotmac_learning import certificates

    original = certificates._serial
    monkeypatch.setattr(certificates, "_serial", lambda: "x" * 33)
    with Session(engine) as db:
        with pytest.raises(DBAPIError), db.begin():
            _scope(db, tenant)
            recompute_completion(
                db,
                CompletionCommand(tenant_id=tenant, person_id=person, course_id=course),
            )
    monkeypatch.setattr(certificates, "_serial", original)
    with Session(engine) as db:
        transaction = db.begin()
        _scope(db, tenant)
        result = recompute_completion(
            db, CompletionCommand(tenant_id=tenant, person_id=person, course_id=course)
        )
        assert result.first_completion and result.certificate.created
        transaction.rollback()
        with db.begin():
            _scope(db, tenant)
            assert db.scalar(select(func.count()).select_from(CourseCompletion)) == 0
            assert db.scalar(select(func.count()).select_from(Certificate)) == 0


def test_optional_event_sql_failure_preserves_required_certificate(
    progress_case, monkeypatch
):
    engine, tenant, person, course = progress_case
    from dotmac_learning import events

    def broken_record(db, payload, **kwargs):
        db.execute(text("SELECT 1/0"))

    monkeypatch.setattr(events, "record", broken_record)
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        a = _activity(db, tenant, course)
        _score(db, tenant, person, a)
        result = recompute_completion(
            db, CompletionCommand(tenant_id=tenant, person_id=person, course_id=course)
        )
        assert (
            result.certificate.created and result.certificate.event.status == "failed"
        )
        assert db.scalar(text("SELECT 42")) == 42
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1


def test_concurrent_first_recomputes_and_certificate_recheck(progress_case):
    engine, tenant, person, course = progress_case
    command = CompletionCommand(tenant_id=tenant, person_id=person, course_id=course)
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        a = _activity(db, tenant, course)
        _score(db, tenant, person, a)
    entered = Event()

    def contender():
        with Session(engine) as db, db.begin():
            _scope(db, tenant)
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            entered.set()
            return recompute_completion(db, command)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(engine) as db, db.begin():
            _scope(db, tenant)
            winner = recompute_completion(db, command)
            future = pool.submit(contender)
            assert entered.wait(5)
        loser = future.result(timeout=15)
    assert winner.first_completion and not loser.first_completion
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        cert = issue_certificate(
            db, CertificateCommand(tenant_id=tenant, person_id=person, course_id=course)
        )
        assert cert.certificate.id == winner.certificate.certificate.id
        assert not cert.created
        assert db.scalar(select(func.count()).select_from(CourseCompletion)) == 1
        assert db.scalar(select(func.count()).select_from(Certificate)) == 1


def test_concurrent_direct_issuers_recheck_under_real_aggregate_lock(
    progress_case, monkeypatch
):
    engine, tenant, person, course = progress_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=course,
                status="completed",
                pct=1.0,
                completed_at=datetime.now(UTC),
            )
        )
    from dotmac_learning import certificates

    rendezvous = Barrier(2)
    real_lock = certificates.lock_existing_completion

    def synchronized_lock(db, **kwargs):
        rendezvous.wait(timeout=5)
        return real_lock(db, **kwargs)

    monkeypatch.setattr(certificates, "lock_existing_completion", synchronized_lock)

    def issue(_):
        with Session(engine) as db, db.begin():
            _scope(db, tenant)
            db.execute(text("SET LOCAL lock_timeout = '10s'"))
            return issue_certificate(
                db,
                CertificateCommand(
                    tenant_id=tenant, person_id=person, course_id=course
                ),
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(issue, (1, 2)))
    assert results[0].certificate == results[1].certificate
    assert sorted(result.created for result in results) == [False, True]
    creator = next(result for result in results if result.created)
    existing = next(result for result in results if not result.created)
    assert creator.event is not None
    assert creator.event.status == "recorded"
    event = creator.event.event
    assert event is not None
    assert (event.tenant_id, event.person_id, event.course_id, event.subject_id) == (
        tenant,
        person,
        course,
        course,
    )
    assert event.kind == "certificate_earned"
    assert event.detail == {"serial": creator.certificate.serial}
    assert existing.event is None
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        persisted = db.scalars(
            select(LearningEvent).where(
                LearningEvent.tenant_id == tenant,
                LearningEvent.person_id == person,
                LearningEvent.course_id == course,
                LearningEvent.kind == "certificate_earned",
                LearningEvent.subject_id == course,
            )
        ).all()
        assert len(persisted) == 1
        assert persisted[0].id == event.id
        assert persisted[0].detail == {"serial": creator.certificate.serial}


def test_progress_queries_and_certificate_are_tenant_scoped(
    progress_case, learning_database
):
    engine, tenant, person, course = progress_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        a = _activity(db, tenant, course)
        _score(db, tenant, person, a)
        recompute_completion(
            db, CompletionCommand(tenant_id=tenant, person_id=person, course_id=course)
        )
    # A valid independent tenant context must not see the first learner's scores
    # or return their existing certificate, even when given its known IDs.
    other_tenant = uuid4()
    admin = create_engine(learning_database[0])
    try:
        with admin.begin() as conn:
            _tenant(conn, other_tenant)
    finally:
        admin.dispose()
    with Session(engine) as db, db.begin():
        _scope(db, other_tenant)
        assert (
            best_scores_for(db, tenant_id=tenant, person_id=person, course_id=course)
            == {}
        )
        with pytest.raises(CertificateNotEligible):
            issue_certificate(
                db,
                CertificateCommand(
                    tenant_id=tenant, person_id=person, course_id=course
                ),
            )


@pytest.mark.parametrize(
    "historical_json",
    ['[1, null, [false], {"item": "value"}]', "42", '{"legacy": [1]}'],
)
def test_projection_preserves_unconstrained_historical_json(
    progress_case, historical_json
):
    import json

    engine, tenant, person, course = progress_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        a = _activity(db, tenant, course)
        score = _score(db, tenant, person, a)
        db.execute(
            text(
                "UPDATE mod_learning.scores SET per_item=CAST(:value AS jsonb) WHERE "
                "id=:id"
            ),
            {"value": historical_json, "id": score.id},
        )
        db.expire(score)
        fact = best_scores_for(
            db, tenant_id=tenant, person_id=person, course_id=course
        )[a.id]
        assert fact.per_item == json.loads(historical_json)
