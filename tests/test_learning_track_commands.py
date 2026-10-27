"""Hosted PostgreSQL track parity; genuine Kernel identity, caller transactions."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from dotmac_learning.models import Activity, Course, CourseCompletion
from dotmac_learning.participation_contracts import (
    ParticipationBadRequest,
    ParticipationNotFound,
)
from dotmac_learning.participation_models import (
    Cohort,
    CohortTrack,
    CourseOffering,
    Enrollment,
    Track,
    TrackCourse,
)
from dotmac_learning.result_contracts import TransactionChanged
from dotmac_learning.tracks import (
    AddTrackCoursesCommand,
    CreateTrackCommand,
    ReorderTrackCoursesCommand,
    add_courses_to_cohort_track,
    assign_enrollment_track,
    clear_enrollment_track,
    cohort_track_or_404,
    create_cohort_track,
    default_track_for_cohort,
    ensure_default_track_for_cohort,
    ensure_track_offerings,
    list_cohort_tracks,
    reorder_track_courses,
    tracks_for_cohorts,
)
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_participation_catalog as participation_catalog
from tests.test_learning_catalog import _course, _person, _scope, _tenant

learning_database = participation_catalog.learning_database


@pytest.fixture
def track_case(learning_database):
    admin, actor = (create_engine(url) for url in learning_database)
    tenant, other = uuid4(), uuid4()
    try:
        with admin.begin() as conn:
            for tid in (tenant, other):
                _tenant(conn, tid)
            person = _person(conn, tenant)
        with actor.begin() as conn:
            _scope(conn, tenant)
            courses = tuple(_course(conn, tenant) for _ in range(4))
            cohort = uuid4()
            conn.execute(
                Cohort.__table__.insert().values(
                    id=cohort, tenant_id=tenant, name="Cohort", discipline="engineering"
                )
            )
            _scope(conn, other)
            foreign_course = _course(conn, other)
        yield actor, tenant, other, person, cohort, courses, foreign_course
    finally:
        actor.dispose()
        admin.dispose()


def _create(db, tenant, cohort, courses, **kw):
    kw.setdefault("gating_mode", "previous")
    kw.setdefault("pacing_default_open", True)
    return create_cohort_track(
        db,
        CreateTrackCommand(
            tenant_id=tenant, cohort_id=cohort, name="Track", course_ids=courses, **kw
        ),
    )


def test_track_cross_tenant_course_refusal_and_reads(track_case):
    engine, tenant, other, _, cohort, courses, foreign = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        with pytest.raises(ParticipationNotFound, match="one or more courses"):
            _create(db, tenant, cohort, (courses[0], foreign))
        track = _create(db, tenant, cohort, courses[:1])
        assert tracks_for_cohorts(db, tenant_id=other, cohort_ids=[cohort]) == {
            cohort: ()
        }
        with pytest.raises(ParticipationNotFound):
            cohort_track_or_404(
                db, tenant_id=other, cohort_id=cohort, track_id=track.id
            )
        with pytest.raises(ParticipationNotFound):
            clear_enrollment_track(
                db, tenant_id=other, cohort_id=cohort, person_id=uuid4()
            )


def test_dedupe_position_ties_dense_create_gapped_add_exact_reorder(track_case):
    engine, tenant, _, _, cohort, (a, b, c, d), _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        track = _create(db, tenant, cohort, (a, b, a, c), positions={a: 9, b: 9})
        rows = db.scalars(select(TrackCourse).order_by(TrackCourse.order_index)).all()
        assert [(r.course_id, r.order_index) for r in rows] == [(a, 1), (b, 2), (c, 3)]
        rows[-1].order_index = 20
        db.flush()
        add_courses_to_cohort_track(
            db,
            AddTrackCoursesCommand(
                tenant_id=tenant,
                cohort_id=cohort,
                track_id=track.id,
                course_ids=(b, d, d),
                pacing_default_open=True,
            ),
        )
        assert (
            db.scalar(select(TrackCourse.order_index).where(TrackCourse.course_id == d))
            == 21
        )
        assert (
            db.scalar(select(TrackCourse.order_index).where(TrackCourse.course_id == c))
            == 20
        )
        for invalid in ((a, a, c, d), (a, b, c)):
            with pytest.raises(ParticipationBadRequest):
                reorder_track_courses(
                    db,
                    ReorderTrackCoursesCommand(
                        tenant_id=tenant, track_id=track.id, ordered_course_ids=invalid
                    ),
                )
        reorder_track_courses(
            db,
            ReorderTrackCoursesCommand(
                tenant_id=tenant, track_id=track.id, ordered_course_ids=(d, c, b, a)
            ),
        )
        assert db.scalars(
            select(TrackCourse.course_id).order_by(TrackCourse.order_index)
        ).all() == [d, c, b, a]
        with pytest.raises(FrozenInstanceError):
            track.name = "changed"


def test_draft_zero_activity_live_warning_and_status_asymmetry(track_case):
    engine, tenant, _, _, cohort, courses, _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        db.get(Course, courses[0]).status = "draft"
        track = _create(db, tenant, cohort, courses[:2])
        activity = Activity(
            assessment_mode="graded",
            grading="auto",
            pass_threshold=0.0,
            type="mcq_test",
            weight=1.0,
            tenant_id=tenant,
            course_id=courses[0],
            title="Quiz",
        )
        db.add(activity)
        db.flush()
        assert list_cohort_tracks(db, tenant_id=tenant, cohort_id=cohort)[
            0
        ].zero_activity_course_ids == frozenset(courses[1:2])
        db.delete(activity)
        db.get(Track, track.id).status = "inactive"
        db.flush()
        assert list_cohort_tracks(db, tenant_id=tenant, cohort_id=cohort)[
            0
        ].zero_activity_course_ids == frozenset(courses[:2])
        assert tracks_for_cohorts(db, tenant_id=tenant, cohort_ids=[cohort]) == {
            cohort: ()
        }
        assert default_track_for_cohort(db, tenant_id=tenant, cohort_id=cohort) is None
        with pytest.raises(ParticipationNotFound):
            ensure_track_offerings(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                track_id=track.id,
                pacing_default_open=True,
            )


def test_assignment_refusal_keeps_source_flushed_offerings_until_caller_rollback(
    track_case,
):
    engine, tenant, _, person, cohort, courses, _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        track = _create(db, tenant, cohort, courses[:1])
    with Session(engine) as db:
        db.begin()
        _scope(db, tenant)
        offering = db.scalar(select(CourseOffering))
        offering.status = "inactive"
        edge = datetime(2026, 1, 1, tzinfo=UTC)
        offering.starts_at = offering.ends_at = edge
        db.flush()
        with pytest.raises(ParticipationNotFound, match="enrollment not found"):
            assign_enrollment_track(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=person,
                track_id=track.id,
                pacing_default_open=True,
            )
        assert (
            offering.status == "active"
            and offering.starts_at == offering.ends_at == edge
        )
        db.rollback()
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        assert db.scalar(select(CourseOffering.starts_at)) is None
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=person,
                track_id=track.id,
                status="dropped",
                role_in_cohort="instructor",
            )
        )
        db.flush()
        cleared = clear_enrollment_track(
            db, tenant_id=tenant, cohort_id=cohort, person_id=person
        )
        assert (
            cleared.track_id is None
            and cleared.status == "dropped"
            and cleared.role_in_cohort == "instructor"
        )
        assert db.scalar(select(CourseCompletion)) is None


def test_track_requires_root_before_autoflush_and_preserves_nested_boundary(track_case):
    engine, tenant, _, _, cohort, courses, _ = track_case
    with Session(engine) as db:
        with pytest.raises(TransactionChanged):
            _create(db, tenant, cohort, courses[:1])
        assert db.get_transaction() is None
        with db.begin():
            _scope(db, tenant)
            root = db.get_transaction()
            with db.begin_nested():
                nested = db.get_nested_transaction()
                _create(db, tenant, cohort, courses[:1])
                assert (
                    db.get_transaction() is root
                    and db.get_nested_transaction() is nested
                )
        with db.begin():
            _scope(db, tenant)
            db.add(Cohort(tenant_id=tenant, name="invalid", discipline=None))
            with pytest.raises(IntegrityError):
                _create(db, tenant, cohort, courses[:1])
            assert not db.is_active


def test_default_track_created_at_then_name_and_empty_creation(track_case):
    engine, tenant, _, _, cohort, courses, _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        empty = ensure_default_track_for_cohort(db, tenant_id=tenant, cohort_id=cohort)
        assert db.scalar(select(TrackCourse)) is None
        assert (
            ensure_default_track_for_cohort(db, tenant_id=tenant, cohort_id=cohort)
            == empty
        )
        second = _create(db, tenant, cohort, courses[:1])
        stamp = datetime(2026, 1, 1, tzinfo=UTC)
        first_row, second_row = db.get(Track, empty.id), db.get(Track, second.id)
        first_row.created_at = second_row.created_at = stamp
        first_row.name, second_row.name = "Z", "A"
        db.flush()
        assert (
            default_track_for_cohort(db, tenant_id=tenant, cohort_id=cohort).id
            == second.id
        )
        db.scalar(
            select(CohortTrack).where(CohortTrack.track_id == second.id)
        ).status = "inactive"
        db.flush()
        assert (
            default_track_for_cohort(db, tenant_id=tenant, cohort_id=cohort).id
            == empty.id
        )


def test_track_commands_forbid_unknown_fields():
    with pytest.raises(ValidationError):
        CreateTrackCommand(
            tenant_id=uuid4(),
            cohort_id=uuid4(),
            name="Track",
            course_ids=(),
            gating_mode="previous",
            pacing_default_open=True,
            arbitrary=True,
        )


def test_slug_read_before_write_race_propagates_unique_failure(track_case, monkeypatch):
    """Both readers choose the same slug; the loser requires caller rollback."""
    from dotmac_learning import tracks

    engine, tenant, _, _, cohort, courses, _ = track_case
    barrier = Barrier(2)
    original = tracks._unique_track_slug

    def synchronized_slug(db, *, tenant_id, name):
        selected = original(db, tenant_id=tenant_id, name=name)
        barrier.wait(timeout=15)
        return selected

    monkeypatch.setattr(tracks, "_unique_track_slug", synchronized_slug)

    def write():
        with Session(engine) as db:
            try:
                with db.begin():
                    _scope(db, tenant)
                    result = _create(db, tenant, cohort, courses[:1])
                return ("created", result.slug)
            except IntegrityError as exc:
                assert db.get_transaction() is None
                return ("conflict", getattr(exc.orig, "sqlstate", None))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(results) == [("conflict", "23505"), ("created", "track")]
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        assert len(db.scalars(select(Track)).all()) == 1
        assert len(db.scalars(select(CohortTrack)).all()) == 1
        assert len(db.scalars(select(CourseOffering)).all()) == 1


@pytest.mark.parametrize(
    "operation", ["create", "add", "reorder", "ensure", "assign", "clear", "default"]
)
def test_every_track_mutator_refuses_missing_caller_transaction_before_sql(
    track_case, operation
):
    engine, tenant, _, person, cohort, courses, _ = track_case
    track_id = uuid4()
    with Session(engine) as db:
        calls = {
            "create": lambda: _create(db, tenant, cohort, courses[:1]),
            "add": lambda: add_courses_to_cohort_track(
                db,
                AddTrackCoursesCommand(
                    tenant_id=tenant,
                    cohort_id=cohort,
                    track_id=track_id,
                    course_ids=courses[:1],
                    pacing_default_open=True,
                ),
            ),
            "reorder": lambda: reorder_track_courses(
                db,
                ReorderTrackCoursesCommand(
                    tenant_id=tenant, track_id=track_id, ordered_course_ids=()
                ),
            ),
            "ensure": lambda: ensure_track_offerings(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                track_id=track_id,
                pacing_default_open=True,
            ),
            "assign": lambda: assign_enrollment_track(
                db,
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=person,
                track_id=track_id,
                pacing_default_open=True,
            ),
            "clear": lambda: clear_enrollment_track(
                db, tenant_id=tenant, cohort_id=cohort, person_id=person
            ),
            "default": lambda: ensure_default_track_for_cohort(
                db, tenant_id=tenant, cohort_id=cohort
            ),
        }
        with pytest.raises(TransactionChanged):
            calls[operation]()
        assert db.get_transaction() is None


def test_create_track_requires_closed_gating_mode():
    with pytest.raises(ValidationError):
        CreateTrackCommand(
            tenant_id=uuid4(),
            cohort_id=uuid4(),
            name="Track",
            course_ids=(),
            pacing_default_open=True,
        )
    with pytest.raises(ValidationError):
        CreateTrackCommand(
            tenant_id=uuid4(),
            cohort_id=uuid4(),
            name="Track",
            course_ids=(),
            gating_mode="sometimes",
            pacing_default_open=True,
        )
    for mode in ("none", "previous", "all_previous"):
        command = CreateTrackCommand(
            tenant_id=uuid4(),
            cohort_id=uuid4(),
            name="Track",
            course_ids=(),
            gating_mode=mode,
            pacing_default_open=True,
        )
        assert command.gating_mode == mode
