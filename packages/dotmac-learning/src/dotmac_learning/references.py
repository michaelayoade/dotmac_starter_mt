"""Typed reference persistence; authoring/content policy remains with the product.

Activity.course_id and bank_id retain their source plain-UUID meaning. No
curriculum existence/authorization decision is invented by these commands.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Activity, Course, Submission

SCORING_POLICIES: tuple[str, ...] = ("best", "latest", "first")
MAX_ATTEMPT_INTERVAL_SECONDS = 2592000


def _validate_completion_fraction(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not 0 < value <= 1
    ):
        raise ValueError("completion_fraction must be a finite fraction in (0, 1]")
    return float(value)


def _validate_certificate_validity_days(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("certificate_validity_days must be null or at least 1")
    return value


def _validate_prerequisite_composition(value: str) -> str:
    if value not in ("all", "any"):
        raise ValueError(f"unknown prerequisite composition: {value!r}")
    return value


def _validate_scoring_policy(value: str) -> str:
    if value not in SCORING_POLICIES:
        raise ValueError(f"unknown scoring policy: {value!r}")
    return value


def _validate_min_attempt_interval(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 0 <= value <= MAX_ATTEMPT_INTERVAL_SECONDS
    ):
        raise ValueError(
            "min_attempt_interval_seconds must be between 0 and "
            f"{MAX_ATTEMPT_INTERVAL_SECONDS}"
        )
    return value


class CourseWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    slug: str
    title: str
    discipline: str
    source_ref: str
    version: int = 1
    status: str
    listed: bool
    completion_fraction: float = 1.0
    certificate_auto_issue: bool = True
    certificate_validity_days: int | None = None
    prerequisite_composition: str = "all"

    @field_validator("completion_fraction")
    @classmethod
    def validate_completion_fraction(cls, value: float) -> float:
        return _validate_completion_fraction(value)

    @field_validator("certificate_validity_days")
    @classmethod
    def validate_certificate_validity_days(cls, value: int | None) -> int | None:
        return _validate_certificate_validity_days(value)

    @field_validator("prerequisite_composition")
    @classmethod
    def validate_prerequisite_composition(cls, value: str) -> str:
        return _validate_prerequisite_composition(value)


class ActivityWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    tenant_id: UUID
    course_id: UUID
    title: str
    chapter_number: int | None = None
    type: str
    bank_id: UUID | None = None
    pass_threshold: float
    max_attempts: int | None = None
    assessment_mode: str
    grading: str
    weight: float
    question_count: int | None = None
    scoring_policy: str = "best"
    is_final: bool = False
    min_attempt_interval_seconds: int = 0

    @field_validator("scoring_policy")
    @classmethod
    def validate_scoring_policy(cls, value: str) -> str:
        return _validate_scoring_policy(value)

    @field_validator("min_attempt_interval_seconds")
    @classmethod
    def validate_min_attempt_interval_seconds(cls, value: int) -> int:
        return _validate_min_attempt_interval(value)


class ActivityCreate(BaseModel):
    """Mutable plain-coercing create fields; the mapped INSERT assigns the ID."""

    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID
    course_id: UUID
    title: str
    chapter_number: int | None = None
    type: str
    bank_id: UUID | None = None
    pass_threshold: float
    max_attempts: int | None = None
    assessment_mode: str
    grading: str
    weight: float
    question_count: int | None = None
    scoring_policy: str = "best"
    is_final: bool = False
    min_attempt_interval_seconds: int = 0

    @field_validator("scoring_policy")
    @classmethod
    def validate_scoring_policy(cls, value: str) -> str:
        return _validate_scoring_policy(value)

    @field_validator("min_attempt_interval_seconds")
    @classmethod
    def validate_min_attempt_interval_seconds(cls, value: int) -> int:
        return _validate_min_attempt_interval(value)


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
    completion_fraction: float = 1.0
    certificate_auto_issue: bool = True
    certificate_validity_days: int | None = None
    prerequisite_composition: str = "all"

    @field_validator("completion_fraction")
    @classmethod
    def validate_completion_fraction(cls, value: float) -> float:
        return _validate_completion_fraction(value)

    @field_validator("certificate_validity_days")
    @classmethod
    def validate_certificate_validity_days(cls, value: int | None) -> int | None:
        return _validate_certificate_validity_days(value)

    @field_validator("prerequisite_composition")
    @classmethod
    def validate_prerequisite_composition(cls, value: str) -> str:
        return _validate_prerequisite_composition(value)


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
    scoring_policy: str = "best"
    is_final: bool = False
    min_attempt_interval_seconds: int = 0

    @field_validator("scoring_policy")
    @classmethod
    def validate_scoring_policy(cls, value: str) -> str:
        return _validate_scoring_policy(value)

    @field_validator("min_attempt_interval_seconds")
    @classmethod
    def validate_min_attempt_interval_seconds(cls, value: int) -> int:
        return _validate_min_attempt_interval(value)


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
    completion_fraction: float = 1.0
    certificate_auto_issue: bool = True
    certificate_validity_days: int | None = None
    prerequisite_composition: str = "all"


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
    scoring_policy: str = "best"
    is_final: bool = False
    min_attempt_interval_seconds: int = 0


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


def _add_activity(db: Session, values: dict[str, object]) -> Activity:
    """Add raw owner fields without coercion or a flush."""
    row = Activity(**values)
    db.add(row)
    return row


def _assign_activity(row: Activity, values: Iterable[tuple[str, object]]) -> None:
    """Preserve caller field order and raw values."""
    for name, value in values:
        setattr(row, name, value)


def create_activity_identity(db: Session, payload: ActivityCreate) -> UUID:
    """Flush the entire caller Session; read only the INSERT-generated identity."""
    row = _add_activity(db, payload.model_dump())
    db.flush()
    return row.id


def _refuse_scoring_policy_change(db: Session, row: Activity, new_policy: str) -> None:
    """Refuse a scoring-policy CHANGE once submissions exist (RD-3).

    Projections are read-time, so changing the policy would silently
    re-derive every existing learner's winning score. Authoring a policy
    before any submission, or re-stating the current policy, is allowed.
    """
    if new_policy == row.scoring_policy:
        return
    submitted = db.scalar(
        select(Submission.id).where(
            Submission.tenant_id == row.tenant_id,
            Submission.activity_id == row.id,
        )
    )
    if submitted is not None:
        raise ValueError("scoring_policy cannot change on an activity with submissions")


def stage_activity_patch(db: Session, payload: ActivityPatch) -> bool:
    """Stage supplied fields, tolerate missing rows, and avoid fact projection.

    Trusted-import path: unlike :func:`patch_activity`, this raw staging
    helper does NOT apply the scoring-policy prospective guard (RD-3).
    Only callers that own the import decision may use it to restate a
    policy on an activity that already has submissions.
    """
    row = db.scalar(
        select(Activity).where(
            Activity.tenant_id == payload.tenant_id, Activity.id == payload.id
        )
    )
    if row is None:
        return False
    _assign_activity(
        row,
        payload.model_dump(exclude={"id", "tenant_id"}, exclude_unset=True).items(),
    )
    return True


def create_activity(db: Session, payload: ActivityWrite) -> ActivityFact:
    row = _add_activity(db, payload.model_dump())
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
    _refuse_scoring_policy_change(db, row, payload.scoring_policy)
    _assign_activity(row, payload.model_dump(exclude={"id", "tenant_id"}).items())
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
    supplied = payload.model_dump(exclude={"id", "tenant_id"}, exclude_unset=True)
    if "scoring_policy" in supplied:
        _refuse_scoring_policy_change(db, row, payload.scoring_policy)
    _assign_activity(row, supplied.items())
    return _activity_fact(row)
