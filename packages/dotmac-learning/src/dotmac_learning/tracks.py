"""Caller-transaction track management ported from Academy 096.

Tracks are an additive curriculum layer above the existing CourseOffering
entitlement table. CourseOffering remains the coarse cohort/course availability
record; a non-null Enrollment.track_id narrows a learner to that track's courses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Activity, Course
from .participation_contracts import (
    EnrollmentFact,
    enrollment_fact,
)
from .participation_contracts import (
    ParticipationBadRequest as BadRequestError,
)
from .participation_contracts import (
    ParticipationNotFound as NotFoundError,
)
from .participation_models import (
    Cohort,
    CohortTrack,
    CourseOffering,
    Enrollment,
    Track,
    TrackCourse,
)
from .references import CourseFact, _course_fact
from .results import _transaction, _unchanged

TRACK_GATING_MODES: tuple[str, ...] = ("none", "previous", "all_previous")


class CreateTrackCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: UUID
    cohort_id: UUID
    name: str
    course_ids: tuple[UUID, ...]
    gating_mode: str
    positions: dict[UUID, int] = Field(default_factory=dict)

    @field_validator("gating_mode")
    @classmethod
    def validate_gating_mode(cls, value: str) -> str:
        if value not in TRACK_GATING_MODES:
            raise ValueError(f"unknown track gating mode: {value!r}")
        return value


class AddTrackCoursesCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: UUID
    cohort_id: UUID
    track_id: UUID
    course_ids: tuple[UUID, ...]
    positions: dict[UUID, int] = Field(default_factory=dict)


class ReorderTrackCoursesCommand(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    tenant_id: UUID
    track_id: UUID
    ordered_course_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class TrackFact:
    id: UUID
    tenant_id: UUID
    slug: str
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    gating_mode: str = "previous"


@dataclass(frozen=True)
class CohortTrackFact:
    id: UUID
    tenant_id: UUID
    cohort_id: UUID
    track_id: UUID
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CohortTrackItem:
    cohort_track: CohortTrackFact
    track: TrackFact
    courses: tuple[CourseFact, ...]
    zero_activity_course_ids: frozenset[UUID]


def _track_fact(row: Track) -> TrackFact:
    return TrackFact(
        **{name: getattr(row, name) for name in TrackFact.__dataclass_fields__}
    )


def _cohort_track_fact(row: CohortTrack) -> CohortTrackFact:
    return CohortTrackFact(
        **{name: getattr(row, name) for name in CohortTrackFact.__dataclass_fields__}
    )


def _cohort(db: Session, *, tenant_id: UUID, cohort_id: UUID) -> Cohort:
    row = db.scalars(
        select(Cohort)
        .where(Cohort.tenant_id == tenant_id)
        .where(Cohort.id == cohort_id)
    ).first()
    if row is None:
        raise NotFoundError("cohort not found for tenant")
    return row


def _ordered_by_position(
    course_ids: list[UUID], positions: dict[UUID, int] | None
) -> list[UUID]:
    """Order ``course_ids`` by an optional client-supplied position hint.

    Any course with a supplied position sorts by that value (ties broken by
    original submission order); every course with no position hint is
    appended afterward, in submission order. The caller is responsible for
    turning the result into a dense ``1..N`` ``order_index`` sequence — a
    client-supplied numeric value is never stored directly, since duplicate
    or gapped values would silently fall back to the ``Course.title``
    tiebreak used elsewhere (``entitlements.py``'s ``_entitled_course_rows``).
    """
    positions = positions or {}
    positioned = sorted(
        (
            (positions[cid], idx, cid)
            for idx, cid in enumerate(course_ids)
            if cid in positions
        ),
        key=lambda t: (t[0], t[1]),
    )
    unpositioned = [cid for cid in course_ids if cid not in positions]
    return [cid for _, _, cid in positioned] + unpositioned


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")
    return slug or "track"


def _unique_track_slug(db: Session, *, tenant_id: UUID, name: str) -> str:
    base = _slugify(name)[:70]
    slug = base
    i = 2
    while db.scalars(
        select(Track).where(Track.tenant_id == tenant_id).where(Track.slug == slug)
    ).first():
        suffix = f"-{i}"
        slug = f"{base[:80 - len(suffix)]}{suffix}"
        i += 1
    return slug


def list_cohort_tracks(
    db: Session, *, tenant_id: UUID, cohort_id: UUID
) -> tuple[CohortTrackItem, ...]:
    _cohort(db, tenant_id=tenant_id, cohort_id=cohort_id)
    rows = db.execute(
        select(CohortTrack, Track)
        .join(
            Track,
            (Track.id == CohortTrack.track_id)
            & (Track.tenant_id == CohortTrack.tenant_id),
        )
        .where(CohortTrack.tenant_id == tenant_id)
        .where(CohortTrack.cohort_id == cohort_id)
        .where(CohortTrack.status == "active")
        .order_by(Track.name)
    ).all()
    out: list[CohortTrackItem] = []
    for cohort_track, track in rows:
        courses = list(
            db.scalars(
                select(Course)
                .join(
                    TrackCourse,
                    (TrackCourse.course_id == Course.id)
                    & (TrackCourse.tenant_id == Course.tenant_id),
                )
                .where(TrackCourse.tenant_id == tenant_id)
                .where(TrackCourse.track_id == track.id)
                .order_by(TrackCourse.order_index, Course.title)
            ).all()
        )
        # Computed live (not just once, at track-creation time) so a course that
        # had its activities removed, or a draft published later with none, is
        # still caught — see "1c" in the enrollment-authority brief. Warning
        # only: this never blocks track creation, add-courses, or publishing.
        course_ids = [course.id for course in courses]
        with_activities: set[UUID] = set()
        if course_ids:
            with_activities = set(
                db.scalars(
                    select(Activity.course_id)
                    .where(Activity.tenant_id == tenant_id)
                    .where(Activity.course_id.in_(course_ids))
                ).all()
            )
        zero_activity_course_ids = {
            course.id for course in courses if course.id not in with_activities
        }
        out.append(
            CohortTrackItem(
                cohort_track=_cohort_track_fact(cohort_track),
                track=_track_fact(track),
                courses=tuple(_course_fact(course) for course in courses),
                zero_activity_course_ids=frozenset(zero_activity_course_ids),
            )
        )
    return tuple(out)


def tracks_for_cohorts(
    db: Session, *, tenant_id: UUID, cohort_ids: list[UUID]
) -> dict[UUID, tuple[TrackFact, ...]]:
    if not cohort_ids:
        return {}
    rows = db.execute(
        select(CohortTrack.cohort_id, Track)
        .join(
            Track,
            (Track.id == CohortTrack.track_id)
            & (Track.tenant_id == CohortTrack.tenant_id),
        )
        .where(CohortTrack.tenant_id == tenant_id)
        .where(CohortTrack.cohort_id.in_(cohort_ids))
        .where(CohortTrack.status == "active")
        .where(Track.status == "active")
        .order_by(Track.name)
    ).all()
    grouped: dict[UUID, list[TrackFact]] = {cohort_id: [] for cohort_id in cohort_ids}
    for cohort_id, track in rows:
        grouped.setdefault(cohort_id, []).append(_track_fact(track))
    return {key: tuple(value) for key, value in grouped.items()}


def create_cohort_track(db: Session, payload: CreateTrackCommand) -> TrackFact:
    boundary = _transaction(db)
    payload = payload.model_copy(deep=True)
    tenant_id = payload.tenant_id
    cohort_id = payload.cohort_id
    name = payload.name
    course_ids = payload.course_ids
    positions = payload.positions
    _cohort(db, tenant_id=tenant_id, cohort_id=cohort_id)
    clean_name = (name or "").strip()
    if not clean_name:
        raise BadRequestError("track name is required")
    if not course_ids:
        raise BadRequestError("select at least one course")

    seen: list[UUID] = []
    for course_id in course_ids:
        if course_id not in seen:
            seen.append(course_id)
    courses = db.scalars(
        select(Course).where(Course.tenant_id == tenant_id).where(Course.id.in_(seen))
    ).all()
    found = {course.id for course in courses}
    if found != set(seen):
        raise NotFoundError("one or more courses were not found")

    ordered = _ordered_by_position(seen, positions)

    track = Track(
        tenant_id=tenant_id,
        slug=_unique_track_slug(db, tenant_id=tenant_id, name=clean_name),
        name=clean_name,
        gating_mode=payload.gating_mode,
    )
    db.add(track)
    db.flush()
    db.add(
        CohortTrack(
            tenant_id=tenant_id, cohort_id=cohort_id, track_id=track.id, status="active"
        )
    )
    for idx, course_id in enumerate(ordered, start=1):
        db.add(
            TrackCourse(
                tenant_id=tenant_id,
                track_id=track.id,
                course_id=course_id,
                order_index=idx,
            )
        )
    db.flush()
    ensure_track_offerings(
        db, tenant_id=tenant_id, cohort_id=cohort_id, track_id=track.id
    )
    _unchanged(db, boundary)
    return _track_fact(track)


def add_courses_to_cohort_track(
    db: Session, payload: AddTrackCoursesCommand
) -> TrackFact:
    boundary = _transaction(db)
    payload = payload.model_copy(deep=True)
    tenant_id = payload.tenant_id
    cohort_id = payload.cohort_id
    track_id = payload.track_id
    course_ids = payload.course_ids
    positions = payload.positions
    cohort_track_or_404(db, tenant_id=tenant_id, cohort_id=cohort_id, track_id=track_id)
    if not course_ids:
        raise BadRequestError("select at least one course")

    seen: list[UUID] = []
    for course_id in course_ids:
        if course_id not in seen:
            seen.append(course_id)
    courses = db.scalars(
        select(Course).where(Course.tenant_id == tenant_id).where(Course.id.in_(seen))
    ).all()
    found = {course.id for course in courses}
    if found != set(seen):
        raise NotFoundError("one or more courses were not found")

    existing = set(
        db.scalars(
            select(TrackCourse.course_id)
            .where(TrackCourse.tenant_id == tenant_id)
            .where(TrackCourse.track_id == track_id)
        ).all()
    )
    max_order = (
        db.scalar(
            select(TrackCourse.order_index)
            .where(TrackCourse.tenant_id == tenant_id)
            .where(TrackCourse.track_id == track_id)
            .order_by(TrackCourse.order_index.desc())
            .limit(1)
        )
        or 0
    )
    # Position hints only order the newly-added courses relative to each
    # other; an existing track's already-dense order_index sequence is left
    # untouched — reordering existing membership is reorder_track_courses.
    to_add = [course_id for course_id in seen if course_id not in existing]
    ordered_new = _ordered_by_position(to_add, positions)
    next_order = max_order + 1
    for course_id in ordered_new:
        db.add(
            TrackCourse(
                tenant_id=tenant_id,
                track_id=track_id,
                course_id=course_id,
                order_index=next_order,
            )
        )
        next_order += 1
    db.flush()
    ensure_track_offerings(
        db, tenant_id=tenant_id, cohort_id=cohort_id, track_id=track_id
    )
    track = db.scalars(
        select(Track).where(Track.tenant_id == tenant_id).where(Track.id == track_id)
    ).first()
    if track is None:
        raise NotFoundError("track not found")
    _unchanged(db, boundary)
    return _track_fact(track)


def reorder_track_courses(
    db: Session, payload: ReorderTrackCoursesCommand
) -> TrackFact:
    """Reassign a dense ``1..N`` order to an EXISTING track's courses.

    Unlike create/add-courses (which only affect a track scoped to one
    cohort's create/add forms), a ``Track`` is tenant-level — ``CohortTrack``
    is the per-cohort join — so this is a track-level operation, not nested
    under a specific cohort.
    """
    boundary = _transaction(db)
    payload = payload.model_copy(deep=True)
    tenant_id = payload.tenant_id
    track_id = payload.track_id
    ordered_course_ids = payload.ordered_course_ids
    track = db.scalars(
        select(Track).where(Track.tenant_id == tenant_id).where(Track.id == track_id)
    ).first()
    if track is None:
        raise NotFoundError("track not found")

    if len(ordered_course_ids) != len(set(ordered_course_ids)):
        raise BadRequestError("duplicate course id in reorder request")

    rows = db.scalars(
        select(TrackCourse)
        .where(TrackCourse.tenant_id == tenant_id)
        .where(TrackCourse.track_id == track_id)
    ).all()
    by_course_id = {row.course_id: row for row in rows}
    if set(ordered_course_ids) != set(by_course_id):
        raise BadRequestError(
            "ordered_course_ids must match the track's current "
            "course membership exactly"
        )

    for idx, course_id in enumerate(ordered_course_ids, start=1):
        by_course_id[course_id].order_index = idx
    db.flush()
    _unchanged(db, boundary)
    return _track_fact(track)


def cohort_track_or_404(
    db: Session, *, tenant_id: UUID, cohort_id: UUID, track_id: UUID
) -> CohortTrackFact:
    cohort_track = db.scalars(
        select(CohortTrack)
        .join(
            Track,
            (Track.id == CohortTrack.track_id)
            & (Track.tenant_id == CohortTrack.tenant_id),
        )
        .where(CohortTrack.tenant_id == tenant_id)
        .where(CohortTrack.cohort_id == cohort_id)
        .where(CohortTrack.track_id == track_id)
        .where(CohortTrack.status == "active")
        .where(Track.status == "active")
    ).first()
    if cohort_track is None:
        raise NotFoundError("track not found for cohort")
    return _cohort_track_fact(cohort_track)


def ensure_track_offerings(
    db: Session, *, tenant_id: UUID, cohort_id: UUID, track_id: UUID
) -> None:
    boundary = _transaction(db)
    cohort_track_or_404(db, tenant_id=tenant_id, cohort_id=cohort_id, track_id=track_id)
    course_ids = db.scalars(
        select(TrackCourse.course_id)
        .where(TrackCourse.tenant_id == tenant_id)
        .where(TrackCourse.track_id == track_id)
    ).all()
    for course_id in course_ids:
        offering = db.scalars(
            select(CourseOffering)
            .where(CourseOffering.tenant_id == tenant_id)
            .where(CourseOffering.cohort_id == cohort_id)
            .where(CourseOffering.course_id == course_id)
        ).first()
        if offering is None:
            db.add(
                CourseOffering(
                    tenant_id=tenant_id,
                    cohort_id=cohort_id,
                    course_id=course_id,
                    status="active",
                )
            )
        else:
            offering.status = "active"
    db.flush()
    _unchanged(db, boundary)


def assign_enrollment_track(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    person_id: UUID,
    track_id: UUID,
) -> EnrollmentFact:
    boundary = _transaction(db)
    cohort_track_or_404(db, tenant_id=tenant_id, cohort_id=cohort_id, track_id=track_id)
    ensure_track_offerings(
        db, tenant_id=tenant_id, cohort_id=cohort_id, track_id=track_id
    )
    enrollment = db.scalars(
        select(Enrollment)
        .where(Enrollment.tenant_id == tenant_id)
        .where(Enrollment.cohort_id == cohort_id)
        .where(Enrollment.person_id == person_id)
    ).first()
    if enrollment is None:
        raise NotFoundError("enrollment not found")
    enrollment.track_id = track_id
    db.flush()
    _unchanged(db, boundary)
    return enrollment_fact(enrollment)


def clear_enrollment_track(
    db: Session,
    *,
    tenant_id: UUID,
    cohort_id: UUID,
    person_id: UUID,
) -> EnrollmentFact:
    """Recovery path for a learner stuck behind a broken/blocking track.

    Nulls ``Enrollment.track_id`` rather than fabricating a course
    completion — a fabricated completion would trigger completion.py's real
    certificate-issuance side effect for a course the learner never actually
    finished. This does not remove any CourseOffering rows, so the learner
    falls back to full cohort-wide access via the existing null-track code
    path in entitlements.py — that is the intended behavior.
    """
    boundary = _transaction(db)
    enrollment = db.scalars(
        select(Enrollment)
        .where(Enrollment.tenant_id == tenant_id)
        .where(Enrollment.cohort_id == cohort_id)
        .where(Enrollment.person_id == person_id)
    ).first()
    if enrollment is None:
        raise NotFoundError("enrollment not found")
    enrollment.track_id = None
    db.flush()
    _unchanged(db, boundary)
    return enrollment_fact(enrollment)


def default_track_for_cohort(
    db: Session, *, tenant_id: UUID, cohort_id: UUID
) -> TrackFact | None:
    row = (
        db.execute(
            select(Track)
            .join(
                CohortTrack,
                (CohortTrack.track_id == Track.id)
                & (CohortTrack.tenant_id == Track.tenant_id),
            )
            .where(CohortTrack.tenant_id == tenant_id)
            .where(CohortTrack.cohort_id == cohort_id)
            .where(CohortTrack.status == "active")
            .where(Track.status == "active")
            .order_by(Track.created_at, Track.name)
        )
        .scalars()
        .first()
    )
    return _track_fact(row) if row is not None else None


def ensure_default_track_for_cohort(
    db: Session, *, tenant_id: UUID, cohort_id: UUID
) -> TrackFact:
    boundary = _transaction(db)
    existing = default_track_for_cohort(db, tenant_id=tenant_id, cohort_id=cohort_id)
    if existing is not None:
        _unchanged(db, boundary)
        return existing
    cohort = db.scalars(
        select(Cohort)
        .where(Cohort.tenant_id == tenant_id)
        .where(Cohort.id == cohort_id)
    ).first()
    if cohort is None:
        raise NotFoundError("cohort not found for tenant")
    name = f"{cohort.name} Track"
    track = Track(
        tenant_id=tenant_id,
        slug=_unique_track_slug(db, tenant_id=tenant_id, name=name),
        name=name,
        gating_mode="previous",
    )
    db.add(track)
    db.flush()
    db.add(
        CohortTrack(
            tenant_id=tenant_id, cohort_id=cohort_id, track_id=track.id, status="active"
        )
    )
    db.flush()
    _unchanged(db, boundary)
    return _track_fact(track)
