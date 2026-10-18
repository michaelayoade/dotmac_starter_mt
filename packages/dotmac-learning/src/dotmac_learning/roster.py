"""Source-preserving roster writes in an existing caller transaction.

No identity provisioning, credentials or role writes. Product authorization is
supplied as trusted scoped facts. Roster state changes lock fresh authorization
fields and reject pending target changes; other queries retain source autoflush
and races. Callers own rollback, including after partial writes or DB errors.
"""

from __future__ import annotations

from dataclasses import replace
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
    EnsureAdmissionEnrollmentCommand,
    EnsureInvitationEnrollmentCommand,
    FacilitatorEligibility,
    InvitationEnrollmentResult,
    ParticipationBadRequest,
    ParticipationForbidden,
    ParticipationNotFound,
    RosterAuthority,
    RosterStateCommand,
    enrollment_fact,
)
from .participation_models import Cohort, CourseOffering, Enrollment
from .result_contracts import TransactionChanged
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
    """Lock current role before authorization; refuse unflushed target changes.

    This deliberately closes the legacy cached-role/query-write race. Pending
    target changes are never refreshed away; the caller must roll them back.
    The caller owns its Session, transaction and final full-session flush.
    """
    boundary = _transaction(db)
    _authority(command.tenant_id, authority)
    locked = db.execute(
        select(
            Enrollment,
            Enrollment.tenant_id,
            Enrollment.cohort_id,
            Enrollment.person_id,
            Enrollment.role_in_cohort,
        )
        .where(
            Enrollment.tenant_id == command.tenant_id,
            Enrollment.cohort_id == command.cohort_id,
            Enrollment.person_id == command.person_id,
        )
        .with_for_update()
    ).first()
    row = locked[0] if locked is not None else None
    if row is not None and (
        row in db.deleted or db.is_modified(row, include_collections=False)
    ):
        raise TransactionChanged(
            "clean target enrollment required; caller rollback required"
        )
    # Fresh projected role is independent of an already cached ORM identity.
    if locked is not None and locked[4] != "student" and not authority.actor_is_admin:
        raise ParticipationForbidden("Forbidden")
    if command.state not in ROSTER_STATES:
        raise ParticipationNotFound(f"invalid roster state: {command.state}")
    if locked is None:
        raise ParticipationNotFound("enrollment not found")
    row = locked[0]
    row.status = command.state
    db.flush()
    fact = replace(
        enrollment_fact(row),
        tenant_id=locked[1],
        cohort_id=locked[2],
        person_id=locked[3],
        role_in_cohort=locked[4],
    )
    _unchanged(db, boundary)
    return fact


def ensure_admission_enrollment(
    db: Session, command: EnsureAdmissionEnrollmentCommand
) -> EnrollmentFact:
    """Apply the source admission enrollment intent in the caller transaction.

    The trusted product caller has already authorized admission, validated its
    applicant/intake/cohort state and resolved a genuine Kernel person. Existing
    enrollment state is preserved: only a changed track is written. Source SQL
    autoflush remains; the unchanged-track path has no explicit flush.
    """
    boundary = _transaction(db)
    row = _enrollment(db, command.tenant_id, command.cohort_id, command.person_id)
    if row is None:
        row = Enrollment(
            tenant_id=command.tenant_id,
            cohort_id=command.cohort_id,
            person_id=command.person_id,
            track_id=command.track_id,
            role_in_cohort="student",
            status="active",
        )
        db.add(row)
        db.flush()
    elif row.track_id != command.track_id:
        row.track_id = command.track_id
        db.flush()
    fact = enrollment_fact(row)
    _unchanged(db, boundary)
    return fact


def ensure_invitation_enrollment(
    db: Session,
    command: EnsureInvitationEnrollmentCommand,
    *,
    authority: RosterAuthority,
) -> InvitationEnrollmentResult:
    """Apply Academy's invitation assignment in the caller's transaction.

    The trusted product already resolved cohort, person, optional track and
    courses. Existing instructors are never demoted; other role changes precede
    the canonical activation gate, so a refusal can leave a pending role change.
    The caller must roll back failures. Source queries retain caller autoflush;
    only the existing track owner explicitly flushes, when a track is supplied.
    """
    boundary = _transaction(db)
    _authority(command.tenant_id, authority)
    member_role = (
        "instructor" if command.global_role in {"instructor", "admin"} else "student"
    )
    row = _enrollment(db, command.tenant_id, command.cohort_id, command.person_id)
    preserved_instructor_role = False
    activation = None
    if row is None:
        row = Enrollment(
            tenant_id=command.tenant_id,
            cohort_id=command.cohort_id,
            person_id=command.person_id,
            role_in_cohort=member_role,
            status="active",
        )
        db.add(row)
    else:
        if row.role_in_cohort == "instructor" and member_role == "student":
            preserved_instructor_role = True
        else:
            row.role_in_cohort = member_role
        activation = _activate_enrollment(row, authority=authority)

    if command.track_id is not None:
        row.track_id = command.track_id
        tracks.ensure_track_offerings(
            db,
            tenant_id=command.tenant_id,
            cohort_id=command.cohort_id,
            track_id=command.track_id,
        )
    for course_id in command.selected_course_ids:
        offering = db.scalars(
            select(CourseOffering).where(
                CourseOffering.tenant_id == command.tenant_id,
                CourseOffering.cohort_id == command.cohort_id,
                CourseOffering.course_id == course_id,
            )
        ).first()
        if offering is None:
            db.add(
                CourseOffering(
                    tenant_id=command.tenant_id,
                    cohort_id=command.cohort_id,
                    course_id=course_id,
                    status="active",
                )
            )
        else:
            offering.status = "active"
    _unchanged(db, boundary)
    return InvitationEnrollmentResult(
        member_role, preserved_instructor_role, activation
    )
