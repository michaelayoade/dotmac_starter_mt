"""Completion recomputation serialized on its source aggregate row.

Since lr_0012 the SQL capability ``mod_learning.recompute_completion_v1``
is the sole recompute (and auto-issue) writer; this module delegates to
it and rebuilds the detached result. The lock helpers below remain for
callers that serialize on the aggregate row themselves.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import Select, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from . import events
from .models import Certificate, CourseCompletion
from .progress_contracts import (
    CertificateFact,
    CertificateIssue,
    CompletionCommand,
    CompletionFact,
    CompletionResult,
)


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


_RECOMPUTE_CALL = text(
    "SELECT completion_id, first_completion, certificate_id, "
    "certificate_created, cert_event_status, cert_event_id "
    "FROM mod_learning.recompute_completion_v1("
    "CAST(:tenant_id AS pg_catalog.uuid), CAST(:person_id AS pg_catalog.uuid), "
    "CAST(:course_id AS pg_catalog.uuid), "
    "CAST(:now AS pg_catalog.timestamptz))"
)


def _expire_touched(db: Session, payload: CompletionCommand) -> None:
    """Expire identity-map rows the SQL writer may have changed."""
    for obj in list(db.identity_map.values()):
        if (
            isinstance(obj, CourseCompletion | Certificate)
            and obj.tenant_id == payload.tenant_id
            and obj.person_id == payload.person_id
            and obj.course_id == payload.course_id
        ):
            db.expire(obj)


def recompute_completion(db: Session, payload: CompletionCommand) -> CompletionResult:
    """Flush required progress/certificate writes in the supplied transaction.

    Delegates to the lr_0012 SQL capability, the sole recompute writer
    (flush first, then targeted expire, as in the lab delegation). The
    caller's role needs EXECUTE on both lr_0012 capabilities, which
    product composition grants; Learning grants them to nobody.
    Course policy columns are read inside the capability (RD-2): the
    command shape is unchanged, and a missing Course row keeps the
    historical rules (fraction 1.0, auto-issue on).

    Supported concurrency uses READ COMMITTED, as in the source service.
    A lock wait does not refresh a preexisting REPEATABLE READ snapshot.
    """
    if not db.in_transaction():
        raise ValueError("completion requires a caller transaction")
    db.flush()
    row = (
        db.execute(
            _RECOMPUTE_CALL,
            {
                "tenant_id": payload.tenant_id,
                "person_id": payload.person_id,
                "course_id": payload.course_id,
                "now": payload.now,
            },
        )
        .mappings()
        .one()
    )
    _expire_touched(db, payload)
    rec = db.get(CourseCompletion, row["completion_id"])
    if rec is None:
        raise RuntimeError("completion row is unavailable after delegation")
    certificate = None
    if row["certificate_id"] is not None:
        cert = db.get(Certificate, row["certificate_id"])
        if cert is None:
            raise RuntimeError("certificate row is unavailable after delegation")
        created = bool(row["certificate_created"])
        certificate = CertificateIssue(
            certificate=CertificateFact(
                id=cert.id,
                tenant_id=cert.tenant_id,
                person_id=cert.person_id,
                course_id=cert.course_id,
                serial=cert.serial,
                issued_at=cert.issued_at,
                created_at=cert.created_at,
                updated_at=cert.updated_at,
                expires_at=cert.expires_at,
            ),
            created=created,
            event=(
                events.emission_for(db, row["cert_event_status"], row["cert_event_id"])
                if created
                else None
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
        first_completion=bool(row["first_completion"]),
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
