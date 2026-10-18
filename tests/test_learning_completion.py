"""Stored completion reads on real migrated PostgreSQL; hosted CI only."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import dotmac_learning as learning
import pytest
from dotmac_learning.models import Certificate, Course, CourseCompletion, LearningEvent
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from tests import test_learning_progress as progress
from tests.test_learning_catalog import _scope, _tenant

learning_database = progress.learning_database
progress_case = progress.progress_case


def _stored(db, tenant, person, course, *, status="completed", pct=0.25):
    row = CourseCompletion(
        tenant_id=tenant,
        person_id=person,
        course_id=course,
        status=status,
        pct=pct,
        completed_at=datetime(2026, 1, 2, tzinfo=UTC)
        if status == "completed"
        else None,
    )
    db.add(row)
    db.flush()
    return row


def _counts(db):
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (CourseCompletion, Certificate, LearningEvent)
    )


def test_get_completion_missing_and_exact_person_course_filters(progress_case):
    engine, tenant, person, course = progress_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        assert (
            learning.get_completion(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            is None
        )
        assert _counts(db) == (0, 0, 0)
        row = _stored(db, tenant, person, course)
        expected = learning.CompletionFact(
            id=row.id,
            tenant_id=row.tenant_id,
            person_id=row.person_id,
            course_id=row.course_id,
            status=row.status,
            pct=row.pct,
            completed_at=row.completed_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        db.expunge_all()
        fact = learning.get_completion(
            db, tenant_id=tenant, person_id=person, course_id=course
        )
        assert fact == expected
        assert (
            learning.get_completion(
                db, tenant_id=tenant, person_id=uuid4(), course_id=course
            )
            is None
        )
        assert (
            learning.get_completion(
                db, tenant_id=tenant, person_id=person, course_id=uuid4()
            )
            is None
        )
        # A detached frozen fact carries all timestamps after the Session is closed.
        with pytest.raises(FrozenInstanceError):
            fact.status = "changed"
    assert fact == expected


@pytest.mark.parametrize(
    "status, pct", [("completed", 0.0), ("in_progress", 1.0), ("Completed", 1.0)]
)
def test_get_completion_preserves_status_without_progress_or_visibility_policy(
    progress_case, status, pct
):
    engine, tenant, person, course = progress_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        # No activities/scores exist, and the course is not publicly visible.
        course_row = db.get(Course, course)
        course_row.status = "draft"
        course_row.listed = False
        _stored(db, tenant, person, course, status=status, pct=pct)
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        root, nested = db.get_transaction(), db.get_nested_transaction()
        before = _counts(db)
        fact = learning.get_completion(
            db, tenant_id=tenant, person_id=person, course_id=course
        )
        assert fact is not None and fact.status == status and fact.pct == pct
        assert (fact.status == "completed") is (status == "completed")
        assert (
            learning.get_completion(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            == fact
        )
        assert _counts(db) == before == (1, 0, 0)
        assert db.get_transaction() is root and db.get_nested_transaction() is nested
        assert not db.new and not db.dirty and not db.deleted


def test_get_completion_respects_explicit_tenant_and_rls_context(
    progress_case, learning_database
):
    engine, tenant, person, course = progress_case
    other_tenant = uuid4()
    admin = create_engine(learning_database[0])
    try:
        with admin.begin() as conn:
            _tenant(conn, other_tenant)
    finally:
        admin.dispose()
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _stored(db, tenant, person, course)
        assert (
            learning.get_completion(
                db, tenant_id=other_tenant, person_id=person, course_id=course
            )
            is None
        )
    with Session(engine) as db, db.begin():
        _scope(db, other_tenant)
        assert (
            learning.get_completion(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            is None
        )
        assert _counts(db) == (0, 0, 0)


def test_get_completion_does_not_lock_aggregate(progress_case):
    engine, tenant, person, course = progress_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _stored(db, tenant, person, course)
    # A separate connection holds a conflicting aggregate lock throughout the read.
    with Session(engine) as owner, owner.begin():
        _scope(owner, tenant)
        owner.scalars(select(CourseCompletion).with_for_update()).one()
        with Session(engine) as reader, reader.begin():
            _scope(reader, tenant)
            reader.execute(text("SET LOCAL lock_timeout = '250ms'"))
            fact = learning.get_completion(
                reader, tenant_id=tenant, person_id=person, course_id=course
            )
            assert fact is not None and fact.status == "completed"


def test_get_completion_reports_regression_while_existing_certificate_is_retained(
    progress_case,
):
    engine, tenant, person, course = progress_case
    command = learning.CompletionCommand(
        tenant_id=tenant, person_id=person, course_id=course
    )
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        activity = progress._activity(db, tenant, course)
        progress._score(db, tenant, person, activity)
        completed = learning.recompute_completion(db, command)
        assert completed.certificate is not None and completed.certificate.created
        certificate = completed.certificate.certificate
        progress._activity(db, tenant, course)
        regressed = learning.recompute_completion(db, command)
        assert regressed.completion.status == "in_progress"
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        before = _counts(db)
        fact = learning.get_completion(
            db, tenant_id=tenant, person_id=person, course_id=course
        )
        assert fact == regressed.completion
        assert fact.status != "completed" and fact.completed_at is None
        assert _counts(db) == before == (1, 1, 1)
        existing = learning.issue_certificate(
            db,
            learning.CertificateCommand(
                tenant_id=tenant, person_id=person, course_id=course
            ),
        )
        assert not existing.created and existing.certificate == certificate
        assert (
            learning.get_completion(
                db, tenant_id=tenant, person_id=person, course_id=course
            )
            == fact
        )
        assert _counts(db) == before
