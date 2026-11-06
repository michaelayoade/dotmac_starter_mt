"""Certificate record issuance; presentation and delivery stay with products.

Since lr_0012 the SQL capability ``mod_learning.issue_certificate_v1``
is the sole certificate writer; ``issue_certificate`` delegates to it
and rebuilds the detached result.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from . import events
from .models import Certificate
from .progress_contracts import CertificateCommand, CertificateFact, CertificateIssue


class CertificateNotEligible(ValueError):
    """The course has no completed aggregate and no issued certificate."""


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
        expires_at=cert.expires_at,
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


_ISSUE_CALL = text(
    "SELECT outcome, certificate_id, certificate_created, event_status, event_id "
    "FROM mod_learning.issue_certificate_v1("
    "CAST(:tenant_id AS pg_catalog.uuid), CAST(:person_id AS pg_catalog.uuid), "
    "CAST(:course_id AS pg_catalog.uuid), "
    "CAST(:now AS pg_catalog.timestamptz))"
)


def issue_certificate(db: Session, payload: CertificateCommand) -> CertificateIssue:
    """Issue (or return the existing) certificate through the SQL writer.

    The capability rechecks for an existing row after locking the
    completion aggregate (a lock wait may have allowed the first
    issuer to commit), reads validity from the Course row (RD-2;
    existing rows keep their stored expiry), and never hides the
    certificate insertion failure: only the ``certificate_earned``
    event append is isolated as an outcome.

    Ineligibility comes back as an outcome rather than a SQL error, so
    ``CertificateNotEligible`` is raised here without aborting the
    caller's transaction. The caller's role needs EXECUTE on the
    capability, which product composition grants; Learning grants it
    to nobody.
    """
    if not db.in_transaction():
        raise ValueError("certificate issuance requires a caller transaction")
    db.flush()
    row = (
        db.execute(
            _ISSUE_CALL,
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
    if row["outcome"] == "not_eligible":
        raise CertificateNotEligible(
            "course not completed; certificate cannot be issued"
        )
    for obj in list(db.identity_map.values()):
        if (
            isinstance(obj, Certificate)
            and obj.tenant_id == payload.tenant_id
            and obj.person_id == payload.person_id
            and obj.course_id == payload.course_id
        ):
            db.expire(obj)
    cert = db.get(Certificate, row["certificate_id"])
    if cert is None:
        raise RuntimeError("certificate row is unavailable after delegation")
    created = bool(row["certificate_created"])
    return CertificateIssue(
        certificate=_fact(cert),
        created=created,
        event=(
            events.emission_for(db, row["event_status"], row["event_id"])
            if created
            else None
        ),
    )
