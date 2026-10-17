"""Explicit ERP roster classification, without employment or email-domain guesses.

ERP owns employee references. Matching keys use source strip/lower semantics;
reference bytes are preserved. Existing unmatched classifications remain intact
unless the caller explicitly asserts that the supplied roster is complete.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.models import Party, PartyPerson
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .participation_contracts import (
    AudienceCounts,
    ClassificationCounts,
    ClassifyRosterCommand,
    UnclassifiedPerson,
)
from .participation_models import Enrollment
from .results import _transaction, _unchanged

STAFF = "staff"
EXTERNAL = "external"
AUDIENCES = (STAFF, EXTERNAL)


def classify_from_roster(
    db: Session, command: ClassifyRosterCommand
) -> ClassificationCounts:
    """Mutate active student rows and flush inside the caller's transaction.

    Falsey email/reference entries are discarded; the last colliding normalized
    key wins. Source queries may autoflush pending caller writes and errors are
    propagated for caller rollback. No savepoint or atomic-refusal promise.
    """
    boundary = _transaction(db)
    normalized = {
        (email or "").strip().lower(): ref
        for email, ref in command.roster.items()
        if email and ref
    }
    staff = external = unclassified_count = unchanged = 0
    rows = db.execute(
        select(Enrollment, Party.email)
        .join(
            Party,
            (Party.id == Enrollment.person_id)
            & (Party.tenant_id == Enrollment.tenant_id),
        )
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .where(
            Enrollment.tenant_id == command.tenant_id,
            Enrollment.status == "active",
            Enrollment.role_in_cohort == "student",
        )
    ).all()
    for row, email in rows:
        ref = normalized.get((email or "").strip().lower())
        if ref is not None:
            if row.audience == STAFF and row.employee_ref == ref:
                unchanged += 1
                continue
            row.audience = STAFF
            row.employee_ref = ref
            staff += 1
        elif command.mark_rest_external:
            if row.audience == EXTERNAL:
                unchanged += 1
                continue
            row.audience = EXTERNAL
            row.employee_ref = None
            external += 1
        else:
            unclassified_count += 1
    db.flush()
    _unchanged(db, boundary)
    return ClassificationCounts(staff, external, unclassified_count, unchanged)


def unclassified(db: Session, *, tenant_id: UUID) -> tuple[UnclassifiedPerson, ...]:
    """Distinct email/first/last source rows in email order, with source names."""
    rows = db.execute(
        select(Party.email, PartyPerson.first_name, PartyPerson.last_name)
        .select_from(Party)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .join(
            Enrollment,
            (Enrollment.person_id == Party.id)
            & (Enrollment.tenant_id == Party.tenant_id),
        )
        .where(
            Enrollment.tenant_id == tenant_id,
            Enrollment.status == "active",
            Enrollment.role_in_cohort == "student",
            Enrollment.audience.is_(None),
        )
        .distinct()
        .order_by(Party.email)
    ).all()
    return tuple(
        UnclassifiedPerson(email, f"{first or ''} {last or ''}".strip())
        for email, first, last in rows
    )


def counts_by_audience(db: Session, *, tenant_id: UUID) -> AudienceCounts:
    """Count distinct people per audience, including people in several groups."""
    counts = {STAFF: 0, EXTERNAL: 0, "unclassified": 0}
    for value, count in db.execute(
        select(Enrollment.audience, func.count(func.distinct(Enrollment.person_id)))
        .where(
            Enrollment.tenant_id == tenant_id,
            Enrollment.status == "active",
            Enrollment.role_in_cohort == "student",
        )
        .group_by(Enrollment.audience)
    ).all():
        counts[value or "unclassified"] = int(count)
    return AudienceCounts(counts[STAFF], counts[EXTERNAL], counts["unclassified"])


def staff_person_ids(db: Session, *, tenant_id: UUID) -> tuple[UUID, ...]:
    return tuple(
        db.scalars(
            select(Enrollment.person_id)
            .where(
                Enrollment.tenant_id == tenant_id,
                Enrollment.status == "active",
                Enrollment.role_in_cohort == "student",
                Enrollment.audience == STAFF,
            )
            .distinct()
        ).all()
    )
