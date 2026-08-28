"""Tenant-scoped employment-directory decisions extracted from ERP.

Every function operates inside the caller's transaction.  This module mutates
and flushes only; it never commits, rolls back, resolves a tenant, sends a
notification, provisions an account, or writes a consuming domain.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import TypeVar, cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Party, PartyPerson, PartyType
from sqlalchemy import Select, and_, case, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from dotmac_people.contracts import (
    ActivateEmploymentType,
    AssignmentType,
    Conflict,
    CreateCatalogEntry,
    CreateEmployee,
    CreateEmploymentType,
    CreatePosition,
    DeactivateEmploymentType,
    EmployeeStatus,
    EmploymentTypePage,
    EmploymentTypeQuery,
    EmploymentTypeReconcileOutcome,
    EmploymentTypeRecord,
    InvalidHierarchy,
    InvalidLifecycle,
    NotFound,
    PositionAssignmentCommand,
    ReconcileAction,
    ReconcileEmploymentType,
    RehireEmployee,
    ResolutionResult,
    ReviseEmploymentType,
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


def _employment_type_code(value: str) -> str:
    normalized = _code(value, field="employment type code")
    if len(normalized) > 20:
        raise ValueError("employment type code must be at most 20 characters")
    return normalized


def _employment_type_name(value: str) -> str:
    normalized = _name(value, field="employment type name")
    if len(normalized) > 100:
        raise ValueError("employment type name must be at most 100 characters")
    return normalized


def _source_timestamp(value: datetime, *, field: str) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _one(db: Session, statement: Select[tuple[_Model]], *, detail: str) -> _Model:
    result = db.scalar(statement)
    if result is None:
        raise NotFound(detail)
    return result


def _flush_new(db: Session, record: _Model, *, detail: str) -> _Model:
    from dotmac_kernel.transactions import conflict_savepoint

    try:
        with conflict_savepoint(db):
            db.add(record)
            db.flush()
    except IntegrityError as exc:
        raise Conflict(detail) from exc
    return record


def _write_employment_type(
    db: Session,
    *,
    row: EmploymentType,
    code: str,
    name: str,
    description: str | None,
    is_active: bool,
    detail: str,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> None:
    from dotmac_kernel.transactions import conflict_savepoint

    try:
        with conflict_savepoint(db):
            row.code = code
            row.name = name
            row.description = description
            row.is_active = is_active
            if created_at is not None:
                row.created_at = created_at
            if updated_at is not None:
                row.updated_at = updated_at
            db.flush()
    except IntegrityError as exc:
        raise Conflict(detail) from exc


def _employment_type_record(row: EmploymentType) -> EmploymentTypeRecord:
    def utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    return EmploymentTypeRecord(
        id=row.id,
        tenant_id=row.tenant_id,
        code=row.code,
        name=row.name,
        description=row.description,
        is_active=row.is_active,
        created_at=utc(row.created_at),
        updated_at=utc(row.updated_at),
    )


def _fingerprint_part(name: str, kind: str, value: bytes) -> bytes:
    """Length-prefix a named, typed field for the ``et1`` semantic digest."""
    name_bytes = name.encode("utf-8")
    kind_bytes = kind.encode("ascii")
    return b"".join(
        (
            len(name_bytes).to_bytes(2, "big"),
            name_bytes,
            len(kind_bytes).to_bytes(1, "big"),
            kind_bytes,
            len(value).to_bytes(8, "big"),
            value,
        )
    )


def employment_type_fingerprint(
    *,
    scope: TenantScope,
    employment_type_id: UUID,
    code: str,
    name: str,
    description: str | None,
    is_active: bool,
) -> str:
    """Bind all normalized Employment Type decision evidence under ``et1``."""
    tenant_id = _tenant(scope)
    if not isinstance(employment_type_id, UUID):
        raise TypeError("employment_type_id must be a UUID")
    if not isinstance(is_active, bool):
        raise TypeError("is_active must be a bool")
    normalized_code = _employment_type_code(code)
    normalized_name = _employment_type_name(name)
    encoded = b"".join(
        (
            _fingerprint_part("owner", "str", b"dotmac-people"),
            _fingerprint_part("entity", "str", b"employment-type"),
            _fingerprint_part("tenant_id", "uuid", tenant_id.bytes),
            _fingerprint_part("id", "uuid", employment_type_id.bytes),
            _fingerprint_part("code", "str", normalized_code.encode("utf-8")),
            _fingerprint_part("name", "str", normalized_name.encode("utf-8")),
            _fingerprint_part(
                "description",
                "none" if description is None else "str",
                b"" if description is None else description.encode("utf-8"),
            ),
            _fingerprint_part("is_active", "bool", b"1" if is_active else b"0"),
        )
    )
    return f"et1:{hashlib.sha256(encoded).hexdigest()}"


def _validate_source_fingerprint(value: str) -> str:
    if len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("source fingerprint must be a lowercase SHA-256 digest")
    return value


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
    """Deprecated a1 ORM adapter; new callers use ``register_employment_type``."""
    tenant_id = _tenant(scope)
    code = _employment_type_code(command.code)
    name = _employment_type_name(command.name)
    return _create_employment_type(
        db,
        tenant_id=tenant_id,
        code=code,
        name=name,
        description=command.description,
    )


def _create_employment_type(
    db: Session,
    *,
    tenant_id: UUID,
    code: str,
    name: str,
    description: str | None,
    row_id: UUID | None = None,
    is_active: bool = True,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> EmploymentType:
    if db.scalar(
        select(EmploymentType.id).where(
            EmploymentType.tenant_id == tenant_id, EmploymentType.code == code
        )
    ):
        raise Conflict(f"employment type code {code!r} already exists")
    row = EmploymentType(
        **({"id": row_id} if row_id is not None else {}),
        tenant_id=tenant_id,
        code=code,
        name=name,
        description=description,
        is_active=is_active,
    )
    if created_at is not None:
        row.created_at = created_at
    if updated_at is not None:
        row.updated_at = updated_at
    return _flush_new(
        db,
        row,
        detail=f"employment type code {code!r} conflicts",
    )


def register_employment_type(
    db: Session, *, scope: TenantScope, command: CreateEmploymentType
) -> EmploymentTypeRecord:
    tenant_id = _tenant(scope)
    row = _create_employment_type(
        db,
        tenant_id=tenant_id,
        code=_employment_type_code(command.code),
        name=_employment_type_name(command.name),
        description=command.description,
    )
    return _employment_type_record(row)


def read_employment_type(
    db: Session, *, scope: TenantScope, employment_type_id: UUID
) -> EmploymentTypeRecord:
    return _employment_type_record(
        _employment_type(db, _tenant(scope), employment_type_id)
    )


def list_employment_types(
    db: Session, *, scope: TenantScope, query: EmploymentTypeQuery
) -> EmploymentTypePage:
    tenant_id = _tenant(scope)
    if query.offset < 0:
        raise ValueError("employment type offset must be non-negative")
    if not 1 <= query.limit <= 200:
        raise ValueError("employment type limit must be between 1 and 200")
    predicates: list[sa.ColumnElement[bool]] = [EmploymentType.tenant_id == tenant_id]
    if query.code is not None:
        predicates.append(EmploymentType.code == _employment_type_code(query.code))
    if query.active is not None:
        predicates.append(EmploymentType.is_active.is_(query.active))
    if query.search is not None and query.search.strip():
        term = f"%{query.search.strip()}%"
        predicates.append(
            or_(EmploymentType.code.ilike(term), EmploymentType.name.ilike(term))
        )
    total = int(
        db.scalar(
            select(sa.func.count()).select_from(EmploymentType).where(*predicates)
        )
        or 0
    )
    rows = db.scalars(
        select(EmploymentType)
        .where(*predicates)
        .order_by(EmploymentType.code, EmploymentType.id)
        .offset(query.offset)
        .limit(query.limit)
    )
    return EmploymentTypePage(
        items=tuple(_employment_type_record(row) for row in rows),
        total=total,
        offset=query.offset,
        limit=query.limit,
    )


def revise_employment_type(
    db: Session, *, scope: TenantScope, command: ReviseEmploymentType
) -> EmploymentTypeRecord:
    tenant_id = _tenant(scope)
    row = _employment_type(db, tenant_id, command.employment_type_id)
    code = _employment_type_code(command.code)
    name = _employment_type_name(command.name)
    occupied = db.scalar(
        select(EmploymentType.id).where(
            EmploymentType.tenant_id == tenant_id,
            EmploymentType.code == code,
            EmploymentType.id != row.id,
        )
    )
    if occupied is not None:
        raise Conflict(f"employment type code {code!r} already exists")
    _write_employment_type(
        db,
        row=row,
        code=code,
        name=name,
        description=command.description,
        is_active=row.is_active,
        detail=f"employment type code {code!r} conflicts",
    )
    return _employment_type_record(row)


def _set_employment_type_active(
    db: Session,
    *,
    scope: TenantScope,
    employment_type_id: UUID,
    active: bool,
) -> EmploymentTypeRecord:
    row = _employment_type(db, _tenant(scope), employment_type_id)
    if row.is_active != active:
        row.is_active = active
        db.flush()
    return _employment_type_record(row)


def deactivate_employment_type(
    db: Session, *, scope: TenantScope, command: DeactivateEmploymentType
) -> EmploymentTypeRecord:
    return _set_employment_type_active(
        db, scope=scope, employment_type_id=command.employment_type_id, active=False
    )


def activate_employment_type(
    db: Session, *, scope: TenantScope, command: ActivateEmploymentType
) -> EmploymentTypeRecord:
    return _set_employment_type_active(
        db, scope=scope, employment_type_id=command.employment_type_id, active=True
    )


def require_active_employment_type(
    db: Session, *, scope: TenantScope, employment_type_id: UUID
) -> EmploymentTypeRecord:
    row = _employment_type(db, _tenant(scope), employment_type_id)
    if not row.is_active:
        raise InvalidLifecycle(f"employment type {employment_type_id} is inactive")
    return _employment_type_record(row)


def reconcile_employment_type(
    db: Session, *, scope: TenantScope, command: ReconcileEmploymentType
) -> EmploymentTypeReconcileOutcome:
    tenant_id = _tenant(scope)
    source_fingerprint = _validate_source_fingerprint(command.source_fingerprint)
    source_created_at = _source_timestamp(
        command.source_created_at, field="source_created_at"
    )
    source_updated_at = (
        source_created_at
        if command.source_updated_at is None
        else _source_timestamp(command.source_updated_at, field="source_updated_at")
    )
    code = _employment_type_code(command.code)
    name = _employment_type_name(command.name)
    if not isinstance(command.is_active, bool):
        raise TypeError("is_active must be a bool")

    row = db.scalar(
        select(EmploymentType).where(
            EmploymentType.tenant_id == tenant_id,
            EmploymentType.id == command.source_id,
        )
    )
    occupied = db.scalar(
        select(EmploymentType.id).where(
            EmploymentType.tenant_id == tenant_id,
            EmploymentType.code == code,
            EmploymentType.id != command.source_id,
        )
    )
    if occupied is not None:
        raise Conflict(f"employment type code {code!r} already exists")

    if row is None:
        row = _create_employment_type(
            db,
            tenant_id=tenant_id,
            row_id=command.source_id,
            code=code,
            name=name,
            description=command.description,
            is_active=command.is_active,
            created_at=source_created_at,
            updated_at=source_updated_at,
        )
        action = ReconcileAction.CREATED
    else:
        current_record = _employment_type_record(row)
        if source_updated_at < current_record.updated_at:
            raise Conflict(
                f"employment type {command.source_id} source update is stale"
            )
        changed = (
            row.code != code
            or row.name != name
            or row.description != command.description
            or row.is_active != command.is_active
            or current_record.created_at != source_created_at
            or current_record.updated_at != source_updated_at
        )
        if changed:
            _write_employment_type(
                db,
                row=row,
                code=code,
                name=name,
                description=command.description,
                is_active=command.is_active,
                detail=f"employment type code {code!r} conflicts",
                created_at=source_created_at,
                updated_at=source_updated_at,
            )
            action = ReconcileAction.UPDATED
        else:
            action = ReconcileAction.UNCHANGED

    record = _employment_type_record(row)
    return EmploymentTypeReconcileOutcome(
        action=action,
        record=record,
        source_fingerprint=source_fingerprint,
        target_fingerprint=employment_type_fingerprint(
            scope=scope,
            employment_type_id=record.id,
            code=record.code,
            name=record.name,
            description=record.description,
            is_active=record.is_active,
        ),
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
        employment_type = _employment_type(db, tenant_id, command.employment_type_id)
        if not employment_type.is_active:
            raise InvalidLifecycle(
                f"employment type {command.employment_type_id} is inactive"
            )
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
    "activate_employment_type",
    "assign_position",
    "create_department",
    "create_designation",
    "create_employee",
    "create_employment_type",
    "create_position",
    "deactivate_employment_type",
    "employment_type_fingerprint",
    "end_assignment",
    "list_employment_types",
    "position_is_vacant",
    "read_employment_type",
    "reconcile_employment_type",
    "register_employment_type",
    "rehire_employee",
    "require_active_employment_type",
    "resolve_approval_chain",
    "resolve_department_head",
    "resolve_direct_reports",
    "resolve_manager",
    "revise_employment_type",
    "search_employees",
]
