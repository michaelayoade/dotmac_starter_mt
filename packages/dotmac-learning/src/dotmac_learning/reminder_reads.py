"""Six independent Academy reminder input reads in the caller's Session.

Product code supplies the source time bounds and interleaves these reads with
canonical todo, scheduling and event reads. Reminder occurrence order, keys,
copy, inactivity windows, delivery preferences and ReminderLog stay in Academy.
These functions neither select recipients nor establish a population snapshot.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Activity, Certificate, Course, CourseCompletion, Score, Submission
from .participation_models import Cohort, CourseOffering, Enrollment
from .reminder_contracts import (
    OpeningOffering,
    RecentAssignment,
    RecentCertificate,
    RecentCompletedCourse,
    RecentManualResult,
)


def recent_assignments(
    db: Session, *, tenant_id: UUID, person_id: UUID, since: datetime
) -> tuple[RecentAssignment, ...]:
    """Active enrollments of any role created at or after the supplied bound."""
    rows = db.execute(
        select(Enrollment.id, Cohort.name)
        .join(
            Cohort,
            (Cohort.id == Enrollment.cohort_id)
            & (Cohort.tenant_id == Enrollment.tenant_id),
        )
        .where(Enrollment.tenant_id == tenant_id)
        .where(Enrollment.person_id == person_id)
        .where(Enrollment.status == "active")
        .where(Enrollment.created_at >= since)
    ).all()
    return tuple(RecentAssignment(enrollment_id, name) for enrollment_id, name in rows)


def opening_offerings(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    after: datetime,
    through: datetime,
) -> tuple[OpeningOffering, ...]:
    """Active offerings with starts strictly after and inclusively through bounds.

    Preserve the source enrollment join, multiplicity and unspecified SQL order.
    No course-publication, cohort-status or enrollment-role selector is added.
    """
    rows = db.execute(
        select(CourseOffering.id, Course.title, CourseOffering.starts_at)
        .join(
            Course,
            (Course.id == CourseOffering.course_id)
            & (Course.tenant_id == CourseOffering.tenant_id),
        )
        .join(
            Enrollment,
            (Enrollment.cohort_id == CourseOffering.cohort_id)
            & (Enrollment.tenant_id == CourseOffering.tenant_id),
        )
        .where(CourseOffering.tenant_id == tenant_id)
        .where(Enrollment.person_id == person_id)
        .where(Enrollment.status == "active")
        .where(CourseOffering.status == "active")
        .where(CourseOffering.starts_at.is_not(None))
        .where(CourseOffering.starts_at > after)
        .where(CourseOffering.starts_at <= through)
    ).all()
    result: list[OpeningOffering] = []
    for offering_id, title, starts_at in rows:
        if starts_at is None:
            raise AssertionError
        result.append(OpeningOffering(offering_id, title, starts_at))
    return tuple(result)


def recent_manual_results(
    db: Session, *, tenant_id: UUID, person_id: UUID, since: datetime
) -> tuple[RecentManualResult, ...]:
    """Every recent non-auto Score, including failures and multiple per submission.

    Source != 'auto' is retained with SQL's null behavior. 'Manual' in this
    projection's name does not restrict storage to a particular source code.
    """
    rows = db.execute(
        select(Score.id, Activity.title)
        .join(
            Submission,
            (Submission.id == Score.submission_id)
            & (Submission.tenant_id == Score.tenant_id),
        )
        .join(
            Activity,
            (Activity.id == Submission.activity_id)
            & (Activity.tenant_id == Submission.tenant_id),
        )
        .where(Score.tenant_id == tenant_id)
        .where(Submission.person_id == person_id)
        .where(Score.source != "auto")
        .where(Score.created_at >= since)
    ).all()
    return tuple(RecentManualResult(score_id, title) for score_id, title in rows)


def recent_completed_courses(
    db: Session, *, tenant_id: UUID, person_id: UUID, since: datetime
) -> tuple[RecentCompletedCourse, ...]:
    """Completed records with a non-null completion timestamp at or after since."""
    rows = db.execute(
        select(CourseCompletion.id, Course.title)
        .join(
            Course,
            (Course.id == CourseCompletion.course_id)
            & (Course.tenant_id == CourseCompletion.tenant_id),
        )
        .where(CourseCompletion.tenant_id == tenant_id)
        .where(CourseCompletion.person_id == person_id)
        .where(CourseCompletion.status == "completed")
        .where(CourseCompletion.completed_at.is_not(None))
        .where(CourseCompletion.completed_at >= since)
    ).all()
    return tuple(
        RecentCompletedCourse(completion_id, title) for completion_id, title in rows
    )


def recent_certificates(
    db: Session, *, tenant_id: UUID, person_id: UUID, since: datetime
) -> tuple[RecentCertificate, ...]:
    """Certificates issued at or after since; no expiration policy is invented."""
    rows = db.execute(
        select(Certificate.id, Course.title)
        .join(
            Course,
            (Course.id == Certificate.course_id)
            & (Course.tenant_id == Certificate.tenant_id),
        )
        .where(Certificate.tenant_id == tenant_id)
        .where(Certificate.person_id == person_id)
        .where(Certificate.issued_at >= since)
    ).all()
    return tuple(
        RecentCertificate(certificate_id, title) for certificate_id, title in rows
    )


def earliest_active_enrollment_at(
    db: Session, *, tenant_id: UUID, person_id: UUID
) -> datetime | None:
    """Earliest active enrollment of any role, for the caller's no-event fallback.

    Academy calls this only when events.last_activity_at has returned None; this
    read owns neither that interleaving nor inactivity cutoffs/counts/windows.
    """
    return db.scalar(
        select(func.min(Enrollment.created_at))
        .where(Enrollment.tenant_id == tenant_id)
        .where(Enrollment.person_id == person_id)
        .where(Enrollment.status == "active")
    )


__all__ = [
    "recent_assignments",
    "opening_offerings",
    "recent_manual_results",
    "recent_completed_courses",
    "recent_certificates",
    "earliest_active_enrollment_at",
]
