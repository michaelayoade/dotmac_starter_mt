"""Hosted PostgreSQL prerequisite-condition evaluator and track gating (HM-01/02).

Mirrors the entitlements harness; defaults (course_completed/g0/all,
gating 'previous') are covered by the pre-existing suites, which must keep
passing unmodified.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotmac_learning.entitlements import (
    CourseAccessDenied,
    course_access_states,
    require_course_open,
    unmet_prerequisites,
)
from dotmac_learning.models import (
    Activity,
    Certificate,
    Course,
    CourseCompletion,
    Score,
    Submission,
)
from dotmac_learning.participation_models import (
    CourseOffering,
    CoursePrerequisite,
    Enrollment,
    Track,
)
from dotmac_learning.prerequisite_commands import reconcile_prerequisites
from dotmac_learning.prerequisite_contracts import (
    PrerequisiteConditionDeclaration,
    PrerequisiteDeclaration,
    PrerequisiteReconciliationError,
    ReconcilePrerequisitesCommand,
)
from dotmac_learning.tracks import CreateTrackCommand, create_cohort_track
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests import test_learning_track_commands as track_commands
from tests.test_learning_catalog import _scope

track_case = track_commands.track_case
learning_database = track_commands.learning_database

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _legacy_enroll(db, tenant, person, cohort, courses):
    for course_id in courses:
        db.add(CourseOffering(tenant_id=tenant, cohort_id=cohort, course_id=course_id))
    db.add(
        Enrollment(
            tenant_id=tenant,
            cohort_id=cohort,
            person_id=person,
            role_in_cohort="student",
            status="active",
        )
    )
    db.flush()


def _publish(db, *course_ids):
    for course_id in course_ids:
        db.get(Course, course_id).status = "published"
    db.flush()


def _activity_with_score(
    db, tenant, person, course_id, *, fraction, passed, attempts=1
):
    activity = Activity(
        tenant_id=tenant,
        course_id=course_id,
        type="quiz",
        title="Quiz",
        pass_threshold=0.5,
        grading="auto",
        weight=1.0,
    )
    db.add(activity)
    db.flush()
    for number in range(1, attempts + 1):
        submission = Submission(
            tenant_id=tenant,
            activity_id=activity.id,
            person_id=person,
            attempt_no=number,
        )
        db.add(submission)
        db.flush()
        if number == attempts:
            db.add(
                Score(
                    tenant_id=tenant,
                    submission_id=submission.id,
                    score=fraction * 10,
                    max_score=10,
                    fraction=fraction,
                    passed=passed,
                )
            )
    db.flush()
    return activity


def _reconcile(db, tenant, declarations):
    result = reconcile_prerequisites(
        db,
        ReconcilePrerequisitesCommand(
            tenant_id=tenant, discipline="engineering", declarations=declarations
        ),
    )
    db.flush()
    return result


def test_kinds_composition_and_activity_only_trap(track_case):
    engine, tenant, _, person, cohort, (a, b, c, d), _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _publish(db, a, b, c, d)
        _legacy_enroll(db, tenant, person, cohort, (a, b, c, d))
        kwargs = {"tenant_id": tenant, "person_id": person}
        activity = _activity_with_score(
            db, tenant, person, a, fraction=0.8, passed=True, attempts=1
        )

        # certificate_held (interim: any issued certificate satisfies).
        _reconcile(
            db,
            tenant,
            (
                PrerequisiteDeclaration(
                    course=b.hex,
                    conditions=(
                        PrerequisiteConditionDeclaration(
                            kind="certificate_held", requires=a.hex
                        ),
                    ),
                ),
            ),
        )
        assert course_access_states(db, **kwargs)[b].locked
        db.add(
            Certificate(
                tenant_id=tenant,
                person_id=person,
                course_id=a,
                serial="S1",
                issued_at=NOW,
            )
        )
        db.flush()
        assert not course_access_states(db, **kwargs)[b].locked

        # any-composition: grade group fails (0.8 < 0.9); activity group needs
        # BOTH passed and >= 2 attempts.
        _reconcile(
            db,
            tenant,
            (
                PrerequisiteDeclaration(
                    course=c.hex,
                    composition="any",
                    conditions=(
                        PrerequisiteConditionDeclaration(
                            kind="course_grade_at_least",
                            requires=a.hex,
                            min_fraction=0.9,
                            group="g1",
                        ),
                        PrerequisiteConditionDeclaration(
                            kind="activity_passed", activity_id=activity.id, group="g2"
                        ),
                        PrerequisiteConditionDeclaration(
                            kind="attempt_number_at_least",
                            activity_id=activity.id,
                            min_attempts=2,
                            group="g2",
                        ),
                    ),
                ),
            ),
        )
        assert course_access_states(db, **kwargs)[c].locked
        second = Submission(
            tenant_id=tenant, activity_id=activity.id, person_id=person, attempt_no=2
        )
        db.add(second)
        db.flush()
        assert not course_access_states(db, **kwargs)[c].locked
        assert db.get(Course, c).prerequisite_composition == "any"

        # Activity-only condition: legacy projection is empty yet the course
        # stays closed (the projection trap regression).
        other_activity = _activity_with_score(
            db, tenant, person, a, fraction=0.2, passed=False
        )
        _reconcile(
            db,
            tenant,
            (
                PrerequisiteDeclaration(
                    course=d.hex,
                    conditions=(
                        PrerequisiteConditionDeclaration(
                            kind="activity_passed", activity_id=other_activity.id
                        ),
                    ),
                ),
            ),
        )
        assert course_access_states(db, **kwargs)[d].locked
        assert unmet_prerequisites(db, course_id=d, **kwargs) == []
        with pytest.raises(CourseAccessDenied):
            require_course_open(db, course_id=d, **kwargs)


def test_track_gating_modes_and_reason_precedence(track_case):
    engine, tenant, _, person, cohort, (a, b, c, _), _ = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _publish(db, a, b, c)
        track = create_cohort_track(
            db,
            CreateTrackCommand(
                tenant_id=tenant,
                cohort_id=cohort,
                name="Seq",
                course_ids=(a, b, c),
                gating_mode="none",
            ),
        )
        db.add(
            Enrollment(
                tenant_id=tenant,
                cohort_id=cohort,
                person_id=person,
                track_id=track.id,
                role_in_cohort="student",
                status="active",
            )
        )
        db.flush()
        kwargs = {"tenant_id": tenant, "person_id": person}
        states = course_access_states(db, **kwargs)
        assert not states[b].locked and not states[c].locked

        row = db.get(Track, track.id)
        row.gating_mode = "all_previous"
        db.flush()
        db.add(
            CourseCompletion(
                tenant_id=tenant,
                person_id=person,
                course_id=b,
                status="completed",
                pct=1,
            )
        )
        db.flush()
        states = course_access_states(db, **kwargs)
        assert states[c].locked and states[c].locked_until_course_id == a
        row.gating_mode = "previous"
        db.flush()
        assert not course_access_states(db, **kwargs)[c].locked

        # Track lock reason wins over an unmet explicit prerequisite.
        db.delete(
            db.scalar(select(CourseCompletion).where(CourseCompletion.course_id == b))
        )
        db.add(CoursePrerequisite(tenant_id=tenant, course_id=c, requires_course_id=a))
        db.flush()
        states = course_access_states(db, **kwargs)
        assert states[c].locked and states[c].locked_until_course_id == b


def test_reconcile_removal_guard_params_and_negatives(track_case):
    engine, tenant, _other, person, cohort, (a, b, c, _), foreign = track_case
    with Session(engine) as db, db.begin():
        _scope(db, tenant)
        _publish(db, a, b, c)
        _legacy_enroll(db, tenant, person, cohort, (a, b, c))
        declaration = PrerequisiteDeclaration(
            course=b.hex,
            conditions=(
                PrerequisiteConditionDeclaration(
                    kind="course_grade_at_least", requires=a.hex, min_fraction=0.5
                ),
            ),
        )
        first = _reconcile(db, tenant, (declaration,))
        assert (first.added, first.removed, first.declared) == (1, 0, 1)
        # Params change updates the same row (no duplicate, no recount).
        declaration = PrerequisiteDeclaration(
            course=b.hex,
            conditions=(
                PrerequisiteConditionDeclaration(
                    kind="course_grade_at_least", requires=a.hex, min_fraction=0.7
                ),
            ),
        )
        second = _reconcile(db, tenant, (declaration,))
        assert (second.added, second.removed, second.declared) == (0, 0, 1)
        rows = db.scalars(
            select(CoursePrerequisite).where(CoursePrerequisite.course_id == b)
        ).all()
        assert len(rows) == 1 and rows[0].params == {"min_fraction": 0.7}

        # A legacy requires-only reconcile never deletes authored conditions.
        _reconcile(db, tenant, (PrerequisiteDeclaration(course=b.hex),))
        assert (
            db.scalar(
                select(CoursePrerequisite).where(CoursePrerequisite.course_id == b)
            )
            is not None
        )
        # A conditions declaration manages all rows of its course (empty
        # conditions + composition still manages, per the removal guard).
        _reconcile(
            db,
            tenant,
            (PrerequisiteDeclaration(course=b.hex, composition="all"),),
        )
        assert (
            db.scalar(
                select(CoursePrerequisite).where(CoursePrerequisite.course_id == b)
            )
            is None
        )

        with pytest.raises(PrerequisiteReconciliationError, match="unknown activity"):
            _reconcile(
                db,
                tenant,
                (
                    PrerequisiteDeclaration(
                        course=b.hex,
                        conditions=(
                            PrerequisiteConditionDeclaration(
                                kind="activity_passed", activity_id=uuid4()
                            ),
                        ),
                    ),
                ),
            )
        with pytest.raises(PrerequisiteReconciliationError, match="cycle"):
            _reconcile(
                db,
                tenant,
                (
                    PrerequisiteDeclaration(
                        course=a.hex,
                        conditions=(
                            PrerequisiteConditionDeclaration(
                                kind="course_grade_at_least",
                                requires=b.hex,
                                min_fraction=0.5,
                            ),
                        ),
                    ),
                    PrerequisiteDeclaration(course=b.hex, requires=(a.hex,)),
                ),
            )
        with pytest.raises(PrerequisiteReconciliationError, match=foreign.hex):
            _reconcile(
                db,
                tenant,
                (
                    PrerequisiteDeclaration(
                        course=b.hex,
                        conditions=(
                            PrerequisiteConditionDeclaration(
                                kind="certificate_held", requires=foreign.hex
                            ),
                        ),
                    ),
                ),
            )
