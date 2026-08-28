"""ERP parity behaviours retained by the narrow employment-directory owner."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_kernel.models import Party, PartyPerson, PartyType, Tenant
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
    EmploymentTypeQuery,
    InvalidLifecycle,
    NotFound,
    PositionAssignmentCommand,
    ReconcileAction,
    ReconcileEmploymentType,
    RehireEmployee,
    ReviseEmploymentType,
    VacancyRoutingPolicy,
)
from dotmac_people.models import TENANT_TABLES, Employee, EmploymentType, Position
from dotmac_people.service import (
    activate_employment_type,
    assign_position,
    create_department,
    create_designation,
    create_employee,
    create_employment_type,
    create_position,
    deactivate_employment_type,
    employment_type_fingerprint,
    end_assignment,
    list_employment_types,
    position_is_vacant,
    read_employment_type,
    reconcile_employment_type,
    register_employment_type,
    rehire_employee,
    require_active_employment_type,
    resolve_approval_chain,
    resolve_department_head,
    resolve_direct_reports,
    resolve_manager,
    revise_employment_type,
    search_employees,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TENANT_A = uuid.uuid4()
TENANT_B = uuid.uuid4()
PERSON_A = uuid.uuid4()


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite:///:memory:",
        execution_options={"schema_translate_map": {"mod_people": None}},
    )
    Tenant.__table__.create(engine)
    Party.__table__.create(engine)
    PartyPerson.__table__.create(engine)
    from dotmac_people import models

    for table_name in TENANT_TABLES:
        models.metadata_table(table_name).create(engine)
    with Session(engine) as session:
        session.add_all(
            [
                Tenant(id=TENANT_A, slug="alpha", name="Alpha"),
                Tenant(id=TENANT_B, slug="bravo", name="Bravo"),
                Party(
                    id=PERSON_A,
                    tenant_id=TENANT_A,
                    party_type=PartyType.person,
                    display_name="Ada Lovelace",
                ),
            ]
        )
        session.flush()
        session.add(
            PartyPerson(party_id=PERSON_A, first_name="Ada", last_name="Lovelace")
        )
        session.flush()
        yield session
    engine.dispose()


def _employee(db: Session, *, code: str = "emp-001") -> Employee:
    return create_employee(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateEmployee(
            party_id=PERSON_A,
            employee_code=code,
            date_of_joining=date(2026, 1, 1),
            status=EmployeeStatus.ACTIVE,
        ),
    )


def test_employee_reuses_party_and_normalizes_the_stable_code(db: Session) -> None:
    employee = _employee(db)
    assert employee.party_id == PERSON_A
    assert employee.employee_code == "EMP-001"
    assert employee.status == EmployeeStatus.ACTIVE


def test_employee_code_and_party_are_unique_inside_the_tenant(db: Session) -> None:
    _employee(db)
    with pytest.raises(Conflict):
        _employee(db, code="EMP-002")


def test_an_employee_cannot_link_another_tenants_party(db: Session) -> None:
    other_party = uuid.uuid4()
    db.add_all(
        [
            Party(
                id=other_party,
                tenant_id=TENANT_B,
                party_type=PartyType.person,
                display_name="Other Tenant",
            ),
            PartyPerson(
                party_id=other_party,
                first_name="Other",
                last_name="Tenant",
            ),
        ]
    )
    db.flush()
    with pytest.raises(NotFound, match="was not found"):
        create_employee(
            db,
            scope=TenantScope(TENANT_A),
            command=CreateEmployee(
                party_id=other_party,
                employee_code="foreign",
                date_of_joining=date(2026, 1, 1),
            ),
        )


def test_rehire_preserves_the_erp_state_and_date_rules(db: Session) -> None:
    employee = _employee(db)
    employee.status = EmployeeStatus.RESIGNED
    employee.date_of_leaving = date(2026, 1, 31)
    db.flush()

    rehire_employee(
        db,
        scope=TenantScope(TENANT_A),
        command=RehireEmployee(employee.id, date(2026, 2, 1)),
    )
    assert employee.status == EmployeeStatus.ACTIVE
    assert employee.date_of_joining == date(2026, 2, 1)
    assert employee.date_of_leaving is None

    employee.status = EmployeeStatus.TERMINATED
    employee.date_of_leaving = date(2026, 3, 1)
    with pytest.raises(InvalidLifecycle, match="before"):
        rehire_employee(
            db,
            scope=TenantScope(TENANT_A),
            command=RehireEmployee(employee.id, date(2026, 2, 28)),
        )


def test_catalogues_and_positions_normalize_codes(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    department = create_department(
        db, scope=scope, command=CreateCatalogEntry("ops", "Operations")
    )
    designation = create_designation(
        db, scope=scope, command=CreateCatalogEntry("mgr", "Manager")
    )
    employment_type = create_employment_type(
        db, scope=scope, command=CreateCatalogEntry("full", "Full time")
    )
    position = create_position(
        db,
        scope=scope,
        command=CreatePosition(
            code="ops-mgr",
            name="Operations manager",
            department_id=department.id,
            designation_id=designation.id,
            is_department_head=True,
        ),
    )
    assert {department.code, designation.code, employment_type.code, position.code} == {
        "OPS",
        "MGR",
        "FULL",
        "OPS-MGR",
    }
    with pytest.raises(Conflict, match="department code"):
        create_department(
            db, scope=scope, command=CreateCatalogEntry("OPS", "Duplicate")
        )


def test_future_assignment_does_not_make_a_position_currently_occupied(
    db: Session,
) -> None:
    employee = _employee(db)
    scope = TenantScope(TENANT_A)
    position = create_position(
        db, scope=scope, command=CreatePosition(code="future", name="Future")
    )
    assign_position(
        db,
        scope=scope,
        command=PositionAssignmentCommand(
            employee_id=employee.id,
            position_id=position.id,
            assignment_type=AssignmentType.PRIMARY,
            start_date=date(2099, 1, 1),
        ),
    )
    assert position_is_vacant(
        db, scope=scope, position_id=position.id, as_of=date(2026, 1, 1)
    )


def test_primary_overlap_is_refused_but_acting_alongside_primary_is_allowed(
    db: Session,
) -> None:
    employee = _employee(db)
    scope = TenantScope(TENANT_A)
    first = create_position(
        db, scope=scope, command=CreatePosition(code="one", name="One")
    )
    second = create_position(
        db, scope=scope, command=CreatePosition(code="two", name="Two")
    )
    command = PositionAssignmentCommand(
        employee_id=employee.id,
        position_id=first.id,
        assignment_type=AssignmentType.PRIMARY,
        start_date=date(2026, 1, 1),
    )
    assign_position(db, scope=scope, command=command)
    with pytest.raises(Conflict, match="primary"):
        assign_position(
            db,
            scope=scope,
            command=PositionAssignmentCommand(
                employee_id=employee.id,
                position_id=second.id,
                assignment_type=AssignmentType.PRIMARY,
                start_date=date(2026, 2, 1),
            ),
        )
    acting = assign_position(
        db,
        scope=scope,
        command=PositionAssignmentCommand(
            employee_id=employee.id,
            position_id=second.id,
            assignment_type=AssignmentType.ACTING,
            start_date=date(2026, 2, 1),
        ),
    )
    assert acting.assignment_type == AssignmentType.ACTING


def test_vacant_position_rolls_manager_resolution_up_and_reports_down(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    manager = _employee(db, code="mgr")
    # A second person identity is still kernel-owned; the module only links it.
    report_party = uuid.uuid4()
    db.add_all(
        [
            Party(
                id=report_party,
                tenant_id=TENANT_A,
                party_type=PartyType.person,
                display_name="Grace Hopper",
            ),
            PartyPerson(party_id=report_party, first_name="Grace", last_name="Hopper"),
        ]
    )
    db.flush()
    report = create_employee(
        db,
        scope=scope,
        command=CreateEmployee(
            party_id=report_party,
            employee_code="report",
            date_of_joining=date(2026, 1, 1),
            status=EmployeeStatus.ACTIVE,
        ),
    )
    top = create_position(
        db,
        scope=scope,
        command=CreatePosition(code="top", name="Top"),
    )
    vacant = create_position(
        db,
        scope=scope,
        command=CreatePosition(code="vacant", name="Vacant", parent_id=top.id),
    )
    leaf = create_position(
        db,
        scope=scope,
        command=CreatePosition(code="leaf", name="Leaf", parent_id=vacant.id),
    )
    manager_assignment = assign_position(
        db,
        scope=scope,
        command=PositionAssignmentCommand(
            employee_id=manager.id,
            position_id=top.id,
            assignment_type=AssignmentType.PRIMARY,
            start_date=date(2026, 1, 1),
        ),
    )
    assign_position(
        db,
        scope=scope,
        command=PositionAssignmentCommand(
            employee_id=report.id,
            position_id=leaf.id,
            assignment_type=AssignmentType.PRIMARY,
            start_date=date(2026, 1, 1),
        ),
    )
    manager_result = resolve_manager(
        db, scope=scope, employee_id=report.id, as_of=date(2026, 2, 1)
    )
    assert manager_result.employee_ids == (manager.id,)
    direct = resolve_direct_reports(
        db, scope=scope, manager_employee_id=manager.id, as_of=date(2026, 2, 1)
    )
    assert direct.employee_ids == (report.id,)
    approval_chain = resolve_approval_chain(
        db, scope=scope, employee_id=report.id, as_of=date(2026, 2, 1)
    )
    assert approval_chain.employee_ids == (manager.id,)

    end_assignment(
        db,
        scope=scope,
        assignment_id=manager_assignment.id,
        end_date=date(2026, 1, 31),
    )
    top.vacancy_routing_policy = VacancyRoutingPolicy.BLOCK
    assert not resolve_manager(
        db, scope=scope, employee_id=report.id, as_of=date(2026, 2, 1)
    ).employee_ids


def test_department_head_and_person_backed_search_are_derived_not_copied(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    employee = _employee(db, code="directory-001")
    department = create_department(
        db, scope=scope, command=CreateCatalogEntry("eng", "Engineering")
    )
    head = create_position(
        db,
        scope=scope,
        command=CreatePosition(
            code="eng-head",
            name="Engineering head",
            department_id=department.id,
            is_department_head=True,
        ),
    )
    assign_position(
        db,
        scope=scope,
        command=PositionAssignmentCommand(
            employee_id=employee.id,
            position_id=head.id,
            assignment_type=AssignmentType.PRIMARY,
            start_date=date(2026, 1, 1),
        ),
    )

    resolved = resolve_department_head(
        db, scope=scope, department_id=department.id, as_of=date(2026, 2, 1)
    )
    assert resolved.employee_ids == (employee.id,)
    assert tuple(row.id for row in search_employees(db, scope=scope, query="Ada")) == (
        employee.id,
    )
    assert tuple(
        row.id for row in search_employees(db, scope=scope, query="directory-001")
    ) == (employee.id,)


def test_position_model_does_not_persist_the_erp_vacancy_cache() -> None:
    assert "is_vacant" not in Position.__table__.c


def test_employment_type_typed_reads_are_immutable_tenant_scoped_and_paged(
    db: Session,
) -> None:
    alpha = register_employment_type(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateEmploymentType(" full ", " Full time ", "Permanent"),
    )
    register_employment_type(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateEmploymentType("contract", "Contractor"),
    )
    register_employment_type(
        db,
        scope=TenantScope(TENANT_B),
        command=CreateEmploymentType("full", "Other tenant"),
    )

    assert alpha.code == "FULL"
    assert alpha.tenant_id == TENANT_A
    assert alpha.name == "Full time"
    assert (
        read_employment_type(
            db, scope=TenantScope(TENANT_A), employment_type_id=alpha.id
        )
        == alpha
    )
    with pytest.raises(FrozenInstanceError):
        alpha.name = "mutable"  # type: ignore[misc]
    with pytest.raises(NotFound):
        read_employment_type(
            db, scope=TenantScope(TENANT_B), employment_type_id=alpha.id
        )

    page = list_employment_types(
        db,
        scope=TenantScope(TENANT_A),
        query=EmploymentTypeQuery(search="t", offset=1, limit=1),
    )
    assert page.total == 2
    assert page.offset == 1
    assert page.limit == 1
    assert tuple(item.code for item in page.items) == ("FULL",)

    exact = list_employment_types(
        db,
        scope=TenantScope(TENANT_A),
        query=EmploymentTypeQuery(code=" full ", limit=1),
    )
    assert exact.total == 1
    assert tuple(item.id for item in exact.items) == (alpha.id,)


def test_employment_type_codes_are_bounded_and_never_reused_after_deactivation(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    record = register_employment_type(
        db, scope=scope, command=CreateEmploymentType("full", "Full time")
    )
    deactivate_employment_type(
        db, scope=scope, command=DeactivateEmploymentType(record.id)
    )

    with pytest.raises(Conflict, match="already exists"):
        register_employment_type(
            db, scope=scope, command=CreateEmploymentType("FULL", "Replacement")
        )
    with pytest.raises(ValueError, match="at most 20"):
        register_employment_type(
            db, scope=scope, command=CreateEmploymentType("x" * 21, "Too long")
        )
    with pytest.raises(ValueError, match="at most 100"):
        register_employment_type(
            db, scope=scope, command=CreateEmploymentType("ok", "x" * 101)
        )


def test_employment_type_revision_and_lifecycle_are_idempotent(db: Session) -> None:
    scope = TenantScope(TENANT_A)
    created = register_employment_type(
        db,
        scope=scope,
        command=CreateEmploymentType("contract", "Contractor", "Temporary"),
    )
    revised = revise_employment_type(
        db,
        scope=scope,
        command=ReviseEmploymentType(created.id, " vendor ", " Vendor ", None),
    )
    assert (revised.code, revised.name, revised.description) == (
        "VENDOR",
        "Vendor",
        None,
    )

    first_inactive = deactivate_employment_type(
        db, scope=scope, command=DeactivateEmploymentType(created.id)
    )
    replayed_inactive = deactivate_employment_type(
        db, scope=scope, command=DeactivateEmploymentType(created.id)
    )
    assert first_inactive == replayed_inactive
    with pytest.raises(InvalidLifecycle, match="inactive"):
        require_active_employment_type(db, scope=scope, employment_type_id=created.id)

    first_active = activate_employment_type(
        db, scope=scope, command=ActivateEmploymentType(created.id)
    )
    replayed_active = activate_employment_type(
        db, scope=scope, command=ActivateEmploymentType(created.id)
    )
    assert first_active == replayed_active
    assert require_active_employment_type(
        db, scope=scope, employment_type_id=created.id
    ).is_active


def test_new_employee_assignment_refuses_an_inactive_employment_type(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    employment_type = register_employment_type(
        db, scope=scope, command=CreateEmploymentType("former", "Former")
    )
    deactivate_employment_type(
        db, scope=scope, command=DeactivateEmploymentType(employment_type.id)
    )

    with pytest.raises(InvalidLifecycle, match="inactive"):
        create_employee(
            db,
            scope=scope,
            command=CreateEmployee(
                party_id=PERSON_A,
                employee_code="inactive-type",
                employment_type_id=employment_type.id,
                date_of_joining=date(2026, 1, 1),
            ),
        )


def test_employment_type_reconciliation_preserves_source_id_and_is_repeatable(
    db: Session,
) -> None:
    scope = TenantScope(TENANT_A)
    source_id = uuid.uuid4()
    source_fingerprint = "a" * 64
    source_created_at = datetime(2020, 3, 4, 5, 6, tzinfo=UTC)
    source_updated_at = datetime(2021, 4, 5, 6, 7, tzinfo=UTC)
    command = ReconcileEmploymentType(
        source_id=source_id,
        source_fingerprint=source_fingerprint,
        source_created_at=source_created_at,
        source_updated_at=source_updated_at,
        code="perm",
        name="Permanent",
        description=None,
        is_active=True,
    )

    created = reconcile_employment_type(db, scope=scope, command=command)
    assert created.action == ReconcileAction.CREATED
    assert created.record.id == source_id
    assert created.record.created_at == source_created_at
    assert created.record.updated_at == source_updated_at
    assert created.source_fingerprint == source_fingerprint
    assert created.target_fingerprint.startswith("et1:")

    replayed = reconcile_employment_type(db, scope=scope, command=command)
    assert replayed.action == ReconcileAction.UNCHANGED
    assert replayed.target_fingerprint == created.target_fingerprint

    updated = reconcile_employment_type(
        db,
        scope=scope,
        command=ReconcileEmploymentType(
            source_id=source_id,
            source_fingerprint="b" * 64,
            source_created_at=source_created_at,
            source_updated_at=datetime(2022, 5, 6, 7, 8, tzinfo=UTC),
            code="perm",
            name="Permanent staff",
            description="Imported",
            is_active=False,
        ),
    )
    assert updated.action == ReconcileAction.UPDATED
    assert not updated.record.is_active
    assert updated.target_fingerprint != created.target_fingerprint

    with pytest.raises(Conflict, match="source update is stale"):
        reconcile_employment_type(db, scope=scope, command=command)


def test_employment_type_reconciliation_refuses_identity_and_code_collisions(
    db: Session,
) -> None:
    source_id = uuid.uuid4()
    register_employment_type(
        db,
        scope=TenantScope(TENANT_A),
        command=CreateEmploymentType("occupied", "Occupied"),
    )
    db.add(
        EmploymentType(
            id=source_id,
            tenant_id=TENANT_B,
            code="FOREIGN",
            name="Foreign tenant",
            is_active=True,
        )
    )
    db.flush()

    with pytest.raises(Conflict):
        reconcile_employment_type(
            db,
            scope=TenantScope(TENANT_A),
            command=ReconcileEmploymentType(
                source_id=source_id,
                source_fingerprint="c" * 64,
                source_created_at=datetime(2020, 1, 1, tzinfo=UTC),
                source_updated_at=None,
                code="new",
                name="Conflicting identity",
                description=None,
                is_active=True,
            ),
        )
    with pytest.raises(Conflict, match="code"):
        reconcile_employment_type(
            db,
            scope=TenantScope(TENANT_A),
            command=ReconcileEmploymentType(
                source_id=uuid.uuid4(),
                source_fingerprint="d" * 64,
                source_created_at=datetime(2020, 1, 1, tzinfo=UTC),
                source_updated_at=None,
                code="occupied",
                name="Conflicting code",
                description=None,
                is_active=True,
            ),
        )
    with pytest.raises(ValueError, match="source fingerprint"):
        reconcile_employment_type(
            db,
            scope=TenantScope(TENANT_A),
            command=ReconcileEmploymentType(
                source_id=uuid.uuid4(),
                source_fingerprint="not-a-digest",
                source_created_at=datetime(2020, 1, 1, tzinfo=UTC),
                source_updated_at=None,
                code="valid",
                name="Valid",
                description=None,
                is_active=True,
            ),
        )


def test_employment_type_reconciliation_requires_aware_source_timestamps(
    db: Session,
) -> None:
    base = {
        "source_id": uuid.uuid4(),
        "source_fingerprint": "e" * 64,
        "code": "aware",
        "name": "Aware",
        "description": None,
        "is_active": True,
    }
    with pytest.raises(ValueError, match="source_created_at must be timezone-aware"):
        reconcile_employment_type(
            db,
            scope=TenantScope(TENANT_A),
            command=ReconcileEmploymentType(
                **base,
                source_created_at=datetime(2020, 1, 1),
                source_updated_at=None,
            ),
        )
    with pytest.raises(ValueError, match="source_updated_at must be timezone-aware"):
        reconcile_employment_type(
            db,
            scope=TenantScope(TENANT_A),
            command=ReconcileEmploymentType(
                **base,
                source_created_at=datetime(2020, 1, 1, tzinfo=UTC),
                source_updated_at=datetime(2020, 1, 2),
            ),
        )


def test_employment_type_fingerprint_binds_scope_identity_state_and_typed_absence(
    db: Session,
) -> None:
    del db
    row_id = uuid.uuid4()
    base = employment_type_fingerprint(
        scope=TenantScope(TENANT_A),
        employment_type_id=row_id,
        code="FULL",
        name="Full time",
        description=None,
        is_active=True,
    )
    assert base.startswith("et1:")
    assert len(base) == 68
    assert base != employment_type_fingerprint(
        scope=TenantScope(TENANT_A),
        employment_type_id=row_id,
        code="FULL",
        name="Full time",
        description="<absent>",
        is_active=True,
    )
    assert base != employment_type_fingerprint(
        scope=TenantScope(TENANT_B),
        employment_type_id=row_id,
        code="FULL",
        name="Full time",
        description=None,
        is_active=True,
    )
