"""Academy 096 per-activity pacing reads and enforcement.

Offering windows are enforced by the product's course-access owner. This owner
only reads applicable overrides; the audited source has no production writer.
Ordinary ORM autoflush and caller-owned transaction behavior are retained.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from .delivery_models import OfferingActivity
from .pacing_contracts import PacingFact, PacingForbidden
from .participation_models import CourseOffering, Enrollment


def _applicable_overrides(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID, activity_id: UUID
) -> list[OfferingActivity]:
    """Activity overrides across the person's active offerings, of any role."""
    return list(
        db.scalars(
            select(OfferingActivity)
            .join(
                CourseOffering,
                (CourseOffering.id == OfferingActivity.offering_id)
                & (CourseOffering.tenant_id == OfferingActivity.tenant_id),
            )
            .join(
                Enrollment,
                (Enrollment.cohort_id == CourseOffering.cohort_id)
                & (Enrollment.tenant_id == CourseOffering.tenant_id),
            )
            .where(OfferingActivity.tenant_id == tenant_id)
            .where(OfferingActivity.activity_id == activity_id)
            .where(CourseOffering.course_id == course_id)
            .where(CourseOffering.status == "active")
            .where(Enrollment.person_id == person_id)
            .where(Enrollment.status == "active")
        ).all()
    )


def _offering_pacing_defaults(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> list[bool]:
    """Pacing defaults across the person's active offerings for the course."""
    return list(
        db.scalars(
            select(CourseOffering.pacing_default_open)
            .join(
                Enrollment,
                (Enrollment.cohort_id == CourseOffering.cohort_id)
                & (Enrollment.tenant_id == CourseOffering.tenant_id),
            )
            .where(CourseOffering.tenant_id == tenant_id)
            .where(CourseOffering.course_id == course_id)
            .where(CourseOffering.status == "active")
            .where(Enrollment.person_id == person_id)
            .where(Enrollment.status == "active")
        ).all()
    )


def activity_pacing(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_id: UUID,
    activity_id: UUID,
    now: datetime | None = None,
) -> PacingFact:
    """Return the most permissive applicable release/submission decision."""
    now = now or datetime.now(UTC)
    overrides = _applicable_overrides(
        db,
        tenant_id=tenant_id,
        person_id=person_id,
        course_id=course_id,
        activity_id=activity_id,
    )
    if not overrides:
        defaults = _offering_pacing_defaults(
            db, tenant_id=tenant_id, person_id=person_id, course_id=course_id
        )
        # No applicable offering keeps the historical open result: entitlement
        # is decided by other owners, not by pacing.
        open_by_default = True if not defaults else any(defaults)
        return PacingFact(
            readable=open_by_default,
            submittable=open_by_default,
            has_override=False,
        )

    readable = False
    submittable = False
    for ov in overrides:
        released = ov.release_at is None or ov.release_at <= now
        not_past_due = ov.due_at is None or ov.due_at >= now
        readable = readable or released
        submittable = submittable or (released and not_past_due)
    return PacingFact(readable=readable, submittable=submittable, has_override=True)


def require_activity_readable(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_id: UUID,
    activity_id: UUID,
    now: datetime | None = None,
) -> None:
    fact = activity_pacing(
        db,
        tenant_id=tenant_id,
        person_id=person_id,
        course_id=course_id,
        activity_id=activity_id,
        now=now,
    )
    if not fact.readable:
        raise PacingForbidden("Activity not yet released")


def require_activity_submittable(
    db: Session,
    *,
    tenant_id: UUID,
    person_id: UUID,
    course_id: UUID,
    activity_id: UUID,
    now: datetime | None = None,
) -> None:
    fact = activity_pacing(
        db,
        tenant_id=tenant_id,
        person_id=person_id,
        course_id=course_id,
        activity_id=activity_id,
        now=now,
    )
    if not fact.submittable:
        raise PacingForbidden(
            "Activity is not open for submission (not released or past due)"
        )


__all__ = [
    "activity_pacing",
    "require_activity_readable",
    "require_activity_submittable",
]
