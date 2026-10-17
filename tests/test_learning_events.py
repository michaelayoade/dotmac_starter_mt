"""Source-derived event behavior on real migrated PostgreSQL as app_user.

No metadata DDL or provider stamping: reuse the catalog fixture's real lineages.
These tests are written for Git-hosted CI and are not local runtime evidence.
"""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from dotmac_learning import events
from dotmac_learning.event_contracts import EventWrite
from dotmac_learning.models import (
    KIND_ACTIVITY_STARTED,
    KIND_CERTIFICATE_EARNED,
    KIND_CHAPTER_COMPLETED,
    KIND_CHAPTER_VIEWED,
    KIND_COURSE_VIEWED,
    EVENT_KINDS,
    LearningEvent,
)
from tests.test_learning_catalog import (
    _course,
    _person,
    _scope,
    _tenant,
    learning_database as learning_database,
)


@pytest.fixture
def event_database(learning_database):
    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    tenant, other_tenant = uuid4(), uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            _tenant(conn, other_tenant)
            person = _person(conn, tenant)
            other_person = _person(conn, other_tenant)
            organization = _person(
                conn, tenant, subtype=False, party_type="organization"
            )
            malformed_org = _person(conn, tenant, party_type="organization")
            missing_profile = _person(conn, tenant, subtype=False)
            course = _course(conn, tenant)
        yield (
            app,
            tenant,
            person,
            other_tenant,
            other_person,
            organization,
            malformed_org,
            missing_profile,
            course,
        )
    finally:
        app.dispose()
        admin.dispose()


def test_once_subject_null_subject_and_daily_dedupe(event_database) -> None:
    app, tenant, person, _, _, _, _, _, course = event_database
    now = datetime(2026, 10, 17, 22, 30, tzinfo=UTC)
    zone = timezone(timedelta(hours=3))
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db:
        _scope(conn, tenant)
        for kind in (KIND_CHAPTER_COMPLETED, KIND_CERTIFICATE_EARNED):
            payload = EventWrite(
                tenant_id=tenant,
                person_id=person,
                kind=kind,
                course_id=course,
                subject_id=uuid4(),
                occurred_at=now,
                detail={"nested": [None, {"passed": True}]},
            )
            first = events.record(db, payload)
            assert first is not None and first.detail == payload.detail
            assert first.created_at is not None and first.updated_at is not None
            assert events.record(db, payload) is None
            # PostgreSQL's source partial unique index allows repeated NULL subjects.
            null_subject = payload.model_copy(update={"subject_id": None})
            assert events.record(db, null_subject) is not None
            assert events.record(db, null_subject) is not None
        for kind in (KIND_COURSE_VIEWED, KIND_CHAPTER_VIEWED):
            payload = EventWrite(
                tenant_id=tenant,
                person_id=person,
                kind=kind,
                subject_id=uuid4(),
                occurred_at=now,
            )
            with pytest.raises(ValueError, match="local_day_zone"):
                events.record(db, payload)
            assert events.emit(db, payload).status == "failed"
            assert events.emit(db, payload, local_day_zone=zone).status == "recorded"
            assert events.emit(db, payload, local_day_zone=zone).status == "suppressed"
            # Midnight in +03 occurs at 21:00 UTC; this is the following local day.
            following_day = payload.model_copy(
                update={"occurred_at": now + timedelta(hours=23)}
            )
            assert events.record(db, following_day, local_day_zone=zone) is not None


def test_identity_type_and_tenant_refusals(event_database) -> None:
    (
        app,
        tenant,
        person,
        other_tenant,
        other_person,
        organization,
        malformed_org,
        _,
        _,
    ) = event_database
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db:
        _scope(conn, tenant)
        for rejected in (uuid4(), other_person, organization, malformed_org):
            payload = EventWrite(
                tenant_id=tenant, person_id=rejected, kind=KIND_ACTIVITY_STARTED
            )
            with pytest.raises(ValueError, match="person"):
                events.record(db, payload)
            assert events.emit(db, payload).status == "failed"
        # An explicit foreign tenant does not override the caller's RLS context.
        with pytest.raises(ValueError, match="person"):
            events.record(
                db,
                EventWrite(
                    tenant_id=other_tenant,
                    person_id=other_person,
                    kind=KIND_ACTIVITY_STARTED,
                ),
            )
        assert db.scalar(select(func.count()).select_from(LearningEvent)) == 0
        assert (
            events.record(
                db,
                EventWrite(
                    tenant_id=tenant, person_id=person, kind=KIND_ACTIVITY_STARTED
                ),
            )
            is not None
        )


def test_optional_real_fk_failure_keeps_root_usable_and_owner_rollback(
    event_database,
) -> None:
    app, tenant, person, _, _, _, _, missing_profile, _ = event_database
    with app.connect() as conn:
        root = conn.begin()
        with Session(bind=conn) as db:
            _scope(conn, tenant)
            before = events.record(
                db,
                EventWrite(
                    tenant_id=tenant, person_id=person, kind=KIND_ACTIVITY_STARTED
                ),
            )
            assert before is not None
            failed = events.emit(
                db,
                EventWrite(
                    tenant_id=tenant,
                    person_id=missing_profile,
                    kind=KIND_ACTIVITY_STARTED,
                ),
            )
            assert failed.status == "failed" and failed.event is None
            assert db.scalar(
                text("SELECT current_setting('app.current_tenant')")
            ) == str(tenant)
            after = events.emit(
                db,
                EventWrite(
                    tenant_id=tenant, person_id=person, kind=KIND_ACTIVITY_STARTED
                ),
            )
            assert after.status == "recorded" and after.event is not None
            assert db.scalar(select(func.count()).select_from(LearningEvent)) == 2
            root.rollback()
        with conn.begin():
            _scope(conn, tenant)
            assert conn.scalar(select(func.count()).select_from(LearningEvent)) == 0


def test_required_fk_error_propagates_for_owner_rollback(event_database) -> None:
    app, tenant, _, _, _, _, _, missing_profile, _ = event_database
    with app.connect() as conn:
        root = conn.begin()
        try:
            with Session(bind=conn) as db:
                _scope(conn, tenant)
                with pytest.raises(DBAPIError) as caught:
                    events.record(
                        db,
                        EventWrite(
                            tenant_id=tenant,
                            person_id=missing_profile,
                            kind=KIND_ACTIVITY_STARTED,
                        ),
                    )
                assert getattr(caught.value.orig, "sqlstate", None) == "23503"
        finally:
            root.rollback()
        with conn.begin():
            _scope(conn, tenant)
            assert conn.scalar(select(func.count()).select_from(LearningEvent)) == 0


def test_primitive_readers_keep_source_windows_filters_and_tenant_isolation(
    event_database,
) -> None:
    app, tenant, person, other_tenant, other_person, _, _, _, course = event_database
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db:
        _scope(conn, other_tenant)
        events.record(
            db,
            EventWrite(
                tenant_id=other_tenant,
                person_id=other_person,
                kind=KIND_ACTIVITY_STARTED,
                course_id=course,
                occurred_at=now,
            ),
        )
        _scope(conn, tenant)
        assert events.last_activity_at(db, tenant_id=tenant, person_id=person) is None
        for weeks_ago, n in ((1, 3), (2, 1)):
            for i in range(n):
                events.record(
                    db,
                    EventWrite(
                        tenant_id=tenant,
                        person_id=person,
                        kind=KIND_ACTIVITY_STARTED,
                        course_id=course,
                        subject_id=uuid4(),
                        occurred_at=now - timedelta(weeks=weeks_ago, hours=i + 1),
                    ),
                )
        events.record(
            db,
            EventWrite(
                tenant_id=tenant,
                person_id=person,
                kind=KIND_CHAPTER_COMPLETED,
                occurred_at=now - timedelta(days=30),
            ),
        )
        buckets = events.weekly_buckets(
            db,
            tenant_id=tenant,
            person_id=person,
            weeks=4,
            kinds=(KIND_ACTIVITY_STARTED,),
            now=now,
        )
        assert [bucket.count for bucket in buckets] == [0, 0, 1, 3]
        assert buckets[0].week_start == (now - timedelta(weeks=4)).date()
        assert events.last_activity_at(
            db, tenant_id=tenant, person_id=person
        ) == now - timedelta(weeks=1, hours=1)
        assert events.active_person_ids(
            db,
            tenant_id=tenant,
            person_ids=[person, other_person],
            since=now - timedelta(days=10),
        ) == {person}
        assert (
            events.active_person_ids(db, tenant_id=tenant, person_ids=[], since=now)
            == set()
        )
        args = {"tenant_id": tenant, "kinds": (KIND_ACTIVITY_STARTED,)}
        assert events.kind_counts(db, **args) == {course: {KIND_ACTIVITY_STARTED: 4}}
        assert events.kind_counts(db, **args, person_ids=[]) == {}
        # Source empty course_ids means no course filter; empty roster means nobody.
        assert events.kind_counts(db, **args, course_ids=[]) == {
            course: {KIND_ACTIVITY_STARTED: 4}
        }
        assert events.kind_counts(db, **args, course_ids=[uuid4()]) == {}
        assert events.kind_counts(
            db, **args, person_ids=[person], since=now - timedelta(days=10)
        ) == {course: {KIND_ACTIVITY_STARTED: 3}}
        assert (
            events.kind_counts(
                db, tenant_id=other_tenant, kinds=(KIND_ACTIVITY_STARTED,)
            )
            == {}
        )


def test_unthrottled_kinds_repeat_and_null_view_subject_is_throttled(
    event_database,
) -> None:
    app, tenant, person, _, _, _, _, _, _ = event_database
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    throttled = {
        KIND_CHAPTER_COMPLETED,
        KIND_CERTIFICATE_EARNED,
        KIND_CHAPTER_VIEWED,
        KIND_COURSE_VIEWED,
    }
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db:
        _scope(conn, tenant)
        for kind in EVENT_KINDS:
            if kind in throttled:
                continue
            payload = EventWrite(
                tenant_id=tenant, person_id=person, kind=kind, occurred_at=now
            )
            first = events.record(db, payload)
            second = events.record(db, payload)
            assert first is not None and second is not None and first.id != second.id
            assert first.subject_id is None and first.course_id is None
            assert first.occurred_at == now and first.detail == {}
        view = EventWrite(
            tenant_id=tenant, person_id=person, kind=KIND_COURSE_VIEWED, occurred_at=now
        )
        assert events.record(db, view, local_day_zone=UTC) is not None
        assert events.record(db, view, local_day_zone=UTC) is None


def test_unset_tenant_context_refuses_event_before_append(event_database) -> None:
    app, tenant, person, _, _, _, _, _, _ = event_database
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db:
        with pytest.raises(ValueError, match="person"):
            events.record(
                db,
                EventWrite(
                    tenant_id=tenant, person_id=person, kind=KIND_ACTIVITY_STARTED
                ),
            )
        assert db.scalar(select(func.count()).select_from(LearningEvent)) == 0


def test_required_pending_sql_failure_propagates_before_optional_savepoint(
    event_database,
) -> None:
    from sqlalchemy.exc import PendingRollbackError

    app, tenant, person, _, _, _, _, missing_profile, _ = event_database
    with app.connect() as conn, Session(bind=conn) as db:
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        # This pending row belongs to the required host operation, not emit.
        db.add(
            LearningEvent(
                tenant_id=tenant,
                person_id=missing_profile,
                kind=KIND_ACTIVITY_STARTED,
                detail={},
                occurred_at=datetime(2026, 10, 17, 12, tzinfo=UTC),
            )
        )
        with pytest.raises(DBAPIError) as caught:
            events.emit(
                db,
                EventWrite(
                    tenant_id=tenant, person_id=person, kind=KIND_ACTIVITY_STARTED
                ),
            )
        assert getattr(caught.value.orig, "sqlstate", None) == "23503"
        assert not db.is_active
        with pytest.raises(PendingRollbackError):
            db.scalar(select(func.count()).select_from(LearningEvent))
        # The caller must roll back its failed root before continuing.
        db.rollback()
        db.execute(
            text("SELECT set_config('app.current_tenant', :tenant, true)"),
            {"tenant": str(tenant)},
        )
        assert db.scalar(select(func.count()).select_from(LearningEvent)) == 0
        assert (
            events.emit(
                db,
                EventWrite(
                    tenant_id=tenant, person_id=person, kind=KIND_ACTIVITY_STARTED
                ),
            ).status
            == "recorded"
        )
        db.rollback()


@pytest.mark.parametrize("detail", [None, "legacy", 7, [None, {"legacy": True}]])
def test_historical_event_facts_preserve_unconstrained_jsonb(
    event_database, detail
) -> None:
    import json

    app, tenant, person, _, _, _, _, _, _ = event_database
    with app.connect() as conn, conn.begin(), Session(bind=conn) as db:
        _scope(conn, tenant)
        event_id = uuid4()
        conn.execute(
            text(
                "INSERT INTO mod_learning.learning_events "
                "(id, tenant_id, person_id, kind, detail, occurred_at) "
                "VALUES (:id, :tenant, :person, :kind, CAST(:detail AS jsonb), now())"
            ),
            {
                "id": event_id,
                "tenant": tenant,
                "person": person,
                "kind": KIND_ACTIVITY_STARTED,
                "detail": json.dumps(detail),
            },
        )
        event = db.get(LearningEvent, event_id)
        assert event is not None
        fact = events._fact(event)
        assert fact.detail == detail
        if isinstance(detail, list):
            assert fact.detail is not event.detail
