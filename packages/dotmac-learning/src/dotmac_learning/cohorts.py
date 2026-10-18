"""The source's direct cohort writes in an existing caller transaction.

The product retains instructor/admin route authorization and supplies trusted
tenant context. Creation stages only: generated identity, timestamps and
delivery defaults belong to the caller's later flush. Removal retains source
autoflush and query order, delegating the status change to the roster owner.
Callers own rollback, including after partial writes or propagated DB errors.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import roster
from .cohort_contracts import CreateCohortCommand, RemoveFacilitatorCommand
from .participation_contracts import (
    EnrollmentFact,
    ParticipationForbidden,
    ParticipationNotFound,
    RosterAuthority,
    RosterStateCommand,
)
from .participation_models import Cohort, Enrollment
from .results import _transaction, _unchanged


def create_cohort(db: Session, command: CreateCohortCommand) -> None:
    """Stage the raw source values without SQL, flushing or fabricated facts."""
    boundary = _transaction(db)
    db.add(
        Cohort(
            tenant_id=command.tenant_id,
            name=command.name,
            discipline=command.discipline,
            status="active",
        )
    )
    _unchanged(db, boundary)


def remove_facilitator(
    db: Session, command: RemoveFacilitatorCommand, *, authority: RosterAuthority
) -> EnrollmentFact:
    boundary = _transaction(db)
    roster._authority(command.tenant_id, authority)
    if not authority.actor_is_admin:
        raise ParticipationForbidden("Forbidden")
    cohort = db.scalars(
        select(Cohort).where(
            Cohort.tenant_id == command.tenant_id, Cohort.id == command.cohort_id
        )
    ).first()
    if cohort is None:
        raise ParticipationNotFound("cohort not found for tenant")
    enrollment = db.scalars(
        select(Enrollment).where(
            Enrollment.tenant_id == command.tenant_id,
            Enrollment.cohort_id == command.cohort_id,
            Enrollment.person_id == command.person_id,
            Enrollment.role_in_cohort == "instructor",
        )
    ).first()
    if enrollment is None:
        raise ParticipationNotFound("facilitator enrollment not found")
    fact = roster.set_roster_state(
        db,
        RosterStateCommand(
            tenant_id=command.tenant_id,
            cohort_id=command.cohort_id,
            person_id=command.person_id,
            state="dropped",
        ),
        authority=authority,
    )
    _unchanged(db, boundary)
    return fact
