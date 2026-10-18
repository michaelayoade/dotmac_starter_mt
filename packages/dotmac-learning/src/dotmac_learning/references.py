"""Typed reference persistence; authoring/content policy remains with the product.

Activity.course_id and bank_id retain their source plain-UUID meaning. No
curriculum existence/authorization decision is invented by these commands.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Activity, Course


class CourseWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    slug: str
    title: str
    discipline: str
    source_ref: str
    version: int = 1
    status: str = "published"
    listed: bool = False


class ActivityWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    course_id: UUID
    title: str
    chapter_number: int | None = None
    type: str = "mcq_test"
    bank_id: UUID | None = None
    pass_threshold: float = 0.0
    max_attempts: int | None = None
    assessment_mode: str = "graded"
    grading: str = "auto"
    weight: float = 1.0
    question_count: int | None = None


class CoursePatch(BaseModel):
    """Stage only provided fields; authoring/status policy belongs to the product.

    Defaults permit omission and are never applied unless explicitly supplied.
    Nonnullable column types deliberately reject an explicit null.
    """

    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    slug: str = ""
    title: str = ""
    discipline: str = ""
    source_ref: str = ""
    version: int = 1
    status: str = "published"
    listed: bool = False


class ActivityPatch(BaseModel):
    """Stage supplied reference/policy fields, preserving omitted values.

    Defaults are ignored for unset fields, including the plain UUID reference.
    Explicit null clears only the nullable columns, matching ActivityWrite.
    """

    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    course_id: UUID = UUID(int=0)
    title: str = ""
    chapter_number: int | None = None
    type: str = "mcq_test"
    bank_id: UUID | None = None
    pass_threshold: float = 0.0
    max_attempts: int | None = None
    assessment_mode: str = "graded"
    grading: str = "auto"
    weight: float = 1.0
    question_count: int | None = None


@dataclass(frozen=True, slots=True)
class CourseFact:
    id: UUID
    tenant_id: UUID
    slug: str
    title: str
    discipline: str
    source_ref: str
    version: int
    status: str
    listed: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ActivityFact:
    id: UUID
    tenant_id: UUID
    course_id: UUID
    title: str
    chapter_number: int | None
    type: str
    bank_id: UUID | None
    pass_threshold: float
    max_attempts: int | None
    assessment_mode: str
    grading: str
    weight: float
    question_count: int | None
    created_at: datetime
    updated_at: datetime


class ReferenceNotFound(ValueError):
    pass


def _course_fact(row: Course) -> CourseFact:
    return CourseFact(
        **{field: getattr(row, field) for field in CourseFact.__dataclass_fields__}
    )


def _activity_fact(row: Activity) -> ActivityFact:
    return ActivityFact(
        **{field: getattr(row, field) for field in ActivityFact.__dataclass_fields__}
    )


def get_course(db: Session, *, tenant_id: UUID, course_id: UUID) -> CourseFact | None:
    row = db.scalar(
        select(Course)
        .where(Course.tenant_id == tenant_id, Course.id == course_id)
        .execution_options(populate_existing=True)
    )
    return _course_fact(row) if row is not None else None


def get_activity(
    db: Session, *, tenant_id: UUID, activity_id: UUID
) -> ActivityFact | None:
    row = db.scalar(
        select(Activity)
        .where(Activity.tenant_id == tenant_id, Activity.id == activity_id)
        .execution_options(populate_existing=True)
    )
    return _activity_fact(row) if row is not None else None


def create_course(db: Session, payload: CourseWrite) -> CourseFact:
    row = Course(**payload.model_dump())
    db.add(row)
    db.flush()
    return _course_fact(row)


def update_course(db: Session, payload: CourseWrite) -> CourseFact:
    """Replace supplied reference fields; product has already decided the change."""
    row = db.scalar(
        select(Course).where(
            Course.tenant_id == payload.tenant_id, Course.id == payload.id
        )
    )
    if row is None:
        raise ReferenceNotFound("course not found for tenant")
    for name, value in payload.model_dump(exclude={"id", "tenant_id"}).items():
        setattr(row, name, value)
    db.flush()
    return _course_fact(row)


def create_activity(db: Session, payload: ActivityWrite) -> ActivityFact:
    row = Activity(**payload.model_dump())
    db.add(row)
    db.flush()
    return _activity_fact(row)


def update_activity(db: Session, payload: ActivityWrite) -> ActivityFact:
    row = db.scalar(
        select(Activity).where(
            Activity.tenant_id == payload.tenant_id, Activity.id == payload.id
        )
    )
    if row is None:
        raise ReferenceNotFound("activity not found for tenant")
    for name, value in payload.model_dump(exclude={"id", "tenant_id"}).items():
        setattr(row, name, value)
    db.flush()
    return _activity_fact(row)


def patch_course(db: Session, payload: CoursePatch) -> CourseFact:
    """Stage selective changes in the caller's Session without an ending flush."""
    row = db.scalar(
        select(Course).where(
            Course.tenant_id == payload.tenant_id, Course.id == payload.id
        )
    )
    if row is None:
        raise ReferenceNotFound("course not found for tenant")
    for name, value in payload.model_dump(
        exclude={"id", "tenant_id"}, exclude_unset=True
    ).items():
        setattr(row, name, value)
    return _course_fact(row)


def patch_activity(db: Session, payload: ActivityPatch) -> ActivityFact:
    """Stage source update fields; the caller owns flushing and missing-row policy."""
    row = db.scalar(
        select(Activity).where(
            Activity.tenant_id == payload.tenant_id, Activity.id == payload.id
        )
    )
    if row is None:
        raise ReferenceNotFound("activity not found for tenant")
    for name, value in payload.model_dump(
        exclude={"id", "tenant_id"}, exclude_unset=True
    ).items():
        setattr(row, name, value)
    return _activity_fact(row)
