"""Learner calendar / agenda service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import scheduling
from .agenda_contracts import AgendaItem, AgendaTarget
from .delivery_models import ClassSession, OfferingActivity
from .entitlements import accessible_course_ids
from .models import Activity, Course
from .participation_models import CourseOffering, Enrollment


def upcoming_for_person(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    limit: int = 50,
    now: datetime | None = None,
) -> tuple[AgendaItem, ...]:
    """Merged, chronologically-sorted future agenda items for a person.

    Returns offering window open/close events and activity due_at deadlines
    across the person's accessible courses. Past items are excluded.
    Products render kind labels and navigation from detached semantic targets.
    """
    now = now or datetime.now(UTC)
    items: list[AgendaItem] = []
    course_ids = accessible_course_ids(db, tenant_id=tenant_id, person_id=person_id)

    courses_by_id: dict[UUID, Course] = (
        {
            c.id: c
            for c in db.scalars(
                select(Course)
                .where(Course.tenant_id == tenant_id)
                .where(Course.id.in_(course_ids))
            ).all()
        }
        if course_ids
        else {}
    )

    for off in (
        []
        if not course_ids
        else db.scalars(
            select(CourseOffering)
            .where(CourseOffering.tenant_id == tenant_id)
            .where(CourseOffering.status == "active")
            .where(CourseOffering.course_id.in_(course_ids))
        ).all()
    ):
        course = courses_by_id.get(off.course_id)
        if course is None:
            continue
        if off.starts_at is not None and off.starts_at > now:
            items.append(
                AgendaItem(
                    off.starts_at,
                    "opens",
                    course.title,
                    course.title,
                    AgendaTarget("course", course.id, course.slug),
                )
            )
        if off.ends_at is not None and off.ends_at > now:
            items.append(
                AgendaItem(
                    off.ends_at,
                    "closes",
                    course.title,
                    course.title,
                    AgendaTarget("course", course.id, course.slug),
                )
            )

    rows = (
        []
        if not course_ids
        else db.execute(
            select(OfferingActivity, Activity, Course)
            .join(
                Activity,
                (Activity.id == OfferingActivity.activity_id)
                & (Activity.tenant_id == OfferingActivity.tenant_id),
            )
            .join(
                CourseOffering,
                (CourseOffering.id == OfferingActivity.offering_id)
                & (CourseOffering.tenant_id == OfferingActivity.tenant_id),
            )
            .join(
                Course,
                (Course.id == CourseOffering.course_id)
                & (Course.tenant_id == CourseOffering.tenant_id),
            )
            .where(OfferingActivity.tenant_id == tenant_id)
            .where(CourseOffering.status == "active")
            .where(CourseOffering.course_id.in_(course_ids))
            .where(OfferingActivity.due_at.isnot(None))
            .where(OfferingActivity.due_at > now)
        ).all()
    )

    for pa, act, course in rows:
        if pa.due_at is None:
            raise AssertionError
        items.append(
            AgendaItem(
                pa.due_at,
                "due",
                act.title,
                course.title,
                AgendaTarget("activity", act.id),
            )
        )

    # Class sessions on the person's cohort timetable (student or instructor).
    cohort_ids = list(
        db.scalars(
            select(Enrollment.cohort_id)
            .where(Enrollment.tenant_id == tenant_id)
            .where(Enrollment.person_id == person_id)
        ).all()
    )
    if cohort_ids:
        for s in db.scalars(
            select(ClassSession)
            .where(ClassSession.tenant_id == tenant_id)
            .where(ClassSession.cohort_id.in_(cohort_ids))
            .where(ClassSession.status == "scheduled")
            # Keep a session visible until it ends, not until it starts —
            # otherwise a live class vanishes from the calendar at the exact
            # moment the learner needs the Join affordance (join_is_open and
            # scheduling.list_for_person both treat in-progress as current).
            .where(func.coalesce(ClassSession.ends_at, ClassSession.starts_at) >= now)
        ).all():
            session = scheduling._fact(s)
            items.append(
                AgendaItem(
                    s.starts_at,
                    "session",
                    s.title,
                    "",
                    AgendaTarget("session", s.id),
                    session,
                    scheduling.join_is_open(session, now=now),
                )
            )

    items.sort(key=lambda x: x.when)
    return tuple(items[:limit])


__all__ = ["upcoming_for_person"]
