"""Pure commands, outcomes, vocabulary, and refusals for dotmac-people."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date
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


@dataclass(frozen=True, slots=True)
class CreateCatalogEntry:
    code: str
    name: str
    description: str | None = None
    parent_id: UUID | None = None


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
    "AssignmentType",
    "Conflict",
    "CreateCatalogEntry",
    "CreateEmployee",
    "CreatePosition",
    "EmployeeStatus",
    "InvalidHierarchy",
    "InvalidLifecycle",
    "NotFound",
    "PeopleError",
    "PositionAssignmentCommand",
    "RehireEmployee",
    "ResolutionResult",
    "VacancyRoutingAlert",
    "VacancyRoutingPolicy",
]
