"""Source entitlement/window/order parity on hosted migrated PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotmac_kernel.models import Party
from dotmac_learning.entitlements import (
    CourseAccessDenied,
    accessible_course_ids,
    course_access_states,
    open_course_ids,
    person_can_access_course,
    require_course_access,
    require_course_open,
    unmet_prerequisites,
    visible_course_ids,
)
from dotmac_learning.models import Course, CourseCompletion
from dotmac_learning.participation_models import (
    Cohort,
    CohortTrack,
    CourseOffering,
    CoursePrerequisite,
    Enrollment,
    Track,
)
from dotmac_learning.tracks import CreateTrackCommand, create_cohort_track
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests import test_learning_track_commands as track_commands
from tests.test_learning_catalog import _scope

track_case = track_commands.track_case
learning_database = track_commands.learning_database


def _setup(db, tenant, person, cohort, courses):
    track = create_cohort_track(
        db,
        CreateTrackCommand(
            tenant_id=tenant, cohort_id=cohort, name="Access", course_ids=courses
        ),
    )
    enrollment = Enrollment(
        tenant_id=tenant,
        cohort_id=cohort,
        person_id=person,
        track_id=track.id,
        role_in_cohort="instructor",
        status="active",
    )
    db.add(enrollment)
    db.flush()
    return track, enrollment


def test_entitlements_cross_tenant_canary(track_case):
    engine, tenant, other, person, cohort, courses, foreign = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _setup(db, tenant, person, cohort, courses[:2])
        assert visible_course_ids(db, tenant_id=other, person_id=person) == set()
        assert open_course_ids(db, tenant_id=other, person_id=person) == set()
        assert visible_course_ids(db, tenant_id=tenant, person_id=uuid4()) == set()
        assert foreign not in visible_course_ids(db, tenant_id=tenant, person_id=person)
        with pytest.raises(CourseAccessDenied) as refusal:
            require_course_access(
                db, tenant_id=other, person_id=person, course_id=courses[0]
            )
        assert refusal.value.detail == "Forbidden"


def test_selected_membership_eligible_sequence_and_no_extra_status_filters(track_case):
    engine, tenant, _, person, cohort, (a, b, c, d), _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        track, enrollment = _setup(db, tenant, person, cohort, (a, b, c))
        db.get(Course, a).status = "draft"
        db.get(Course, b).title = "Eligible previous"
        db.get(Course, c).status = "completed"
        db.get(Track, track.id).status = "inactive"
        db.get(Cohort, cohort).status = "inactive"
        db.get(Party, person).is_active = False
        db.scalar(select(CohortTrack)).status = "inactive"
        db.add(CourseOffering(tenant_id=tenant, cohort_id=cohort, course_id=d))
        db.flush()
        kwargs = {"tenant_id": tenant, "person_id": person}
        assert visible_course_ids(db, **kwargs) == {b, c}
        states = course_access_states(db, **kwargs)
        assert not states[b].locked and states[c].locked_until_course_id == b
        assert (
            states[c].locked_reason
            == 'Complete "Eligible previous" to unlock this course.'
        )
        assert accessible_course_ids(db, **kwargs) == {b}
        assert person_can_access_course(db, course_id=b, **kwargs)
        enrollment.track_id = None
        db.flush()
        assert accessible_course_ids(db, **kwargs) == {b, c, d}
        enrollment.status = "dropped"
        db.flush()
        assert visible_course_ids(db, **kwargs) == set()


def test_any_unlocked_route_then_explicit_prerequisites_duplicate_unmet_source_order(
    track_case,
):
    engine, tenant, _, person, cohort, (a, b, c, _), _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _, enrollment = _setup(db, tenant, person, cohort, (a, b))
        db.get(Course, a).title = "First"
        db.get(Course, c).title = "Explicit"
        db.add(CoursePrerequisite(tenant_id=tenant, course_id=b, requires_course_id=a))
        db.flush()
        kwargs = {"tenant_id": tenant, "person_id": person}
        assert unmet_prerequisites(db, course_id=b, **kwargs) == [a, a]
        # A legacy cohort is a second route that unlocks sequence B.
        legacy = Cohort(tenant_id=tenant, name="Legacy", discipline="engineering")
        db.add(legacy)
        db.flush()
        db.add_all(
            [
                Enrollment(
                    tenant_id=tenant,
                    cohort_id=legacy.id,
                    person_id=person,
                    role_in_cohort="student",
                    status="active",
                ),
                CourseOffering(tenant_id=tenant, cohort_id=legacy.id, course_id=b),
            ]
        )
        db.flush()
        # Explicit prerequisite still locks after the unlocked legacy route wins.
        assert course_access_states(db, **kwargs)[b].locked_until_course_id == a
        prerequisite = db.scalar(select(CoursePrerequisite))
        db.delete(prerequisite)
        db.flush()
        assert not course_access_states(db, **kwargs)[b].locked
        db.add(CoursePrerequisite(tenant_id=tenant, course_id=b, requires_course_id=a))
        db.flush()
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=a,
                status="completed",
                pct=1,
            )
        )
        db.flush()
        assert not course_access_states(db, **kwargs)[b].locked
        db.add(CoursePrerequisite(tenant_id=tenant, course_id=b, requires_course_id=c))
        db.flush()
        assert (
            course_access_states(db, **kwargs)[b].locked_reason
            == 'Complete "Explicit" to unlock this course.'
        )
        assert enrollment.track_id is not None


def test_window_edges_extension_future_start_and_null_end(
    track_case,
):
    engine, tenant, _, person, cohort, courses, _ = track_case
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _, enrollment = _setup(db, tenant, person, cohort, courses[:2])
        offerings = {row.course_id: row for row in db.scalars(select(CourseOffering))}
        a, b = courses[:2]
        offerings[a].starts_at = offerings[a].ends_at = now
        offerings[b].ends_at = now - timedelta(days=1)
        enrollment.access_ends_at = now
        db.flush()
        kwargs = {"tenant_id": tenant, "person_id": person}
        assert open_course_ids(db, now=now, **kwargs) == {a, b}
        assert visible_course_ids(db, **kwargs) == {a, b}
        assert accessible_course_ids(db, **kwargs) == {a}
        offerings[b].starts_at = now + timedelta(microseconds=1)
        db.flush()
        assert open_course_ids(db, now=now, **kwargs) == {a}
        offerings[b].starts_at = None
        offerings[b].ends_at = None
        enrollment.access_ends_at = now - timedelta(days=30)
        db.flush()
        assert open_course_ids(db, now=now + timedelta(days=2), **kwargs) == {b}


def test_require_open_window_before_prerequisites_and_preserves_source_details(
    track_case, monkeypatch
):
    engine, tenant, _, person, cohort, courses, _ = track_case
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _setup(db, tenant, person, cohort, courses[:2])
        a, b = courses[:2]
        offering = db.scalar(
            select(CourseOffering).where(CourseOffering.course_id == b)
        )
        offering.starts_at = now + timedelta(days=1)
        db.flush()
        kwargs = {"tenant_id": tenant, "person_id": person, "course_id": b, "now": now}

        def unexpected(*args, **kw):
            raise AssertionError("prerequisites checked before closed window refusal")

        with monkeypatch.context() as patch:
            patch.setattr(
                "dotmac_learning.entitlements.unmet_prerequisites", unexpected
            )
            with pytest.raises(CourseAccessDenied) as refusal:
                require_course_open(db, **kwargs)
            assert refusal.value.detail == "Forbidden"
        offering.starts_at = now
        db.flush()
        with pytest.raises(CourseAccessDenied) as refusal:
            require_course_open(db, **kwargs)
        assert (
            refusal.value.detail
            == "Complete the previous course to unlock this course."
        )
        require_course_open(db, **{**kwargs, "course_id": a})
