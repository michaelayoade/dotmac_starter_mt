"""Hosted-only real migrated Kernel/Learning PostgreSQL staging qualification.

Reuse restricted app_user and Kernel tenant fixtures; never create metadata,
substitute SQLite, stamp migrations or bypass RLS. Written, not run locally.
"""

from uuid import UUID, uuid4

import pytest
from dotmac_learning import (
    ActivityCreate,
    create_activity_identity,
    stage_activity_patch,
)
from dotmac_learning.models import Activity, Course
from dotmac_learning.references import ActivityPatch
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, PendingRollbackError
from sqlalchemy.orm import Session, defer

from tests import test_learning_reference_patches as reference_cases
from tests.test_learning_catalog import _scope

learning_database = reference_cases.learning_database
reference_database = reference_cases.reference_database


def _payload(tenant, course):
    return ActivityCreate(
        tenant_id=tenant,
        course_id=course,
        title="New lab",
        type="lab",
        pass_threshold=0.7,
        assessment_mode="graded",
        grading="auto",
        weight=1,
    )


@pytest.mark.parametrize("autoflush", [True, False])
def test_identity_flushes_unrelated_dirty_rows_and_insert_default_in_same_root(
    reference_database, autoflush
):
    app, tenant, _, course, _, _ = reference_database
    with Session(app, autoflush=autoflush) as db:
        with db.begin() as outer:
            _scope(db.connection(), tenant)
            assert db.scalar(text("SELECT current_user")) == "app_user"
            existing = db.scalar(select(Course).where(Course.id == course))
            existing.title = "Caller dirty"
            root = db.get_transaction()
            events = []
            added = []

            def attached(session, row):
                if isinstance(row, Activity):
                    assert row.id is None
                    added.append(row)

            def flushing(session, context, instances):
                events.append((session, instances))

            event.listen(db, "after_attach", attached)
            event.listen(db, "before_flush", flushing)
            try:
                identity = create_activity_identity(db, _payload(tenant, course))
                assert isinstance(identity, UUID) and identity == added[0].id
                assert events == [(db, None)]
                assert existing not in db.dirty
                assert db.get_transaction() is root
                db.expire(existing, ["title"])
                assert existing.title == "Caller dirty"
            finally:
                event.remove(db, "after_attach", attached)
                event.remove(db, "before_flush", flushing)
            outer.rollback()
    with Session(app) as db, db.begin():
        _scope(db.connection(), tenant)
        assert db.scalar(select(Activity).where(Activity.id == identity)) is None
        assert db.scalar(select(Course.title).where(Course.id == course)) == "Course"


@pytest.mark.parametrize("autoflush", [True, False])
def test_stage_one_ordinary_select_autoflush_and_no_unused_deferred_or_expired_reads(
    reference_database, autoflush
):
    app, tenant, _, course, activity, _ = reference_database
    with Session(app, autoflush=autoflush) as db:
        with db.begin() as outer:
            _scope(db.connection(), tenant)
            row = db.scalar(
                select(Activity)
                .where(Activity.id == activity)
                .options(
                    defer(Activity.grading, raiseload=True),
                    defer(Activity.weight, raiseload=True),
                )
            )
            # Expired unused attributes must not induce a second SELECT either.
            db.expire(row, ["created_at", "updated_at"])
            caller = db.scalar(select(Course).where(Course.id == course))
            caller.title = "Caller staged"
            root = db.get_transaction()
            flushes, statements = [], []

            def flushing(session, context, instances):
                flushes.append(session)

            def executing(execute_state):
                if execute_state.is_select:
                    statements.append(execute_state.statement)

            event.listen(db, "before_flush", flushing)
            event.listen(db, "do_orm_execute", executing)
            try:
                assert (
                    stage_activity_patch(
                        db,
                        ActivityPatch(
                            id=activity,
                            tenant_id=tenant,
                            title="Changed",
                            chapter_number=None,
                            pass_threshold=0,
                        ),
                    )
                    is True
                )
                assert len(statements) == 1
                assert flushes == ([db] if autoflush else [])
                assert row in db.dirty and row.title == "Changed"
                assert (caller in db.dirty) is (not autoflush)
                assert db.get_transaction() is root
                assert not db.in_nested_transaction()
                db.flush()
                assert len(flushes) == (2 if autoflush else 1)
            finally:
                event.remove(db, "before_flush", flushing)
                event.remove(db, "do_orm_execute", executing)
            outer.rollback()
    with Session(app) as db, db.begin():
        _scope(db.connection(), tenant)
        assert (
            db.scalar(select(Activity.title).where(Activity.id == activity))
            == "Original"
        )
        assert db.scalar(select(Course.title).where(Course.id == course)) == "Course"


def test_stage_missing_and_other_tenant_rls_never_mutates(reference_database):
    app, tenant, other, _, activity, _ = reference_database
    for scope in (tenant, other):
        with Session(app) as db, db.begin():
            _scope(db.connection(), scope)
            for requested_tenant, identity in ((tenant, uuid4()), (other, activity)):
                assert (
                    stage_activity_patch(
                        db,
                        ActivityPatch(
                            id=identity, tenant_id=requested_tenant, title="Wrong"
                        ),
                    )
                    is False
                )
            if scope == other:
                assert (
                    stage_activity_patch(
                        db, ActivityPatch(id=activity, tenant_id=tenant, title="Wrong")
                    )
                    is False
                )


def test_identity_wrong_tenant_insert_fails_in_caller_root(reference_database):
    app, tenant, other, course, _, _ = reference_database
    with Session(app) as db:
        db.begin()
        _scope(db.connection(), other)
        root = db.get_transaction()
        with pytest.raises(DBAPIError):
            create_activity_identity(db, _payload(tenant, course))
        assert db.get_transaction() is root and not db.is_active
        assert not db.in_nested_transaction()
        db.rollback()


def test_identity_caller_flush_failure_requires_root_rollback(
    reference_database,
):
    app, tenant, _, course, _, _ = reference_database
    with Session(app, autoflush=False) as db:
        db.begin()
        _scope(db.connection(), tenant)
        caller = db.scalar(select(Course).where(Course.id == course))
        caller.title = None
        root = db.get_transaction()
        with pytest.raises(IntegrityError):
            create_activity_identity(db, _payload(tenant, course))
        assert db.get_transaction() is root and not db.is_active
        with pytest.raises(PendingRollbackError):
            db.scalar(select(Course))
        db.rollback()
    with Session(app) as db, db.begin():
        _scope(db.connection(), tenant)
        assert db.scalar(select(Course.title).where(Course.id == course)) == "Course"
        assert db.scalar(select(Activity).where(Activity.title == "New lab")) is None
