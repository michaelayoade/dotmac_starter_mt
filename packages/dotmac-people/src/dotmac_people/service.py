"""Tenant-scoped employment-directory decisions extracted from ERP.

Every function operates inside the caller's transaction.  This module mutates
and flushes only; it never commits, rolls back, resolves a tenant, sends a
notification, provisions an account, or writes a consuming domain.
"""

from __future__ import annotations

from datetime import date
from typing import TypeVar, cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Party, PartyPerson, PartyType
from sqlalchemy import Select, and_, case, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_people.contracts import (
    AssignmentType,
    Conflict,
    CreateCatalogEntry,
    CreateEmployee,
    CreatePosition,
    EmployeeStatus,
    InvalidHierarchy,
    InvalidLifecycle,
    NotFound,
    PositionAssignmentCommand,
    RehireEmployee,
    ResolutionResult,
    VacancyRoutingAlert,
    VacancyRoutingPolicy,
)
from dotmac_people.models import (
    Department,
    Designation,
    Employee,
    EmploymentType,
    Position,
    PositionAssignment,
)

_Model = TypeVar("_Model")
_SEPARATED = {
    EmployeeStatus.RESIGNED,
    EmployeeStatus.TERMINATED,
    EmployeeStatus.RETIRED,
}


def _tenant(scope: TenantScope) -> UUID:
    if not isinstance(scope, TenantScope):
        raise TypeError("dotmac-people requires an explicit TenantScope")
    return scope.tenant_id


def _code(value: str, *, field: str) -> str:
    normalized = value.strip().upper()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _name(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty")
    return normalized


def _one(db: Session, statement: Select[tuple[_Model]], *, detail: str) -> _Model:
    result = db.scalar(statement)
    if result is None:
        raise NotFound(detail)
    return result


def _flush_new(db: Session, record: _Model, *, detail: str) -> _Model:
    from dotmac_kernel.db import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(record)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(detail) from exc
    return record


def create_department(
    db: Session, *, scope: TenantScope, command: CreateCatalogEntry
) -> Department:
    tenant_id = _tenant(scope)
    code = _code(command.code, field="department code")
    if db.scalar(
        select(Department.id).where(
            Department.tenant_id == tenant_id, Department.code == code
        )
    ):
        raise Conflict(f"department code {code!r} already exists")
    if command.parent_id is not None:
        _department(db, tenant_id, command.parent_id)
    return _flush_new(
        db,
        Department(
            tenant_id=tenant_id,
            code=code,
            name=_name(command.name, field="department name"),
            description=command.description,
            parent_id=command.parent_id,
        ),
        detail=f"department code {code!r} conflicts",
    )


def create_designation(
    db: Session, *, scope: TenantScope, command: CreateCatalogEntry
) -> Designation:
    tenant_id = _tenant(scope)
    code = _code(command.code, field="designation code")
    if db.scalar(
        select(Designation.id).where(
            Designation.tenant_id == tenant_id, Designation.code == code
        )
    ):
        raise Conflict(f"designation code {code!r} already exists")
    return _flush_new(
        db,
        Designation(
            tenant_id=tenant_id,
            code=code,
            name=_name(command.name, field="designation name"),
            description=command.description,
        ),
        detail=f"designation code {code!r} conflicts",
    )


def create_employment_type(
    db: Session, *, scope: TenantScope, command: CreateCatalogEntry
) -> EmploymentType:
    tenant_id = _tenant(scope)
    code = _code(command.code, field="employment type code")
    if db.scalar(
        select(EmploymentType.id).where(
            EmploymentType.tenant_id == tenant_id, EmploymentType.code == code
        )
    ):
        raise Conflict(f"employment type code {code!r} already exists")
    return _flush_new(
        db,
        EmploymentType(
            tenant_id=tenant_id,
            code=code,
            name=_name(command.name, field="employment type name"),
            description=command.description,
        ),
        detail=f"employment type code {code!r} conflicts",
    )


def _department(db: Session, tenant_id: UUID, row_id: UUID) -> Department:
    return _one(
        db,
        select(Department).where(
            Department.tenant_id == tenant_id, Department.id == row_id
        ),
        detail=f"department {row_id} was not found",
    )


def _designation(db: Session, tenant_id: UUID, row_id: UUID) -> Designation:
    return _one(
        db,
        select(Designation).where(
            Designation.tenant_id == tenant_id, Designation.id == row_id
        ),
        detail=f"designation {row_id} was not found",
    )


def _employment_type(db: Session, tenant_id: UUID, row_id: UUID) -> EmploymentType:
    return _one(
        db,
        select(EmploymentType).where(
            EmploymentType.tenant_id == tenant_id, EmploymentType.id == row_id
        ),
        detail=f"employment type {row_id} was not found",
    )


def _employee(db: Session, tenant_id: UUID, row_id: UUID) -> Employee:
    return _one(
        db,
        select(Employee).where(Employee.tenant_id == tenant_id, Employee.id == row_id),
        detail=f"employee {row_id} was not found",
    )


def _position(db: Session, tenant_id: UUID, row_id: UUID) -> Position:
    return _one(
        db,
        select(Position).where(Position.tenant_id == tenant_id, Position.id == row_id),
        detail=f"position {row_id} was not found",
    )


def create_employee(
    db: Session, *, scope: TenantScope, command: CreateEmployee
) -> Employee:
    tenant_id = _tenant(scope)
    party_exists = db.scalar(
        select(Party.id)
        .join(PartyPerson, PartyPerson.party_id == Party.id)
        .where(
            Party.tenant_id == tenant_id,
            Party.id == command.party_id,
            Party.party_type == PartyType.person,
        )
    )
    if party_exists is None:
        raise NotFound(
            f"person Party {command.party_id} was not found in tenant {tenant_id}"
        )
    if db.scalar(
        select(Employee.id).where(
            Employee.tenant_id == tenant_id,
            Employee.party_id == command.party_id,
        )
    ):
        raise Conflict(f"Party {command.party_id} already has an employee record")
    code = _code(command.employee_code, field="employee code")
    if db.scalar(
        select(Employee.id).where(
            Employee.tenant_id == tenant_id, Employee.employee_code == code
        )
    ):
        raise Conflict(f"employee code {code!r} already exists")
    if command.department_id is not None:
        _department(db, tenant_id, command.department_id)
    if command.designation_id is not None:
        _designation(db, tenant_id, command.designation_id)
    if command.employment_type_id is not None:
        _employment_type(db, tenant_id, command.employment_type_id)
    if (
        command.probation_end_date is not None
        and command.probation_end_date < command.date_of_joining
    ):
        raise InvalidLifecycle("probation end date cannot precede joining")
    if (
        command.confirmation_date is not None
        and command.confirmation_date < command.date_of_joining
    ):
        raise InvalidLifecycle("confirmation date cannot precede joining")
    return _flush_new(
        db,
        Employee(
            tenant_id=tenant_id,
            party_id=command.party_id,
            employee_code=code,
            department_id=command.department_id,
            designation_id=command.designation_id,
            employment_type_id=command.employment_type_id,
            date_of_joining=command.date_of_joining,
            probation_end_date=command.probation_end_date,
            confirmation_date=command.confirmation_date,
            status=command.status,
        ),
        detail=f"employee {code!r} conflicts",
    )


def rehire_employee(
    db: Session, *, scope: TenantScope, command: RehireEmployee
) -> Employee:
    employee = _employee(db, _tenant(scope), command.employee_id)
    if employee.status not in _SEPARATED:
        raise InvalidLifecycle(
            f"employee {employee.id} in state {employee.status} cannot be rehired"
        )
    if (
        employee.date_of_leaving is not None
        and command.rehire_date < employee.date_of_leaving
    ):
        raise InvalidLifecycle("rehire date cannot be before date of leaving")
    employee.status = EmployeeStatus.ACTIVE
    employee.date_of_joining = command.rehire_date
    employee.date_of_leaving = None
    db.flush()
    return employee


def create_position(
    db: Session, *, scope: TenantScope, command: CreatePosition
) -> Position:
    tenant_id = _tenant(scope)
    code = _code(command.code, field="position code")
    if db.scalar(
        select(Position.id).where(
            Position.tenant_id == tenant_id, Position.code == code
        )
    ):
        raise Conflict(f"position code {code!r} already exists")
    if command.department_id is not None:
        _department(db, tenant_id, command.department_id)
    if command.designation_id is not None:
        _designation(db, tenant_id, command.designation_id)
    if command.parent_id is not None:
        _position(db, tenant_id, command.parent_id)
    if command.is_department_head and command.department_id is None:
        raise InvalidHierarchy("a department-head position needs a department")
    if command.is_department_head and db.scalar(
        select(Position.id).where(
            Position.tenant_id == tenant_id,
            Position.department_id == command.department_id,
            Position.is_department_head.is_(True),
            Position.is_active.is_(True),
        )
    ):
        raise Conflict("department already has an active head position")
    return _flush_new(
        db,
        Position(
            tenant_id=tenant_id,
            code=code,
            name=_name(command.name, field="position name"),
            department_id=command.department_id,
            designation_id=command.designation_id,
            parent_id=command.parent_id,
            is_department_head=command.is_department_head,
            vacancy_routing_policy=command.vacancy_routing_policy,
        ),
        detail=f"position {code!r} conflicts",
    )


def _overlaps(
    *,
    existing_start: sa.ColumnElement[date],
    existing_end: sa.ColumnElement[date],
    new_start: date,
    new_end: date | None,
) -> sa.ColumnElement[bool]:
    return and_(
        or_(existing_end.is_(None), existing_end >= new_start),
        sa.true() if new_end is None else existing_start <= new_end,
    )


def assign_position(
    db: Session, *, scope: TenantScope, command: PositionAssignmentCommand
) -> PositionAssignment:
    tenant_id = _tenant(scope)
    _employee(db, tenant_id, command.employee_id)
    _position(db, tenant_id, command.position_id)
    if command.end_date is not None and command.end_date < command.start_date:
        raise InvalidLifecycle("assignment end date cannot precede its start date")
    if command.assignment_type == AssignmentType.PRIMARY:
        overlap = _overlaps(
            existing_start=cast(sa.ColumnElement[date], PositionAssignment.start_date),
            existing_end=cast(sa.ColumnElement[date], PositionAssignment.end_date),
            new_start=command.start_date,
            new_end=command.end_date,
        )
        existing = db.scalar(
            select(PositionAssignment.id).where(
                PositionAssignment.tenant_id == tenant_id,
                PositionAssignment.assignment_type == AssignmentType.PRIMARY,
                or_(
                    PositionAssignment.employee_id == command.employee_id,
                    PositionAssignment.position_id == command.position_id,
                ),
                overlap,
            )
        )
        if existing is not None:
            raise Conflict("primary assignment overlaps for employee or position")
    return _flush_new(
        db,
        PositionAssignment(
            tenant_id=tenant_id,
            employee_id=command.employee_id,
            position_id=command.position_id,
            assignment_type=command.assignment_type,
            start_date=command.start_date,
            end_date=command.end_date,
        ),
        detail="position assignment conflicts",
    )


def end_assignment(
    db: Session,
    *,
    scope: TenantScope,
    assignment_id: UUID,
    end_date: date,
) -> PositionAssignment:
    tenant_id = _tenant(scope)
    assignment = _one(
        db,
        select(PositionAssignment).where(
            PositionAssignment.tenant_id == tenant_id,
            PositionAssignment.id == assignment_id,
        ),
        detail=f"position assignment {assignment_id} was not found",
    )
    if end_date < assignment.start_date:
        raise InvalidLifecycle("assignment end date cannot precede its start date")
    assignment.end_date = end_date
    db.flush()
    return assignment


def _active_range(as_of: date) -> sa.ColumnElement[bool]:
    return and_(
        PositionAssignment.start_date <= as_of,
        or_(
            PositionAssignment.end_date.is_(None),
            PositionAssignment.end_date >= as_of,
        ),
    )


def _assignment_priority() -> sa.ColumnElement[int]:
    return case(
        (PositionAssignment.assignment_type == AssignmentType.PRIMARY, 1),
        (PositionAssignment.assignment_type == AssignmentType.ACTING, 2),
        else_=3,
    )


def _active_assignment(
    db: Session, tenant_id: UUID, employee_id: UUID, *, as_of: date
) -> PositionAssignment | None:
    return db.scalar(
        select(PositionAssignment)
        .where(
            PositionAssignment.tenant_id == tenant_id,
            PositionAssignment.employee_id == employee_id,
            _active_range(as_of),
        )
        .order_by(_assignment_priority(), PositionAssignment.start_date.desc())
        .limit(1)
    )


def _incumbent(
    db: Session, tenant_id: UUID, position_id: UUID, *, as_of: date
) -> Employee | None:
    return db.scalar(
        select(Employee)
        .join(
            PositionAssignment,
            and_(
                PositionAssignment.tenant_id == Employee.tenant_id,
                PositionAssignment.employee_id == Employee.id,
            ),
        )
        .where(
            Employee.tenant_id == tenant_id,
            Employee.status == EmployeeStatus.ACTIVE,
            PositionAssignment.position_id == position_id,
            _active_range(as_of),
        )
        .order_by(_assignment_priority(), PositionAssignment.start_date.desc())
        .limit(1)
    )


def position_is_vacant(
    db: Session, *, scope: TenantScope, position_id: UUID, as_of: date
) -> bool:
    tenant_id = _tenant(scope)
    _position(db, tenant_id, position_id)
    return _incumbent(db, tenant_id, position_id, as_of=as_of) is None


def _vacant_action(position: Position, alerts: list[VacancyRoutingAlert]) -> bool:
    """Return whether traversal may continue past this vacant position."""
    if position.vacancy_routing_policy == VacancyRoutingPolicy.BLOCK:
        return False
    if position.vacancy_routing_policy == VacancyRoutingPolicy.NOTIFY_HR_THEN_SKIP:
        alerts.append(
            VacancyRoutingAlert(
                position_id=position.id,
                policy=VacancyRoutingPolicy.NOTIFY_HR_THEN_SKIP,
            )
        )
    return True


def resolve_manager(
    db: Session,
    *,
    scope: TenantScope,
    employee_id: UUID,
    as_of: date,
) -> ResolutionResult:
    tenant_id = _tenant(scope)
    assignment = _active_assignment(db, tenant_id, employee_id, as_of=as_of)
    if assignment is None:
        return ResolutionResult(())
    current = _position(db, tenant_id, assignment.position_id)
    next_id = current.parent_id
    visited = {current.id}
    alerts: list[VacancyRoutingAlert] = []
    while next_id is not None and next_id not in visited:
        visited.add(next_id)
        position = _position(db, tenant_id, next_id)
        incumbent = _incumbent(db, tenant_id, position.id, as_of=as_of)
        if incumbent is not None:
            return ResolutionResult((incumbent.id,), tuple(alerts))
        if not _vacant_action(position, alerts):
            return ResolutionResult((), tuple(alerts))
        next_id = position.parent_id
    return ResolutionResult((), tuple(alerts))


def resolve_direct_reports(
    db: Session,
    *,
    scope: TenantScope,
    manager_employee_id: UUID,
    as_of: date,
) -> ResolutionResult:
    tenant_id = _tenant(scope)
    assignment = _active_assignment(db, tenant_id, manager_employee_id, as_of=as_of)
    if assignment is None:
        return ResolutionResult(())
    root = _position(db, tenant_id, assignment.position_id)
    queue = list(
        db.scalars(
            select(Position).where(
                Position.tenant_id == tenant_id,
                Position.parent_id == root.id,
                Position.is_active.is_(True),
            )
        )
    )
    visited = {root.id}
    reports: list[UUID] = []
    alerts: list[VacancyRoutingAlert] = []
    while queue:
        position = queue.pop(0)
        if position.id in visited:
            continue
        visited.add(position.id)
        incumbent = _incumbent(db, tenant_id, position.id, as_of=as_of)
        if incumbent is not None:
            reports.append(incumbent.id)
            continue
        if not _vacant_action(position, alerts):
            continue
        queue.extend(
            db.scalars(
                select(Position).where(
                    Position.tenant_id == tenant_id,
                    Position.parent_id == position.id,
                    Position.is_active.is_(True),
                )
            )
        )
    return ResolutionResult(tuple(reports), tuple(alerts))


def resolve_approval_chain(
    db: Session,
    *,
    scope: TenantScope,
    employee_id: UUID,
    as_of: date,
) -> ResolutionResult:
    """Resolve every occupied ancestor, preserving vacancy decisions as facts."""
    tenant_id = _tenant(scope)
    assignment = _active_assignment(db, tenant_id, employee_id, as_of=as_of)
    if assignment is None:
        return ResolutionResult(())
    current = _position(db, tenant_id, assignment.position_id)
    next_id = current.parent_id
    visited = {current.id}
    approvers: list[UUID] = []
    alerts: list[VacancyRoutingAlert] = []
    while next_id is not None and next_id not in visited:
        visited.add(next_id)
        position = _position(db, tenant_id, next_id)
        incumbent = _incumbent(db, tenant_id, position.id, as_of=as_of)
        if incumbent is not None:
            approvers.append(incumbent.id)
        elif not _vacant_action(position, alerts):
            break
        next_id = position.parent_id
    return ResolutionResult(tuple(approvers), tuple(alerts))


def resolve_department_head(
    db: Session, *, scope: TenantScope, department_id: UUID, as_of: date
) -> ResolutionResult:
    tenant_id = _tenant(scope)
    _department(db, tenant_id, department_id)
    head = db.scalar(
        select(Position).where(
            Position.tenant_id == tenant_id,
            Position.department_id == department_id,
            Position.is_department_head.is_(True),
            Position.is_active.is_(True),
        )
    )
    if head is None:
        return ResolutionResult(())
    incumbent = _incumbent(db, tenant_id, head.id, as_of=as_of)
    if incumbent is not None:
        return ResolutionResult((incumbent.id,))
    alerts: list[VacancyRoutingAlert] = []
    _vacant_action(head, alerts)
    return ResolutionResult((), tuple(alerts))


def search_employees(
    db: Session, *, scope: TenantScope, query: str, limit: int = 50
) -> tuple[Employee, ...]:
    """Search kernel-owned identity text without copying it onto employees."""
    tenant_id = _tenant(scope)
    term = f"%{query.strip()}%"
    if not query.strip() or limit < 1:
        return ()
    return tuple(
        db.scalars(
            select(Employee)
            .join(
                Party,
                and_(
                    Party.tenant_id == Employee.tenant_id,
                    Party.id == Employee.party_id,
                ),
            )
            .where(
                Employee.tenant_id == tenant_id,
                or_(
                    Employee.employee_code.ilike(term),
                    Party.display_name.ilike(term),
                    Party.email.ilike(term),
                ),
            )
            .order_by(Employee.employee_code)
            .limit(min(limit, 200))
        )
    )


__all__ = [
    "assign_position",
    "create_department",
    "create_designation",
    "create_employee",
    "create_employment_type",
    "create_position",
    "end_assignment",
    "position_is_vacant",
    "rehire_employee",
    "resolve_approval_chain",
    "resolve_department_head",
    "resolve_direct_reports",
    "resolve_manager",
    "search_employees",
]
