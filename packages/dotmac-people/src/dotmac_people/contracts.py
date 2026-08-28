"""Pure commands, outcomes, vocabulary, and refusals for dotmac-people."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID


class PeopleError(Exception):
    """Base for employment-directory refusals."""


class NotFound(PeopleError):
    """A referenced row does not exist in the declared tenant scope."""


class Conflict(PeopleError):
    """A tenant identity or assignment interval conflicts."""


class InvalidLifecycle(PeopleError):
    """An employee lifecycle transition or date is inadmissible."""


class InvalidHierarchy(PeopleError):
    """A department or position hierarchy would be cyclic or malformed."""


class EmployeeStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ON_LEAVE = "ON_LEAVE"
    SUSPENDED = "SUSPENDED"
    RESIGNED = "RESIGNED"
    TERMINATED = "TERMINATED"
    RETIRED = "RETIRED"


class AssignmentType(enum.StrEnum):
    PRIMARY = "PRIMARY"
    ACTING = "ACTING"
    INTERIM = "INTERIM"


class VacancyRoutingPolicy(enum.StrEnum):
    SKIP_UP = "SKIP_UP"
    BLOCK = "BLOCK"
    NOTIFY_HR_THEN_SKIP = "NOTIFY_HR_THEN_SKIP"


class ReconcileAction(enum.StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"


@dataclass(frozen=True, slots=True)
class CreateCatalogEntry:
    code: str
    name: str
    description: str | None = None
    parent_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class EmploymentTypeRecord:
    id: UUID
    tenant_id: UUID
    code: str
    name: str
    description: str | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class EmploymentTypeQuery:
    search: str | None = None
    code: str | None = None
    active: bool | None = None
    offset: int = 0
    limit: int = 50


@dataclass(frozen=True, slots=True)
class EmploymentTypePage:
    items: tuple[EmploymentTypeRecord, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class CreateEmploymentType:
    code: str
    name: str
    description: str | None = None


@dataclass(frozen=True, slots=True)
class ReviseEmploymentType:
    employment_type_id: UUID
    code: str
    name: str
    description: str | None


@dataclass(frozen=True, slots=True)
class DeactivateEmploymentType:
    employment_type_id: UUID


@dataclass(frozen=True, slots=True)
class ActivateEmploymentType:
    employment_type_id: UUID


@dataclass(frozen=True, slots=True)
class ReconcileEmploymentType:
    source_id: UUID
    source_fingerprint: str
    source_created_at: datetime
    source_updated_at: datetime | None
    code: str
    name: str
    description: str | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class EmploymentTypeReconcileOutcome:
    action: ReconcileAction
    record: EmploymentTypeRecord
    source_fingerprint: str
    target_fingerprint: str


@dataclass(frozen=True, slots=True)
class CreateEmployee:
    party_id: UUID
    employee_code: str
    date_of_joining: date
    status: EmployeeStatus = EmployeeStatus.DRAFT
    department_id: UUID | None = None
    designation_id: UUID | None = None
    employment_type_id: UUID | None = None
    probation_end_date: date | None = None
    confirmation_date: date | None = None


@dataclass(frozen=True, slots=True)
class RehireEmployee:
    employee_id: UUID
    rehire_date: date


@dataclass(frozen=True, slots=True)
class CreatePosition:
    code: str
    name: str
    department_id: UUID | None = None
    designation_id: UUID | None = None
    parent_id: UUID | None = None
    is_department_head: bool = False
    vacancy_routing_policy: VacancyRoutingPolicy = VacancyRoutingPolicy.SKIP_UP


@dataclass(frozen=True, slots=True)
class PositionAssignmentCommand:
    employee_id: UUID
    position_id: UUID
    assignment_type: AssignmentType
    start_date: date
    end_date: date | None = None


@dataclass(frozen=True, slots=True)
class VacancyRoutingAlert:
    position_id: UUID
    policy: VacancyRoutingPolicy


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    employee_ids: tuple[UUID, ...]
    alerts: tuple[VacancyRoutingAlert, ...] = ()


__all__ = [
    "ActivateEmploymentType",
    "AssignmentType",
    "Conflict",
    "CreateCatalogEntry",
    "CreateEmployee",
    "CreateEmploymentType",
    "CreatePosition",
    "DeactivateEmploymentType",
    "EmployeeStatus",
    "EmploymentTypePage",
    "EmploymentTypeQuery",
    "EmploymentTypeReconcileOutcome",
    "EmploymentTypeRecord",
    "InvalidHierarchy",
    "InvalidLifecycle",
    "NotFound",
    "PeopleError",
    "PositionAssignmentCommand",
    "ReconcileAction",
    "ReconcileEmploymentType",
    "RehireEmployee",
    "ResolutionResult",
    "ReviseEmploymentType",
    "VacancyRoutingAlert",
    "VacancyRoutingPolicy",
]
