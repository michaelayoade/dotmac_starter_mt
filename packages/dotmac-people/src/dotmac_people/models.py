"""The six-table tenant employment-directory persistence contract."""

from __future__ import annotations

from datetime import date
from typing import cast
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, Tenant, TimestampMixin, uuid_pk
from dotmac_kernel.namespaces import module_schema, schema_table_args
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from dotmac_people.contracts import (
    AssignmentType,
    EmployeeStatus,
    VacancyRoutingPolicy,
)

SCHEMA = module_schema("people")


class Department(Base, TimestampMixin):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_departments_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [f"{SCHEMA}.departments.tenant_id", f"{SCHEMA}.departments.id"],
            ondelete="RESTRICT",
            name="fk_departments_tenant_parent",
        ),
        Index("ix_departments_tenant_parent", "tenant_id", "parent_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class Designation(Base, TimestampMixin):
    __tablename__ = "designations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_designations_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_designations_tenant_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class EmploymentType(Base, TimestampMixin):
    __tablename__ = "employment_types"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_employment_types_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_employment_types_tenant_code"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class Employee(Base, TimestampMixin):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_employees_tenant_id_id"),
        UniqueConstraint("tenant_id", "party_id", name="uq_employees_tenant_party"),
        UniqueConstraint("tenant_id", "employee_code", name="uq_employees_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["parties.tenant_id", "parties.id"],
            ondelete="RESTRICT",
            name="fk_employees_tenant_party",
        ),
        ForeignKeyConstraint(
            ["party_id"],
            ["party_persons.party_id"],
            ondelete="RESTRICT",
            name="fk_employees_party_person",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            [f"{SCHEMA}.departments.tenant_id", f"{SCHEMA}.departments.id"],
            ondelete="RESTRICT",
            name="fk_employees_tenant_department",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "designation_id"],
            [f"{SCHEMA}.designations.tenant_id", f"{SCHEMA}.designations.id"],
            ondelete="RESTRICT",
            name="fk_employees_tenant_designation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "employment_type_id"],
            [
                f"{SCHEMA}.employment_types.tenant_id",
                f"{SCHEMA}.employment_types.id",
            ],
            ondelete="RESTRICT",
            name="fk_employees_tenant_employment_type",
        ),
        CheckConstraint(
            "date_of_leaving IS NULL OR date_of_leaving >= date_of_joining",
            name="ck_employees_leaving_after_joining",
        ),
        Index("ix_employees_tenant_status", "tenant_id", "status"),
        Index("ix_employees_tenant_department", "tenant_id", "department_id"),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    party_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    employee_code: Mapped[str] = mapped_column(String(30), nullable=False)
    department_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    designation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    employment_type_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    date_of_joining: Mapped[date] = mapped_column(Date, nullable=False)
    date_of_leaving: Mapped[date | None] = mapped_column(Date, nullable=True)
    probation_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    confirmation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[EmployeeStatus] = mapped_column(
        sa.Enum(
            EmployeeStatus,
            name="people_employee_status",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
        default=EmployeeStatus.DRAFT,
        server_default=EmployeeStatus.DRAFT.value,
    )


class Position(Base, TimestampMixin):
    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_positions_tenant_id_id"),
        UniqueConstraint("tenant_id", "code", name="uq_positions_tenant_code"),
        ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            [f"{SCHEMA}.departments.tenant_id", f"{SCHEMA}.departments.id"],
            ondelete="RESTRICT",
            name="fk_positions_tenant_department",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "designation_id"],
            [f"{SCHEMA}.designations.tenant_id", f"{SCHEMA}.designations.id"],
            ondelete="RESTRICT",
            name="fk_positions_tenant_designation",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            [f"{SCHEMA}.positions.tenant_id", f"{SCHEMA}.positions.id"],
            ondelete="RESTRICT",
            name="fk_positions_tenant_parent",
        ),
        Index("ix_positions_tenant_parent", "tenant_id", "parent_id"),
        Index("ix_positions_tenant_department", "tenant_id", "department_id"),
        Index(
            "uq_positions_tenant_department_head",
            "tenant_id",
            "department_id",
            unique=True,
            postgresql_where=sa.text(
                "is_department_head AND is_active AND department_id IS NOT NULL"
            ),
            sqlite_where=sa.text(
                "is_department_head AND is_active AND department_id IS NOT NULL"
            ),
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    department_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    designation_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    parent_id: Mapped[UUID | None] = mapped_column(Uuid(), nullable=True)
    is_department_head: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sa.false()
    )
    vacancy_routing_policy: Mapped[VacancyRoutingPolicy] = mapped_column(
        sa.Enum(
            VacancyRoutingPolicy,
            name="people_vacancy_routing_policy",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
        default=VacancyRoutingPolicy.SKIP_UP,
        server_default=VacancyRoutingPolicy.SKIP_UP.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sa.true()
    )


class PositionAssignment(Base, TimestampMixin):
    __tablename__ = "position_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id", name="uq_position_assignments_tenant_id_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "employee_id"],
            [f"{SCHEMA}.employees.tenant_id", f"{SCHEMA}.employees.id"],
            ondelete="RESTRICT",
            name="fk_position_assignments_tenant_employee",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "position_id"],
            [f"{SCHEMA}.positions.tenant_id", f"{SCHEMA}.positions.id"],
            ondelete="RESTRICT",
            name="fk_position_assignments_tenant_position",
        ),
        CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_position_assignments_date_order",
        ),
        Index(
            "ix_position_assignments_tenant_employee_dates",
            "tenant_id",
            "employee_id",
            "start_date",
            "end_date",
        ),
        Index(
            "ix_position_assignments_tenant_position_dates",
            "tenant_id",
            "position_id",
            "start_date",
            "end_date",
        ),
        schema_table_args(SCHEMA),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey(Tenant.__table__.c.id, ondelete="CASCADE"), nullable=False
    )
    employee_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    position_id: Mapped[UUID] = mapped_column(Uuid(), nullable=False)
    assignment_type: Mapped[AssignmentType] = mapped_column(
        sa.Enum(
            AssignmentType,
            name="people_assignment_type",
            native_enum=False,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
            create_constraint=True,
        ),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)


TENANT_TABLES: tuple[str, ...] = (
    "employees",
    "departments",
    "designations",
    "employment_types",
    "positions",
    "position_assignments",
)

_TABLE_BY_NAME: dict[str, sa.Table] = {
    model.__tablename__: cast(sa.Table, model.__table__)
    for model in (
        Employee,
        Department,
        Designation,
        EmploymentType,
        Position,
        PositionAssignment,
    )
}


def metadata_table(table_name: str) -> sa.Table:
    """Return one declared table; used by assembly/catalogue gates."""
    return _TABLE_BY_NAME[table_name]


__all__ = [
    "SCHEMA",
    "TENANT_TABLES",
    "Department",
    "Designation",
    "Employee",
    "EmploymentType",
    "Position",
    "PositionAssignment",
    "metadata_table",
]
