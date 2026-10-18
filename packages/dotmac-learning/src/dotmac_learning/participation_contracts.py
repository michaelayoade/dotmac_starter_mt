"""Participation commands, detached facts and trusted product authorization.

Authority and instructor eligibility are resolved by the product's existing
Kernel authorization owner, never from client request fields. Stable person
UUIDs refer to genuine Kernel Party/PartyPerson fixtures; this does not establish
legacy Academy identity adoption.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from .participation_models import Enrollment


class ParticipationBadRequest(ValueError):
    """Invalid operation, retaining the Academy error detail."""


class ParticipationNotFound(ValueError):
    """Missing scoped reference or invalid roster state."""


class ParticipationForbidden(PermissionError):
    """Product authority does not cover the requested operation."""


@dataclass(frozen=True, slots=True)
class RosterAuthority:
    tenant_id: UUID
    actor_is_admin: bool


@dataclass(frozen=True, slots=True)
class FacilitatorEligibility:
    tenant_id: UUID
    person_id: UUID
    has_instructor_role: bool
    account_is_active: bool


class ParticipationCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tenant_id: UUID


class ActivateEnrollmentCommand(ParticipationCommand):
    enrollment_id: UUID


class BulkEnrollCommand(ParticipationCommand):
    cohort_id: UUID
    emails: tuple[str | None, ...]
    track_id: UUID | None = None


class RosterStateCommand(ParticipationCommand):
    cohort_id: UUID
    person_id: UUID
    state: str


class AssignFacilitatorCommand(ParticipationCommand):
    cohort_id: UUID
    person_id: UUID


class ClassifyRosterCommand(ParticipationCommand):
    roster: dict[str | None, str | None]
    mark_rest_external: bool = False


@dataclass(frozen=True, slots=True)
class EnrollmentFact:
    id: UUID
    tenant_id: UUID
    cohort_id: UUID
    person_id: UUID
    track_id: UUID | None
    role_in_cohort: str
    status: str
    audience: str | None
    employee_ref: str | None
    access_ends_at: datetime | None
    created_at: datetime
    updated_at: datetime


def enrollment_fact(row: Enrollment) -> EnrollmentFact:
    return EnrollmentFact(
        id=row.id,
        tenant_id=row.tenant_id,
        cohort_id=row.cohort_id,
        person_id=row.person_id,
        track_id=row.track_id,
        role_in_cohort=row.role_in_cohort,
        status=row.status,
        audience=row.audience,
        employee_ref=row.employee_ref,
        access_ends_at=row.access_ends_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@dataclass(frozen=True, slots=True)
class ActivationResult:
    outcome: str
    previous_status: str | None


@dataclass(frozen=True, slots=True)
class BulkEnrollResult:
    enrolled: tuple[str, ...]
    reactivated: tuple[str, ...]
    already_active: tuple[str, ...]
    not_found: tuple[str, ...]
    admin_required: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ClassificationCounts:
    staff: int
    external: int
    unclassified: int
    unchanged: int


@dataclass(frozen=True, slots=True)
class AudienceCounts:
    staff: int
    external: int
    unclassified: int


@dataclass(frozen=True, slots=True)
class UnclassifiedPerson:
    email: str | None
    name: str


class EnsureAdmissionEnrollmentCommand(ParticipationCommand):
    """Enrollment intent from an already authorized product admission flow.

    Applicant/onboarding/intake checks and identity provisioning belong to the
    product. These typed fields carry intent; they do not prove authorization.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    cohort_id: UUID
    person_id: UUID
    track_id: UUID | None


class InvitationEnrollmentRolePolicy(Protocol):
    """Installed product policy maps opaque global roles to Learning roles."""

    def member_role(self, global_role: str) -> Literal["student", "instructor"]:
        """Resolve the role using the product's composable policy settings."""
        ...


class EnsureInvitationEnrollmentCommand(ParticipationCommand):
    """Intent from a product-authorized, already resolved invitation assignment.

    Global roles are product-owned strings. Identity, account eligibility,
    credentials, invitations and descriptions remain product responsibilities.
    Course order and duplicates retain the source query/autoflush behavior.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    cohort_id: UUID
    person_id: UUID
    global_role: str
    track_id: UUID | None = None
    selected_course_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class InvitationEnrollmentResult:
    member_role: str
    preserved_instructor_role: bool
    activation: ActivationResult | None
