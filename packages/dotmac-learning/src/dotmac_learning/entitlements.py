"""Course entitlement checks.

Single source of truth for "may this person study this course?". Access requires
an active Enrollment tying the person to a Cohort that has an active
CourseOffering for the course. Discipline strings do not grant access.

Prerequisite satisfaction (HM-01) is decided only by ``_unmet_conditions``:
tenant data rows name a closed condition catalog, grouped by ``group_key``.
Every condition in a group must hold; ``Course.prerequisite_composition``
decides whether every group (``all``) or one group (``any``) must hold. The
historical default -- kind ``course_completed``, group ``g0``, composition
``all`` -- reproduces the previous implicit-ALL completion check exactly.

Certificate conditions require an unexpired certificate (HM-04): a
certificate satisfies ``certificate_held`` iff it exists and its
``expires_at`` is null or later than the evaluation instant ``now``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import Row, func, or_, select
from sqlalchemy.orm import Session

from .gradebook import grade_from
from .models import Activity, Certificate, Course, CourseCompletion, Submission
from .participation_contracts import ParticipationForbidden
from .participation_models import (
    CourseOffering,
    CoursePrerequisite,
    Enrollment,
    Track,
    TrackCourse,
)
from .progress_contracts import ScoreFact
from .projections import best_scores_by_course_for_person
from .references import _activity_fact

_COURSE_SCOPED_KINDS = frozenset(
    ("course_completed", "certificate_held", "course_grade_at_least")
)


class CourseAccessDenied(ParticipationForbidden):
    """Product adapter maps status_code/detail to its existing HTTP response."""

    status_code = 403

    def __init__(self, detail: str = "Forbidden") -> None:
        super().__init__(detail)
        self.detail = detail


@dataclass(frozen=True)
class CourseAccessState:
    course_id: UUID
    locked: bool
    locked_reason: str | None = None
    locked_until_course_id: UUID | None = None


@dataclass(frozen=True)
class _ConditionFact:
    course_id: UUID
    requires_course_id: UUID
    condition_kind: str
    params: Mapping[str, object]
    group_key: str


def _course_lock_reason(title: str | None, fallback: str) -> str:
    if title:
        return f'Complete "{title}" to unlock this course.'
    return fallback


def _entitled_course_rows(
    db: Session, *, tenant_id: UUID, person_id: UUID
) -> Sequence[Row[tuple[UUID, UUID | None, int | None, str]]]:
    rows = db.execute(
        select(
            CourseOffering.course_id,
            Enrollment.track_id,
            TrackCourse.order_index,
            Course.title,
        )
        .distinct()
        .join(
            Enrollment,
            (Enrollment.cohort_id == CourseOffering.cohort_id)
            & (Enrollment.tenant_id == CourseOffering.tenant_id),
        )
        .join(
            Course,
            (Course.id == CourseOffering.course_id)
            & (Course.tenant_id == CourseOffering.tenant_id),
        )
        .outerjoin(
            TrackCourse,
            (TrackCourse.tenant_id == Enrollment.tenant_id)
            & (TrackCourse.track_id == Enrollment.track_id)
            & (TrackCourse.course_id == CourseOffering.course_id),
        )
        .where(CourseOffering.tenant_id == tenant_id)
        .where(CourseOffering.status == "active")
        .where(Course.status.in_(("published", "completed")))
        .where(Enrollment.person_id == person_id)
        .where(Enrollment.status == "active")
        .where(or_(Enrollment.track_id.is_(None), TrackCourse.id.isnot(None)))
        .order_by(Enrollment.track_id, TrackCourse.order_index, Course.title)
    ).all()
    # The outer join can produce a NULL order index despite its column type.
    return cast(Sequence[Row[tuple[UUID, UUID | None, int | None, str]]], rows)


def _param_uuid(params: Mapping[str, object], name: str) -> UUID | None:
    value = params.get(name)
    if not isinstance(value, str):
        return None
    try:
        return UUID(value)
    except ValueError:
        return None


def _param_fraction(params: Mapping[str, object], name: str) -> float | None:
    value = params.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    fraction = float(value)
    if not math.isfinite(fraction) or not 0 < fraction <= 1:
        return None
    return fraction


def _param_min_attempts(params: Mapping[str, object]) -> int | None:
    value = params.get("min_attempts")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


class _PersonFacts:
    """Batched tenant-scoped learner facts for one evaluation pass."""

    def __init__(
        self,
        db: Session,
        *,
        tenant_id: UUID,
        person_id: UUID,
        conditions: Sequence[_ConditionFact],
        now: datetime,
    ) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._person_id = person_id
        kinds = {c.condition_kind for c in conditions}
        self.completed: set[UUID] = set(
            db.scalars(
                select(CourseCompletion.course_id)
                .where(CourseCompletion.tenant_id == tenant_id)
                .where(CourseCompletion.person_id == person_id)
                .where(CourseCompletion.status == "completed")
            ).all()
        )
        self._certified: set[UUID] | None = None
        if "certificate_held" in kinds:
            self._certified = set(
                db.scalars(
                    select(Certificate.course_id)
                    .where(Certificate.tenant_id == tenant_id)
                    .where(Certificate.person_id == person_id)
                    .where(
                        or_(
                            Certificate.expires_at.is_(None),
                            Certificate.expires_at > now,
                        )
                    )
                ).all()
            )
        self._grades: dict[UUID, int] | None = None
        self._passed_activities: set[UUID] | None = None
        self._passed_anchor: dict[UUID, UUID] = {}
        self._attempt_counts: dict[UUID, int] | None = None
        self._attempt_anchor: dict[UUID, UUID] = {}
        self._best_cache: dict[UUID, dict[UUID, ScoreFact]] | None = None
        self._conditions = conditions

    def _best_by_course(self) -> dict[UUID, dict[UUID, ScoreFact]]:
        if self._best_cache is None:
            course_ids = {
                c.requires_course_id
                for c in self._conditions
                if c.condition_kind
                in (
                    "course_grade_at_least",
                    "activity_passed",
                    "attempt_number_at_least",
                )
            }
            if not course_ids:
                self._best_cache = {}
            else:
                self._best_cache = best_scores_by_course_for_person(
                    self._db,
                    tenant_id=self._tenant_id,
                    person_id=self._person_id,
                    course_ids=sorted(course_ids, key=str),
                )
        return self._best_cache

    def grade_pct(self, course_id: UUID) -> int:
        if self._grades is None:
            best_by_course = self._best_by_course()
            course_ids = {
                c.requires_course_id
                for c in self._conditions
                if c.condition_kind == "course_grade_at_least"
            }
            activities_by_course: dict[UUID, list[Activity]] = {}
            if course_ids:
                for activity in self._db.scalars(
                    select(Activity)
                    .where(Activity.tenant_id == self._tenant_id)
                    .where(Activity.course_id.in_(course_ids))
                ).all():
                    activities_by_course.setdefault(activity.course_id, []).append(
                        activity
                    )
            self._grades = {
                cid: grade_from(
                    tuple(_activity_fact(a) for a in activities_by_course.get(cid, ())),
                    best_by_course.get(cid, {}),
                ).pct
                for cid in course_ids
            }
        return self._grades.get(course_id, 0)

    def activity_passed(self, activity_id: UUID, anchor_course_id: UUID) -> bool:
        if self._passed_activities is None:
            best_by_course = self._best_by_course()
            self._passed_activities = {
                aid
                for per_course in best_by_course.values()
                for aid, fact in per_course.items()
                if fact.passed
            }
            self._passed_anchor = {
                aid: cid
                for cid, per_course in best_by_course.items()
                for aid in per_course
            }
        return (
            activity_id in self._passed_activities
            and self._passed_anchor.get(activity_id) == anchor_course_id
        )

    def attempt_count(self, activity_id: UUID, anchor_course_id: UUID) -> int:
        if self._attempt_counts is None:
            activity_ids: set[UUID] = set()
            for condition in self._conditions:
                if condition.condition_kind != "attempt_number_at_least":
                    continue
                found_id = _param_uuid(condition.params, "activity_id")
                if found_id is not None:
                    activity_ids.add(found_id)
            self._attempt_counts = {}
            if activity_ids:
                rows = self._db.execute(
                    select(Submission.activity_id, func.count(Submission.id))
                    .join(
                        Activity,
                        (Activity.id == Submission.activity_id)
                        & (Activity.tenant_id == Submission.tenant_id),
                    )
                    .where(Submission.tenant_id == self._tenant_id)
                    .where(Submission.person_id == self._person_id)
                    .where(Submission.activity_id.in_(activity_ids))
                    .group_by(Submission.activity_id)
                ).all()
                self._attempt_counts = {aid: int(count) for aid, count in rows}
                self._attempt_anchor = {
                    a.id: a.course_id
                    for a in self._db.scalars(
                        select(Activity)
                        .where(Activity.tenant_id == self._tenant_id)
                        .where(Activity.id.in_(activity_ids))
                    ).all()
                }
        if self._attempt_anchor.get(activity_id) != anchor_course_id:
            return 0
        return self._attempt_counts.get(activity_id, 0)

    def satisfied(self, condition: _ConditionFact) -> bool:
        kind = condition.condition_kind
        if kind == "course_completed":
            return condition.requires_course_id in self.completed
        if kind == "certificate_held":
            # Only unexpired certificates were loaded (HM-04).
            return bool(
                self._certified is not None
                and condition.requires_course_id in self._certified
            )
        if kind == "course_grade_at_least":
            min_fraction = _param_fraction(condition.params, "min_fraction")
            return (
                min_fraction is not None
                and self.grade_pct(condition.requires_course_id) / 100 >= min_fraction
            )
        if kind == "activity_passed":
            activity_id = _param_uuid(condition.params, "activity_id")
            return activity_id is not None and self.activity_passed(
                activity_id, condition.requires_course_id
            )
        if kind == "attempt_number_at_least":
            activity_id = _param_uuid(condition.params, "activity_id")
            min_attempts = _param_min_attempts(condition.params)
            return (
                activity_id is not None
                and min_attempts is not None
                and self.attempt_count(activity_id, condition.requires_course_id)
                >= min_attempts
            )
        # Unknown stored kind: fail closed (never satisfied).
        return False


def _load_conditions(
    db: Session, *, tenant_id: UUID, course_ids: Sequence[UUID]
) -> list[_ConditionFact]:
    """Condition facts in deterministic reporting order.

    Rows are ordered by ``(group_key, condition_kind, requires_course_id,
    id)`` so the first unmet course-scoped condition reported as the lock
    blocker (``locked_until``/reason) is stable across calls; the parent
    ordering was DB-nondeterministic. Satisfaction itself is order-free.
    """
    if not course_ids:
        return []
    rows = db.execute(
        select(
            CoursePrerequisite.course_id,
            CoursePrerequisite.requires_course_id,
            CoursePrerequisite.condition_kind,
            CoursePrerequisite.params,
            CoursePrerequisite.group_key,
        )
        .where(CoursePrerequisite.tenant_id == tenant_id)
        .where(CoursePrerequisite.course_id.in_(list(course_ids)))
        .order_by(
            CoursePrerequisite.group_key,
            CoursePrerequisite.condition_kind,
            CoursePrerequisite.requires_course_id,
            CoursePrerequisite.id,
        )
    ).all()
    facts: list[_ConditionFact] = []
    for course_id, requires_course_id, kind, params, group_key in rows:
        facts.append(
            _ConditionFact(
                course_id=course_id,
                requires_course_id=requires_course_id,
                condition_kind=kind,
                params=params if isinstance(params, dict) else {},
                group_key=group_key,
            )
        )
    return facts


def _unmet_conditions(
    conditions: Sequence[_ConditionFact],
    facts: _PersonFacts,
    compositions: Mapping[UUID, str],
) -> dict[UUID, list[_ConditionFact]]:
    """Unmet condition facts per course; the single prerequisite evaluator.

    A group is satisfied iff every condition in it holds. Under composition
    ``all`` every group must hold (unmet = every failing condition); under
    ``any`` one satisfied group opens the course (when none holds, unmet =
    the failing conditions of the first group in deterministic order, the
    nearest path to opening). A course with no condition rows is satisfied.
    """
    by_course: dict[UUID, dict[str, list[_ConditionFact]]] = {}
    for condition in conditions:
        by_course.setdefault(condition.course_id, {}).setdefault(
            condition.group_key, []
        ).append(condition)
    unmet: dict[UUID, list[_ConditionFact]] = {}
    for course_id, groups in by_course.items():
        failing_by_group = {
            group: [c for c in members if not facts.satisfied(c)]
            for group, members in groups.items()
        }
        if compositions.get(course_id, "all") == "any":
            if any(not failing for failing in failing_by_group.values()):
                continue
            first_group = min(failing_by_group)
            unmet[course_id] = failing_by_group[first_group]
        else:
            failing = [
                c for group in sorted(failing_by_group) for c in failing_by_group[group]
            ]
            if failing:
                unmet[course_id] = failing
    return unmet


def _compositions(
    db: Session, *, tenant_id: UUID, course_ids: Sequence[UUID]
) -> dict[UUID, str]:
    if not course_ids:
        return {}
    return {
        row[0]: row[1]
        for row in db.execute(
            select(Course.id, Course.prerequisite_composition)
            .where(Course.tenant_id == tenant_id)
            .where(Course.id.in_(list(course_ids)))
        ).all()
    }


def _evaluate_conditions(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_ids: Sequence[UUID],
    now: datetime | None = None,
) -> dict[UUID, list[_ConditionFact]]:
    conditions = _load_conditions(db, tenant_id=tenant_id, course_ids=course_ids)
    if not conditions:
        return {}
    facts = _PersonFacts(
        db,
        tenant_id=tenant_id,
        person_id=person_id,
        conditions=conditions,
        now=now or datetime.now(UTC),
    )
    compositions = _compositions(db, tenant_id=tenant_id, course_ids=course_ids)
    return _unmet_conditions(conditions, facts, compositions)


def visible_course_ids(db: Session, *, tenant_id: UUID, person_id: UUID) -> set[UUID]:
    """Course ids the person is entitled to see, including locked track courses."""
    return {
        course_id
        for course_id, _track_id, _order_index, _title in _entitled_course_rows(
            db, tenant_id=tenant_id, person_id=person_id
        )
    }


def course_access_states(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    now: datetime | None = None,
) -> dict[UUID, CourseAccessState]:
    """Access state for entitled courses.

    Track-assigned enrollments unlock courses per the track's gating mode
    (HM-02): ``previous`` (the historical rule) needs the immediately
    previous course completed, ``all_previous`` needs every predecessor,
    ``none`` never locks on track order. Null-track enrollments preserve
    legacy cohort-wide access. Prerequisite conditions then apply on top;
    a track lock reason keeps precedence over a prerequisite reason.

    ``now`` is the evaluation instant for certificate expiry (HM-04).
    """
    now = now or datetime.now(UTC)
    rows = _entitled_course_rows(db, tenant_id=tenant_id, person_id=person_id)
    if not rows:
        return {}

    completed = set(
        db.scalars(
            select(CourseCompletion.course_id)
            .where(CourseCompletion.tenant_id == tenant_id)
            .where(CourseCompletion.person_id == person_id)
            .where(CourseCompletion.status == "completed")
        ).all()
    )

    track_ids = {track_id for _c, track_id, _o, _t in rows if track_id is not None}
    gating_modes: dict[UUID, str] = {}
    if track_ids:
        gating_modes = {
            row[0]: row[1]
            for row in db.execute(
                select(Track.id, Track.gating_mode)
                .where(Track.tenant_id == tenant_id)
                .where(Track.id.in_(track_ids))
            ).all()
        }

    track_sequences: dict[UUID, list[UUID]] = {}
    legacy_courses: set[UUID] = set()
    course_titles = {
        course_id: title for course_id, _track_id, _order_index, title in rows
    }
    for course_id, track_id, _order_index, _title in rows:
        if track_id is None:
            legacy_courses.add(course_id)
            continue
        track_sequences.setdefault(track_id, [])
        if course_id not in track_sequences[track_id]:
            track_sequences[track_id].append(course_id)

    states: dict[UUID, CourseAccessState] = {
        course_id: CourseAccessState(course_id=course_id, locked=False)
        for course_id in legacy_courses
    }
    for track_id, sequence in track_sequences.items():
        gating_mode = gating_modes.get(track_id, "previous")
        for index, course_id in enumerate(sequence):
            blocker: UUID | None = None
            if gating_mode == "all_previous":
                blocker = next(
                    (cid for cid in sequence[:index] if cid not in completed),
                    None,
                )
            elif gating_mode != "none":
                # "previous" (and any unrecognized stored value, fail-safe to
                # the historical rule): immediate predecessor only.
                previous_course_id = sequence[index - 1] if index > 0 else None
                if (
                    previous_course_id is not None
                    and previous_course_id not in completed
                ):
                    blocker = previous_course_id
            state = (
                CourseAccessState(course_id=course_id, locked=False)
                if blocker is None
                else CourseAccessState(
                    course_id=course_id,
                    locked=True,
                    locked_reason=_course_lock_reason(
                        course_titles.get(blocker),
                        "Complete the previous course to unlock this course.",
                    ),
                    locked_until_course_id=blocker,
                )
            )
            current = states.get(course_id)
            if current is None or current.locked:
                states[course_id] = state

    # Explicit prerequisite conditions apply on top of track order: a course
    # with unmet conditions is locked even when track order (or a null track)
    # would otherwise open it. Without this, the dashboard shows Start and
    # the chapter route then 403s (require_course_open checks these).
    entitled_ids = [course_id for course_id, _t, _o, _title in rows]
    unmet = _evaluate_conditions(
        db, tenant_id=tenant_id, person_id=person_id, course_ids=entitled_ids, now=now
    )
    if unmet:
        missing_title_ids = {
            condition.requires_course_id
            for conditions in unmet.values()
            for condition in conditions
            if condition.requires_course_id not in course_titles
        }
        if missing_title_ids:
            missing_title_rows = db.execute(
                select(Course.id, Course.title)
                .where(Course.tenant_id == tenant_id)
                .where(Course.id.in_(missing_title_ids))
            ).all()
            course_titles.update({row[0]: row[1] for row in missing_title_rows})
    for course_id, conditions in unmet.items():
        current = states.get(course_id)
        if current is not None and current.locked:
            continue  # already locked (track order); keep the first reason
        first_course_scoped = next(
            (c for c in conditions if c.condition_kind in _COURSE_SCOPED_KINDS),
            None,
        )
        if first_course_scoped is not None:
            reason = _course_lock_reason(
                course_titles.get(first_course_scoped.requires_course_id),
                "Complete the prerequisite course to unlock this course.",
            )
            locked_until: UUID | None = first_course_scoped.requires_course_id
        else:
            reason = "Complete the prerequisite activities to unlock this course."
            locked_until = None
        states[course_id] = CourseAccessState(
            course_id=course_id,
            locked=True,
            locked_reason=reason,
            locked_until_course_id=locked_until,
        )
    return states


def accessible_course_ids(
    db: Session, *, tenant_id: UUID, person_id: UUID
) -> set[UUID]:
    """Course ids the person may currently access/start."""
    return {
        course_id
        for course_id, state in course_access_states(
            db, tenant_id=tenant_id, person_id=person_id
        ).items()
        if not state.locked
    }


def person_can_access_course(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> bool:
    state = course_access_states(db, tenant_id=tenant_id, person_id=person_id).get(
        course_id
    )
    return bool(state and not state.locked)


def require_course_access(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> None:
    """Raise 403 if the person is not entitled to the course."""
    if not person_can_access_course(
        db, tenant_id=tenant_id, person_id=person_id, course_id=course_id
    ):
        raise CourseAccessDenied()


def open_course_ids(
    db: Session, *, tenant_id: UUID, person_id: UUID, now: datetime | None = None
) -> set[UUID]:
    """Entitled course ids whose offering window is currently open.

    A null window edge is open-ended; a fully null window is always open.
    """
    now = now or datetime.now(UTC)
    rows = db.scalars(
        select(CourseOffering.course_id)
        .distinct()
        .join(
            Enrollment,
            (Enrollment.cohort_id == CourseOffering.cohort_id)
            & (Enrollment.tenant_id == CourseOffering.tenant_id),
        )
        .join(
            Course,
            (Course.id == CourseOffering.course_id)
            & (Course.tenant_id == CourseOffering.tenant_id),
        )
        .outerjoin(
            TrackCourse,
            (TrackCourse.tenant_id == Enrollment.tenant_id)
            & (TrackCourse.track_id == Enrollment.track_id)
            & (TrackCourse.course_id == CourseOffering.course_id),
        )
        .where(CourseOffering.tenant_id == tenant_id)
        .where(CourseOffering.status == "active")
        .where(Course.status.in_(("published", "completed")))
        .where(Enrollment.person_id == person_id)
        .where(Enrollment.status == "active")
        .where(or_(Enrollment.track_id.is_(None), TrackCourse.id.isnot(None)))
        .where(or_(CourseOffering.starts_at.is_(None), CourseOffering.starts_at <= now))
        .where(
            or_(
                CourseOffering.ends_at.is_(None),
                CourseOffering.ends_at >= now,
                Enrollment.access_ends_at >= now,
            )
        )
    ).all()
    return set(rows)


def unmet_prerequisites(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_id: UUID,
    now: datetime | None = None,
) -> list[UUID]:
    """Prerequisite course ids the person has not yet satisfied.

    Back-compat projection of the internal evaluator: the required course
    behind every unmet course-scoped condition, in declaration order, plus
    the historical track locked-until append. Activity-scoped conditions
    have no course projection; ``require_course_open`` therefore denies on
    the internal facts, not on this list being empty. ``now`` is the
    evaluation instant for certificate expiry (HM-04).
    """
    now = now or datetime.now(UTC)
    unmet = _evaluate_conditions(
        db, tenant_id=tenant_id, person_id=person_id, course_ids=[course_id], now=now
    )
    required = [
        condition.requires_course_id
        for condition in unmet.get(course_id, [])
        if condition.condition_kind in _COURSE_SCOPED_KINDS
    ]
    state = course_access_states(
        db, tenant_id=tenant_id, person_id=person_id, now=now
    ).get(course_id)
    if state is not None and state.locked and state.locked_until_course_id is not None:
        required.append(state.locked_until_course_id)
    if not required:
        return []
    satisfied_courses = set(
        db.scalars(
            select(CourseCompletion.course_id)
            .where(CourseCompletion.tenant_id == tenant_id)
            .where(CourseCompletion.person_id == person_id)
            .where(CourseCompletion.status == "completed")
            .where(CourseCompletion.course_id.in_(set(required)))
        ).all()
    )
    return [cid for cid in required if cid not in satisfied_courses]


def require_course_open(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_id: UUID,
    now: datetime | None = None,
) -> None:
    """Raise 403 if not entitled, the offering window isn't open, or a
    prerequisite condition is not yet satisfied.

    Denial keeps the historical rule (CORE-1): only unmet prerequisites
    lock a course, so a course with no access state opens when no
    prerequisite condition is unmet. A present state's ``locked`` flag
    already folds in track gating and the evaluator; an absent state
    falls back to evaluating the course's conditions directly, which is
    the parent code's ``unmet_prerequisites`` emptiness test at defaults.
    """
    if course_id not in open_course_ids(
        db, tenant_id=tenant_id, person_id=person_id, now=now
    ):
        raise CourseAccessDenied()
    state = course_access_states(
        db, tenant_id=tenant_id, person_id=person_id, now=now
    ).get(course_id)
    if state is not None:
        locked = state.locked
    else:
        locked = bool(
            _evaluate_conditions(
                db,
                tenant_id=tenant_id,
                person_id=person_id,
                course_ids=[course_id],
                now=now,
            ).get(course_id)
        )
    if locked:
        raise CourseAccessDenied("Complete the previous course to unlock this course.")
