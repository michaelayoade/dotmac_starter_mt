"""Real-PostgreSQL isolation and temporal-integrity canaries for dotmac-people."""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from dotmac_kernel.cache import TenantScope
from dotmac_people.contracts import (
    Conflict,
    EmploymentTypeQuery,
    ReconcileAction,
    ReconcileEmploymentType,
)
from dotmac_people.service import list_employment_types, reconcile_employment_type
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
ASSEMBLY_VERSIONS = REPO_ROOT / "alembic/versions"
PEOPLE_VERSIONS = (
    REPO_ROOT / "packages/dotmac-people/src/dotmac_people/migrations/versions"
)
TABLES = (
    "employees",
    "departments",
    "designations",
    "employment_types",
    "positions",
    "position_assignments",
)


def _superuser_url() -> str:
    url = os.getenv("TEST_MIGRATION_DATABASE_URL") or os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — the RLS canary needs PostgreSQL")
    return url


def _url_for(base_url: str, dbname: str, *, user: str | None = None) -> str:
    scheme_userhost, _, _ = base_url.rpartition("/")
    if user is not None:
        scheme, _, userhost = scheme_userhost.partition("://")
        host = userhost.rpartition("@")[2]
        scheme_userhost = f"{scheme}://{user}@{host}"
    return f"{scheme_userhost}/{dbname}"


@pytest.fixture
def migrated_people() -> Iterator[tuple[str, str]]:
    superuser = _superuser_url()
    name = f"people_rls_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
    with setup.connect() as conn:
        conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
        conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO app_user'))
        conn.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO platform_api'))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO app_user"))
    setup.dispose()

    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        from alembic import command
        from alembic.config import Config

        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations",
            f"{KERNEL_VERSIONS} {ASSEMBLY_VERSIONS} {PEOPLE_VERSIONS}",
        )
        os.environ["MIGRATION_DATABASE_URL"] = admin_url
        command.upgrade(cfg, "heads")
        yield admin_url, _url_for(superuser, name, user="app_user")
    finally:
        with server.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid()"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        server.dispose()


def _seed_plane(admin_url: str) -> tuple[uuid.UUID, uuid.UUID]:
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    engine = create_engine(admin_url)
    try:
        with engine.begin() as conn:
            for tenant, slug in ((tenant_a, "alpha"), (tenant_b, "bravo")):
                party = uuid.uuid4()
                department = uuid.uuid4()
                designation = uuid.uuid4()
                employment_type = uuid.uuid4()
                employee = uuid.uuid4()
                position = uuid.uuid4()
                assignment = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO public.tenants (id, slug, name) "
                        "VALUES (:id, :slug, :name)"
                    ),
                    {"id": tenant, "slug": slug, "name": slug.title()},
                )
                conn.execute(
                    text(
                        "INSERT INTO public.parties "
                        "(id, tenant_id, party_type, display_name) "
                        "VALUES (:id, :tenant, 'person', :name)"
                    ),
                    {"id": party, "tenant": tenant, "name": f"{slug} employee"},
                )
                conn.execute(
                    text(
                        "INSERT INTO public.party_persons "
                        "(party_id, first_name, last_name) "
                        "VALUES (:id, :name, 'Employee')"
                    ),
                    {"id": party, "name": slug.title()},
                )
                for table, row_id, code, label in (
                    ("departments", department, f"{slug}-dept", "department"),
                    ("designations", designation, f"{slug}-des", "designation"),
                    (
                        "employment_types",
                        employment_type,
                        f"{slug}-type",
                        "employment type",
                    ),
                ):
                    conn.execute(
                        text(
                            f"INSERT INTO mod_people.{table} "  # noqa: S608
                            "(id, tenant_id, code, name) "
                            "VALUES (:id, :tenant, :code, :name)"
                        ),
                        {
                            "id": row_id,
                            "tenant": tenant,
                            "code": code,
                            "name": f"{slug} {label}",
                        },
                    )
                conn.execute(
                    text(
                        "INSERT INTO mod_people.employees "
                        "(id, tenant_id, party_id, employee_code, department_id, "
                        "designation_id, employment_type_id, date_of_joining, status) "
                        "VALUES (:id, :tenant, :party, :code, :department, "
                        ":designation, :employment_type, DATE '2026-01-01', 'ACTIVE')"
                    ),
                    {
                        "id": employee,
                        "tenant": tenant,
                        "party": party,
                        "code": f"{slug}-emp",
                        "department": department,
                        "designation": designation,
                        "employment_type": employment_type,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_people.positions "
                        "(id, tenant_id, code, name, department_id, designation_id) "
                        "VALUES (:id, :tenant, :code, :name, :department, :designation)"
                    ),
                    {
                        "id": position,
                        "tenant": tenant,
                        "code": f"{slug}-pos",
                        "name": f"{slug} position",
                        "department": department,
                        "designation": designation,
                    },
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_people.position_assignments "
                        "(id, tenant_id, employee_id, position_id, "
                        "assignment_type, start_date) VALUES "
                        "(:id, :tenant, :employee, :position, 'PRIMARY', "
                        "DATE '2026-01-01')"
                    ),
                    {
                        "id": assignment,
                        "tenant": tenant,
                        "employee": employee,
                        "position": position,
                    },
                )
    finally:
        engine.dispose()
    return tenant_a, tenant_b


def test_every_table_is_forced_and_has_exactly_the_tenant_policy(
    migrated_people: tuple[str, str],
) -> None:
    admin_url, _ = migrated_people
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            for table in TABLES:
                enabled, forced = conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                        "WHERE oid = CAST(:table AS regclass)"
                    ),
                    {"table": f"mod_people.{table}"},
                ).one()
                assert enabled and forced, table
                policies = list(
                    conn.execute(
                        text(
                            "SELECT policyname FROM pg_policies "
                            "WHERE schemaname = 'mod_people' AND tablename = :table"
                        ),
                        {"table": table},
                    ).scalars()
                )
                assert policies == [f"{table}_tenant_isolation"]
                assert not conn.execute(
                    text(
                        "SELECT has_table_privilege("
                        "'platform_api', CAST(:table AS text), 'SELECT')"
                    ),
                    {"table": f"mod_people.{table}"},
                ).scalar_one()
    finally:
        engine.dispose()


def test_real_app_user_connection_sees_only_its_tenant_on_all_six_tables(
    migrated_people: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_people
    tenant_a, tenant_b = _seed_plane(admin_url)
    engine = create_engine(app_url)
    try:
        for tenant in (tenant_a, tenant_b):
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant)},
                )
                for table in TABLES:
                    tenants = set(
                        conn.execute(
                            text(f"SELECT tenant_id FROM mod_people.{table}")  # noqa: S608
                        ).scalars()
                    )
                    assert tenants == {tenant}, table
    finally:
        engine.dispose()


def test_app_user_cannot_insert_for_another_tenant(
    migrated_people: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_people
    tenant_a, tenant_b = _seed_plane(admin_url)
    engine = create_engine(app_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_b)},
            )
            with pytest.raises(DBAPIError, match="row-level security"):
                conn.execute(
                    text(
                        "INSERT INTO mod_people.departments "
                        "(id, tenant_id, code, name) "
                        "VALUES (:id, :tenant, 'INTRUDE', 'Intrude')"
                    ),
                    {"id": uuid.uuid4(), "tenant": tenant_a},
                )
    finally:
        engine.dispose()


def test_employment_type_reconcile_conflict_preserves_rls_scope_and_transaction(
    migrated_people: tuple[str, str],
) -> None:
    """A hidden cross-tenant UUID collision rolls back only its savepoint."""
    admin_url, app_url = migrated_people
    tenant_a, tenant_b = _seed_plane(admin_url)
    admin = create_engine(admin_url)
    try:
        with admin.connect() as conn:
            tenant_b_type = conn.execute(
                text(
                    "SELECT id FROM mod_people.employment_types "
                    "WHERE tenant_id = :tenant"
                ),
                {"tenant": tenant_b},
            ).scalar_one()
    finally:
        admin.dispose()

    engine = create_engine(app_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_a)},
            )
            with Session(bind=connection) as db:
                with pytest.raises(Conflict):
                    reconcile_employment_type(
                        db,
                        scope=TenantScope(tenant_a),
                        command=ReconcileEmploymentType(
                            source_id=tenant_b_type,
                            source_fingerprint="a" * 64,
                            source_created_at=datetime(2020, 1, 1, tzinfo=UTC),
                            source_updated_at=None,
                            code="COLLISION",
                            name="Collision",
                            description=None,
                            is_active=True,
                        ),
                    )

                created = reconcile_employment_type(
                    db,
                    scope=TenantScope(tenant_a),
                    command=ReconcileEmploymentType(
                        source_id=uuid.uuid4(),
                        source_fingerprint="b" * 64,
                        source_created_at=datetime(2020, 1, 1, tzinfo=UTC),
                        source_updated_at=datetime(2020, 2, 1, tzinfo=UTC),
                        code="CASUAL",
                        name="Casual",
                        description=None,
                        is_active=True,
                    ),
                )
                assert created.action == ReconcileAction.CREATED
                page = list_employment_types(
                    db,
                    scope=TenantScope(tenant_a),
                    query=EmploymentTypeQuery(limit=20),
                )
                assert {item.code for item in page.items} == {"alpha-type", "CASUAL"}
                assert connection.scalar(
                    text("SELECT current_setting('app.current_tenant', true)")
                ) == str(tenant_a)
    finally:
        engine.dispose()


def test_database_trigger_rejects_finite_primary_overlap_but_allows_acting(
    migrated_people: tuple[str, str],
) -> None:
    admin_url, app_url = migrated_people
    tenant_a, _ = _seed_plane(admin_url)
    admin = create_engine(admin_url)
    second_employee = uuid.uuid4()
    with admin.begin() as conn:
        employee = conn.execute(
            text("SELECT id FROM mod_people.employees WHERE tenant_id = :tenant"),
            {"tenant": tenant_a},
        ).scalar_one()
        existing_position = conn.execute(
            text("SELECT id FROM mod_people.positions WHERE tenant_id = :tenant"),
            {"tenant": tenant_a},
        ).scalar_one()
        second_party = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO public.parties "
                "(id, tenant_id, party_type, display_name) "
                "VALUES (:id, :tenant, 'person', 'Second Employee')"
            ),
            {"id": second_party, "tenant": tenant_a},
        )
        conn.execute(
            text(
                "INSERT INTO public.party_persons "
                "(party_id, first_name, last_name) "
                "VALUES (:id, 'Second', 'Employee')"
            ),
            {"id": second_party},
        )
        conn.execute(
            text(
                "INSERT INTO mod_people.employees "
                "(id, tenant_id, party_id, employee_code, date_of_joining, status) "
                "VALUES (:id, :tenant, :party, 'SECOND', "
                "DATE '2026-01-01', 'ACTIVE')"
            ),
            {"id": second_employee, "tenant": tenant_a, "party": second_party},
        )
    admin.dispose()

    engine = create_engine(app_url)
    try:
        with pytest.raises(DBAPIError, match="overlaps for employee"):
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant_a)},
                )
                conn.execute(
                    text(
                        "UPDATE mod_people.position_assignments SET end_date = "
                        "DATE '2026-01-31' WHERE tenant_id = :tenant"
                    ),
                    {"tenant": tenant_a},
                )
                second_position = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO mod_people.positions (id, tenant_id, code, name) "
                        "VALUES (:id, :tenant, 'SECOND', 'Second')"
                    ),
                    {"id": second_position, "tenant": tenant_a},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_people.position_assignments "
                        "(id, tenant_id, employee_id, position_id, assignment_type, "
                        "start_date, end_date) VALUES "
                        "(:id, :tenant, :employee, :position, 'PRIMARY', "
                        "DATE '2026-01-15', DATE '2026-02-15')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant_a,
                        "employee": employee,
                        "position": second_position,
                    },
                )
        with pytest.raises(DBAPIError, match="overlaps for position"):
            with engine.begin() as conn:
                conn.execute(
                    text("SELECT set_config('app.current_tenant', :tenant, true)"),
                    {"tenant": str(tenant_a)},
                )
                conn.execute(
                    text(
                        "INSERT INTO mod_people.position_assignments "
                        "(id, tenant_id, employee_id, position_id, assignment_type, "
                        "start_date, end_date) VALUES "
                        "(:id, :tenant, :employee, :position, 'PRIMARY', "
                        "DATE '2026-01-15', DATE '2026-02-15')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant_a,
                        "employee": second_employee,
                        "position": existing_position,
                    },
                )
        with engine.begin() as conn:
            conn.execute(
                text("SELECT set_config('app.current_tenant', :tenant, true)"),
                {"tenant": str(tenant_a)},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_people.position_assignments "
                    "(id, tenant_id, employee_id, position_id, assignment_type, "
                    "start_date, end_date) VALUES "
                    "(:id, :tenant, :employee, :position, 'ACTING', "
                    "DATE '2026-01-15', DATE '2026-02-15')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_a,
                    "employee": employee,
                    "position": existing_position,
                },
            )
    finally:
        engine.dispose()


def test_module_schema_passes_the_kernel_live_catalog_contract(
    migrated_people: tuple[str, str],
) -> None:
    from dotmac_kernel.migrations.catalog import audit_live_schemas
    from dotmac_kernel.namespaces import NamespaceRegistry
    from dotmac_people.manifest import module

    admin_url, _ = migrated_people
    engine = create_engine(admin_url)
    try:
        with engine.connect() as conn:
            registry = NamespaceRegistry.from_manifests([module])
            assert audit_live_schemas(conn, registry) == ()
    finally:
        engine.dispose()


_PARTY_CATALOG_BREAKS = (
    (
        "parties-missing",
        "DROP TABLE public.parties CASCADE",
        "public.parties does not exist",
    ),
    (
        "persons-missing",
        "DROP TABLE public.party_persons CASCADE",
        "public.party_persons does not exist",
    ),
    (
        "person-column-missing",
        "ALTER TABLE public.party_persons DROP COLUMN last_name",
        "missing required columns",
    ),
    (
        "person-column-wrong-type",
        "ALTER TABLE public.party_persons ALTER COLUMN first_name "
        "TYPE integer USING 1",
        "expected String",
    ),
    (
        "parties-primary-key-missing",
        "ALTER TABLE public.parties DROP CONSTRAINT parties_pkey CASCADE",
        "public.parties primary key",
    ),
    (
        "persons-primary-key-missing",
        "ALTER TABLE public.party_persons DROP CONSTRAINT party_persons_pkey CASCADE",
        "public.party_persons primary key",
    ),
    (
        "parties-not-forced",
        "ALTER TABLE public.parties NO FORCE ROW LEVEL SECURITY",
        "ENABLEd and FORCEd",
    ),
    (
        "persons-rls-disabled",
        "ALTER TABLE public.party_persons DISABLE ROW LEVEL SECURITY",
        "ENABLEd and FORCEd",
    ),
    (
        "persons-policy-missing",
        "DROP POLICY party_persons_tenant_isolation ON public.party_persons",
        "no policy tied",
    ),
    (
        "parties-policy-missing",
        "DROP POLICY parties_tenant_isolation ON public.parties",
        "no policy tied",
    ),
    (
        "persons-unreadable",
        "REVOKE SELECT ON public.party_persons FROM app_user",
        "cannot SELECT",
    ),
    (
        "parties-unreadable",
        "REVOKE SELECT ON public.parties FROM app_user",
        "cannot SELECT",
    ),
    (
        "party-composite-identity-missing",
        "ALTER TABLE public.parties DROP CONSTRAINT " "uq_parties_tenant_id_id CASCADE",
        "lacks unique",
    ),
    (
        "person-party-fk-missing",
        "ALTER TABLE public.party_persons DROP CONSTRAINT "
        "party_persons_party_id_fkey",
        "must reference",
    ),
    (
        "party-type-check-missing",
        "ALTER TABLE public.parties DROP CONSTRAINT ck_parties_party_type",
        "does not constrain",
    ),
    (
        "display-name-widened",
        "ALTER TABLE public.parties ALTER COLUMN display_name TYPE varchar(201)",
        "length=201",
    ),
    (
        "active-default-missing",
        "ALTER TABLE public.parties ALTER COLUMN is_active DROP DEFAULT",
        "no server default",
    ),
    (
        "person-name-nullable",
        "ALTER TABLE public.party_persons ALTER COLUMN first_name DROP NOT NULL",
        "nullable=True",
    ),
)


def test_party_catalogue_verifier_refuses_each_observable_break_and_is_sensitive(
    migrated_people: tuple[str, str],
) -> None:
    """Every rule fires; a table-name-only proof misses every shape break."""
    from dotmac_kernel.migrations.verify import (
        PrerequisiteNotSatisfiedError,
        verify_party_person_catalog,
    )
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    admin_url, _ = migrated_people
    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    engine = create_engine(admin_url)
    fired: set[str] = set()
    weak_misses: set[str] = set()
    try:
        with engine.connect() as conn:
            verify_party_person_catalog(conn)
        for case_id, statement, expected in _PARTY_CATALOG_BREAKS:
            with engine.connect() as conn:
                transaction = conn.begin()
                try:
                    conn.execute(text(statement))
                    catalog = inspect(conn)
                    if catalog.has_table(
                        "parties", schema="public"
                    ) and catalog.has_table("party_persons", schema="public"):
                        weak_misses.add(case_id)
                    with pytest.raises(PrerequisiteNotSatisfiedError, match=expected):
                        verify_party_person_catalog(conn)
                    fired.add(case_id)
                finally:
                    transaction.rollback()
    finally:
        engine.dispose()
        install_prerequisite_bindings(previous)

    expected_ids = {case[0] for case in _PARTY_CATALOG_BREAKS}
    assert fired == expected_ids
    assert weak_misses == expected_ids - {"parties-missing", "persons-missing"}
