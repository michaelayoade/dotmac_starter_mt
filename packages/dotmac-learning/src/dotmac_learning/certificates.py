"""Certificate record issuance; presentation and delivery stay with products."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from .completion import lock_existing_completion
from .event_contracts import EventWrite
from .events import emit
from .models import Certificate
from .progress_contracts import CertificateCommand, CertificateFact, CertificateIssue


class CertificateNotEligible(ValueError):
    """The course has no completed aggregate and no issued certificate."""


def _serial() -> str:
    return f"CERT-{uuid4().hex[:12].upper()}"


def _fact(cert: Certificate) -> CertificateFact:
    return CertificateFact(
        id=cert.id,
        tenant_id=cert.tenant_id,
        person_id=cert.person_id,
        course_id=cert.course_id,
        serial=cert.serial,
        issued_at=cert.issued_at,
        created_at=cert.created_at,
        updated_at=cert.updated_at,
    )


def get_certificate(
    db: Session, *, tenant_id: UUID, person_id: UUID, course_id: UUID
) -> CertificateFact | None:
    """Read an issued record without completion eligibility or issuance effects."""
    result = db.execute(
        select(Certificate, *Certificate.__table__.c).where(
            Certificate.tenant_id == tenant_id,
            Certificate.person_id == person_id,
            Certificate.course_id == course_id,
        )
    ).first()
    if result is None:
        return None
    cert = result[0]
    untouched = inspect(cert).unmodified
    for column, fresh_value in zip(
        Certificate.__table__.columns, result[1:], strict=True
    ):
        if column.key in untouched:
            set_committed_value(cert, column.key, fresh_value)
    return _fact(cert)


def issue_certificate(db: Session, payload: CertificateCommand) -> CertificateIssue:
    if not db.in_transaction():
        raise ValueError("certificate issuance requires a caller transaction")
    query = select(Certificate).where(
        Certificate.tenant_id == payload.tenant_id,
        Certificate.person_id == payload.person_id,
        Certificate.course_id == payload.course_id,
    )
    existing = db.scalars(query).first()
    if existing is not None:
        return CertificateIssue(certificate=_fact(existing), created=False)
    completion = lock_existing_completion(
        db,
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        course_id=payload.course_id,
    )
    # Lock wait may have allowed the first issuer to commit its certificate.
    existing = db.scalars(query).first()
    if existing is not None:
        return CertificateIssue(certificate=_fact(existing), created=False)
    if completion is None or completion.status != "completed":
        raise CertificateNotEligible(
            "course not completed; certificate cannot be issued"
        )
    cert = Certificate(
        tenant_id=payload.tenant_id,
        person_id=payload.person_id,
        course_id=payload.course_id,
        serial=_serial(),
        issued_at=payload.now or datetime.now(UTC),
    )
    db.add(cert)
    # Required consequence: never hide insertion/flush failure in a savepoint.
    db.flush()
    event = emit(
        db,
        EventWrite(
            tenant_id=payload.tenant_id,
            person_id=payload.person_id,
            kind="certificate_earned",
            course_id=payload.course_id,
            subject_id=payload.course_id,
            detail={"serial": cert.serial},
        ),
    )
    return CertificateIssue(certificate=_fact(cert), created=True, event=event)
