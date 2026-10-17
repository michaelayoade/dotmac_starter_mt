"""Source-preserving roster writes in an existing caller transaction.

No identity provisioning, credentials or role writes. Product authorization is
supplied as trusted scoped facts. Queries retain source autoflush and races;
callers own rollback, including after partial writes or propagated DB errors.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.models import Party, PartyPerson
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import tracks
from .participation_contracts import (
    ActivateEnrollmentCommand,
    ActivationResult,
    AssignFacilitatorCommand,
    BulkEnrollCommand,
    BulkEnrollResult,
    EnrollmentFact,
    FacilitatorEligibility,
    ParticipationBadRequest,
    ParticipationForbidden,
    ParticipationNotFound,
    RosterAuthority,
    RosterStateCommand,
    enrollment_fact,
)
from .participation_models import Cohort, Enrollment
from .results import _transaction, _unchanged

ROSTER_STATES = frozenset({"active", "waitlisted", "dropped"})


def _authority(tenant_id: UUID, authority: RosterAuthority) -> None:
    if authority.tenant_id != tenant_id:
        raise ParticipationForbidden("roster authority does not cover tenant")


def _cohort(db: Session, tenant_id: UUID, cohort_id: UUID) -> None:
    if (
        db.scalar(
            select(Cohort.id).where(
                Cohort.tenant_id == tenant_id, Cohort.id == cohort_id
            )
        )
        is None
    ):
        raise ParticipationNotFound("cohort not found for tenant")


def _enrollment(
    db: Session, tenant_id: UUID, cohort_id: UUID, person_id: UUID
) -> Enrollment | None:
    return db.scalars(
        select(Enrollment).where(
            Enrollment.tenant_id == tenant_id,
            Enrollment.cohort_id == cohort_id,
            Enrollment.person_id == person_id,
        )
    ).first()


def _activate_enrollment(
    enrollment: Enrollment, *, authority: RosterAuthority
) -> ActivationResult:
    """Loaded internal helper: no query or flush, and active no-op comes first."""
    _authority(enrollment.tenant_id, authority)
    if enrollment.status == "active":
        return ActivationResult("already_active", "active")
    if enrollment.role_in_cohort != "student" and not authority.actor_is_admin:
        raise ParticipationBadRequest(
            "only an admin can reactivate a non-student cohort enrollment"
        )
    previous_status = enrollment.status
    enrollment.status = "active"
    return ActivationResult("reactivated", previous_status)


def activate_enrollment(
    db: Session, command: ActivateEnrollmentCommand, *, authority: RosterAuthority
) -> ActivationResult:
    """ID wrapper: its lookup can autoflush pending caller writes.

    The loaded activation itself remains flush-free, as in Academy. This wrapper
    does not explicitly flush, commit, rollback, retry or open a savepoint.
    """
    boundary = _transaction(db)
    _authority(command.tenant_id, authority)
    row = db.scalars(
        select(Enrollment).where(
            Enrollment.tenant_id == command.tenant_id,
            Enrollment.id == command.enrollment_id,
        )
    ).first()
    if row is None:
        raise ParticipationNotFound("enrollment not found")
    result = _activate_enrollment(row, authority=authority)
    _unchanged(db, boundary)
    return result


def assign_facilitator(
    db: Session,
    command: AssignFacilitatorCommand,
    *,
    authority: RosterAuthority,
    eligibility: FacilitatorEligibility,
) -> EnrollmentFact:
    boundary = _transaction(db)
    _authority(command.tenant_id, authority)
    if not authority.actor_is_admin:
        raise ParticipationBadRequest("only an admin can assign a cohort facilitator")
    if (eligibility.tenant_id, eligibility.person_id) != (
        command.tenant_id,
        command.person_id,
    ):
        raise ParticipationForbidden(
            "facilitator eligibility does not cover person and tenant"
        )
    _cohort(db, command.tenant_id, command.cohort_id)
    person = db.scalars(
        select(Party)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .where(Party.tenant_id == command.tenant_id, Party.id == command.person_id)
    ).first()
    if person is None:
        raise ParticipationNotFound("person not found for tenant")
    if not eligibility.account_is_active:
        raise ParticipationBadRequest("facilitator must be an active user")
    if not eligibility.has_instructor_role:
        raise ParticipationBadRequest("facilitator must have the instructor role")
    row = _enrollment(db, command.tenant_id, command.cohort_id, command.person_id)
    if row is None:
        row = Enrollment(
            tenant_id=command.tenant_id,
            cohort_id=command.cohort_id,
            person_id=command.person_id,
            role_in_cohort="instructor",
            status="active",
        )
        db.add(row)
    else:
        row.role_in_cohort = "instructor"
        row.track_id = None
        _activate_enrollment(row, authority=authority)
    db.flush()
    fact = enrollment_fact(row)
    _unchanged(db, boundary)
    return fact


def _normalize_emails(emails: tuple[str | None, ...]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for raw in emails:
        email = (raw or "").strip().lower()
        if email:
            seen.setdefault(email, None)
    return tuple(seen)


def bulk_enroll(
    db: Session, command: BulkEnrollCommand, *, authority: RosterAuthority
) -> BulkEnrollResult:
    boundary = _transaction(db)
    _authority(command.tenant_id, authority)
    _cohort(db, command.tenant_id, command.cohort_id)
    if command.track_id is not None:
        tracks.ensure_track_offerings(
            db,
            tenant_id=command.tenant_id,
            cohort_id=command.cohort_id,
            track_id=command.track_id,
        )
    enrolled: list[str] = []
    reactivated: list[str] = []
    already_active: list[str] = []
    not_found: list[str] = []
    admin_required: list[str] = []
    for email in _normalize_emails(command.emails):
        person = db.scalars(
            select(Party)
            .join(PartyPerson, PartyPerson.party_id == Party.id)
            .where(
                Party.tenant_id == command.tenant_id,
                func.lower(func.btrim(Party.email)) == email,
            )
        ).first()
        if person is None:
            not_found.append(email)
            continue
        row = _enrollment(db, command.tenant_id, command.cohort_id, person.id)
        if row is None:
            db.add(
                Enrollment(
                    tenant_id=command.tenant_id,
                    cohort_id=command.cohort_id,
                    person_id=person.id,
                    track_id=command.track_id,
                    role_in_cohort="student",
                    status="active",
                )
            )
            enrolled.append(email)
        elif row.status != "active":
            try:
                _activate_enrollment(row, authority=authority)
            except ParticipationBadRequest:
                admin_required.append(email)
                continue
            if command.track_id is not None:
                tracks.assign_enrollment_track(
                    db,
                    tenant_id=command.tenant_id,
                    cohort_id=command.cohort_id,
                    person_id=person.id,
                    track_id=command.track_id,
                )
            reactivated.append(email)
        else:
            if command.track_id is not None and row.track_id != command.track_id:
                tracks.assign_enrollment_track(
                    db,
                    tenant_id=command.tenant_id,
                    cohort_id=command.cohort_id,
                    person_id=person.id,
                    track_id=command.track_id,
                )
            already_active.append(email)
    db.flush()
    _unchanged(db, boundary)
    return BulkEnrollResult(
        tuple(enrolled),
        tuple(reactivated),
        tuple(already_active),
        tuple(not_found),
        tuple(admin_required),
    )


def set_roster_state(
    db: Session, command: RosterStateCommand, *, authority: RosterAuthority
) -> EnrollmentFact:
    boundary = _transaction(db)
    _authority(command.tenant_id, authority)
    # This was the web caller's decision; retain its order before state validation.
    row = _enrollment(db, command.tenant_id, command.cohort_id, command.person_id)
    if (
        row is not None
        and row.role_in_cohort != "student"
        and not authority.actor_is_admin
    ):
        raise ParticipationForbidden("Forbidden")
    if command.state not in ROSTER_STATES:
        raise ParticipationNotFound(f"invalid roster state: {command.state}")
    if row is None:
        raise ParticipationNotFound("enrollment not found")
    row.status = command.state
    db.flush()
    fact = enrollment_fact(row)
    _unchanged(db, boundary)
    return fact
