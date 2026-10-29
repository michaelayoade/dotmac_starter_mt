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
from .cohort_contracts import (
    CohortIntakePatch,
    CreateCohortCommand,
    RemoveFacilitatorCommand,
)
from .curriculum_models import QuestionBank
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


def patch_cohort_intake(db: Session, patch: CohortIntakePatch) -> None:
    """Stage supplied intake fields in the existing caller transaction.

    Normal lookup autoflush is preserved; there is no ending flush or commit.
    Bank IDs retain their legacy logical-bank meaning, with tenant-bound
    existence validation only. No bank version or admissions action is chosen.
    """
    boundary = _transaction(db)
    cohort = db.scalar(
        select(Cohort).where(
            Cohort.tenant_id == patch.tenant_id, Cohort.id == patch.cohort_id
        )
    )
    if cohort is None:
        raise ParticipationNotFound("cohort not found for tenant")
    supplied = patch.model_dump(exclude={"tenant_id", "cohort_id"}, exclude_unset=True)
    if "entrance_bank_id" in supplied and patch.entrance_bank_id is not None:
        bank = db.scalar(
            select(QuestionBank.id).where(
                QuestionBank.tenant_id == patch.tenant_id,
                QuestionBank.id == patch.entrance_bank_id,
            )
        )
        if bank is None:
            raise ParticipationNotFound("entrance bank not found for tenant")
    for name, value in supplied.items():
        setattr(cohort, name, value)
    _unchanged(db, boundary)
