"""Completion recomputation serialized on its source aggregate row."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .models import Activity, Course, CourseCompletion
from .progress_contracts import (
    CertificateCommand,
    CompletionCommand,
    CompletionFact,
    CompletionResult,
)
from .projections import best_scores_for


def _locked_query(
    *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> Select[tuple[CourseCompletion]]:
    return (
        select(CourseCompletion)
        .where(
            CourseCompletion.tenant_id == tenant_id,
            CourseCompletion.person_id == person_id,
            CourseCompletion.course_id == course_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def lock_existing_completion(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> CourseCompletion | None:
    return db.scalars(
        _locked_query(tenant_id=tenant_id, person_id=person_id, course_id=course_id)
    ).first()


def ensure_locked_completion(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> CourseCompletion:
    db.execute(
        insert(CourseCompletion)
        .values(
            id=uuid4(),
            tenant_id=tenant_id,
            person_id=person_id,
            course_id=course_id,
            pct=0.0,
            status="in_progress",
        )
        .on_conflict_do_nothing(
            index_elements=[
                CourseCompletion.tenant_id,
                CourseCompletion.person_id,
                CourseCompletion.course_id,
            ]
        )
    )
    return db.scalars(
        _locked_query(tenant_id=tenant_id, person_id=person_id, course_id=course_id)
    ).one()


def recompute_completion(db: Session, payload: CompletionCommand) -> CompletionResult:
    """Flush required progress/certificate writes in the supplied transaction.

    Supported concurrency uses READ COMMITTED, as in the source service.
    A lock wait does not refresh a preexisting REPEATABLE READ snapshot.
    """
    if not db.in_transaction():
        raise ValueError("completion requires a caller transaction")
    rec = ensure_locked_completion(
        db,
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        course_id=payload.course_id,
    )
    total = (
        db.scalar(
            select(func.count())
            .select_from(Activity)
            .where(
                Activity.tenant_id == payload.tenant_id,
                Activity.course_id == payload.course_id,
            )
        )
        or 0
    )
    best = best_scores_for(
        db,
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        course_id=payload.course_id,
    )
    passed = sum(1 for score in best.values() if score.passed)
    # Course policy columns are read inside the handler (RD-2): the command
    # shape is unchanged. A missing Course row keeps the historical rules
    # (fraction 1.0, auto-issue on), matching pre-column behavior exactly.
    course = db.scalars(
        select(Course).where(
            Course.tenant_id == payload.tenant_id, Course.id == payload.course_id
        )
    ).first()
    completion_fraction = course.completion_fraction if course is not None else 1.0
    is_complete = total > 0 and passed / total >= completion_fraction
    first_completion = is_complete and rec.completed_at is None
    rec.pct = passed / total if total else 0.0
    if is_complete:
        rec.status = "completed"
        if rec.completed_at is None:
            rec.completed_at = payload.now or datetime.now(UTC)
    else:
        rec.status = "in_progress"
        rec.completed_at = None
    db.flush()
    certificate = None
    auto_issue = course.certificate_auto_issue if course is not None else True
    if first_completion and auto_issue:
        from .certificates import issue_certificate

        certificate = issue_certificate(
            db,
            CertificateCommand(
                tenant_id=payload.tenant_id,
                person_id=payload.person_id,
                course_id=payload.course_id,
                now=payload.now,
            ),
        )
    return CompletionResult(
        completion=CompletionFact(
            id=rec.id,
            tenant_id=rec.tenant_id,
            person_id=rec.person_id,
            course_id=rec.course_id,
            status=rec.status,
            pct=rec.pct,
            completed_at=rec.completed_at,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        ),
        first_completion=first_completion,
        certificate=certificate,
    )


def get_completion(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> CompletionFact | None:
    """Read the stored aggregate without locking, recomputing or issuing.

    This is not a visibility or certificate-eligibility decision. The supplied
    Session retains its normal query/autoflush behavior and caller ownership.
    """
    row = db.scalars(
        select(CourseCompletion).where(
            CourseCompletion.tenant_id == tenant_id,
            CourseCompletion.person_id == person_id,
            CourseCompletion.course_id == course_id,
        )
    ).first()
    if row is None:
        return None
    return CompletionFact(
        id=row.id,
        tenant_id=row.tenant_id,
        person_id=row.person_id,
        course_id=row.course_id,
        status=row.status,
        pct=row.pct,
        completed_at=row.completed_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
