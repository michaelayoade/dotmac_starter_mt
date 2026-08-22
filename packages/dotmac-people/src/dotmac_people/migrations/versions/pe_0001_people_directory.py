"""Create the tenant employment directory as one secure module plane.

Revision ID: pe_0001_people_directory
Revises: (lineage root)
Create Date: 2026-08-18
"""

from __future__ import annotations

import sqlalchemy as sa
from dotmac_kernel.migrations.verify import require_prerequisites
from dotmac_kernel.prerequisites import resolve_depends_on

from alembic import op

revision = "pe_0001_people_directory"
down_revision = None
branch_labels = ("people",)

# Snapshot literals: the gate compares these with the manifest.  The module
# names observable effects, never the foreign revisions that provide them.
REQUIRES = (
    "tenant_scope_catalog.v1",
    "module_database_roles.v1",
    "party_person_catalog.v1",
)
depends_on = resolve_depends_on(REQUIRES)

_SCHEMA = "mod_people"


def upgrade() -> None:
    require_prerequisites(op.get_bind(), REQUIRES)
    op.execute("CREATE SCHEMA IF NOT EXISTS mod_people;")
    op.execute("REVOKE ALL ON SCHEMA mod_people FROM PUBLIC;")
    op.execute("GRANT USAGE ON SCHEMA mod_people TO app_user, app_admin;")

    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_departments_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["mod_people.departments.tenant_id", "mod_people.departments.id"],
            ondelete="RESTRICT",
            name="fk_departments_tenant_parent",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_departments_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_departments_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_departments_tenant_parent",
        "departments",
        ["tenant_id", "parent_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "designations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_designations_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_designations_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_designations_tenant_code"),
        schema=_SCHEMA,
    )

    op.create_table(
        "employment_types",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_employment_types_tenant",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_employment_types_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "code", name="uq_employment_types_tenant_code"
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "employees",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("party_id", sa.Uuid(), nullable=False),
        sa.Column("employee_code", sa.String(30), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("designation_id", sa.Uuid(), nullable=True),
        sa.Column("employment_type_id", sa.Uuid(), nullable=True),
        sa.Column("date_of_joining", sa.Date(), nullable=False),
        sa.Column("date_of_leaving", sa.Date(), nullable=True),
        sa.Column("probation_end_date", sa.Date(), nullable=True),
        sa.Column("confirmation_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_employees_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "party_id"],
            ["public.parties.tenant_id", "public.parties.id"],
            ondelete="RESTRICT",
            name="fk_employees_tenant_party",
        ),
        sa.ForeignKeyConstraint(
            ["party_id"],
            ["public.party_persons.party_id"],
            ondelete="RESTRICT",
            name="fk_employees_party_person",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["mod_people.departments.tenant_id", "mod_people.departments.id"],
            ondelete="RESTRICT",
            name="fk_employees_tenant_department",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "designation_id"],
            ["mod_people.designations.tenant_id", "mod_people.designations.id"],
            ondelete="RESTRICT",
            name="fk_employees_tenant_designation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "employment_type_id"],
            [
                "mod_people.employment_types.tenant_id",
                "mod_people.employment_types.id",
            ],
            ondelete="RESTRICT",
            name="fk_employees_tenant_employment_type",
        ),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'ON_LEAVE', 'SUSPENDED', "
            "'RESIGNED', 'TERMINATED', 'RETIRED')",
            name="ck_employees_status",
        ),
        sa.CheckConstraint(
            "date_of_leaving IS NULL OR date_of_leaving >= date_of_joining",
            name="ck_employees_leaving_after_joining",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_employees_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "party_id", name="uq_employees_tenant_party"),
        sa.UniqueConstraint(
            "tenant_id", "employee_code", name="uq_employees_tenant_code"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_employees_tenant_status",
        "employees",
        ["tenant_id", "status"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_employees_tenant_department",
        "employees",
        ["tenant_id", "department_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "positions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=True),
        sa.Column("designation_id", sa.Uuid(), nullable=True),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column(
            "is_department_head",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            "vacancy_routing_policy",
            sa.String(32),
            nullable=False,
            server_default="SKIP_UP",
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_positions_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "department_id"],
            ["mod_people.departments.tenant_id", "mod_people.departments.id"],
            ondelete="RESTRICT",
            name="fk_positions_tenant_department",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "designation_id"],
            ["mod_people.designations.tenant_id", "mod_people.designations.id"],
            ondelete="RESTRICT",
            name="fk_positions_tenant_designation",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_id"],
            ["mod_people.positions.tenant_id", "mod_people.positions.id"],
            ondelete="RESTRICT",
            name="fk_positions_tenant_parent",
        ),
        sa.CheckConstraint(
            "vacancy_routing_policy IN " "('SKIP_UP', 'BLOCK', 'NOTIFY_HR_THEN_SKIP')",
            name="ck_positions_vacancy_policy",
        ),
        sa.CheckConstraint(
            "NOT is_department_head OR department_id IS NOT NULL",
            name="ck_positions_head_has_department",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_positions_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_positions_tenant_code"),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_positions_tenant_parent",
        "positions",
        ["tenant_id", "parent_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_positions_tenant_department",
        "positions",
        ["tenant_id", "department_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "uq_positions_tenant_department_head",
        "positions",
        ["tenant_id", "department_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text(
            "is_department_head AND is_active AND department_id IS NOT NULL"
        ),
    )

    op.create_table(
        "position_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("employee_id", sa.Uuid(), nullable=False),
        sa.Column("position_id", sa.Uuid(), nullable=False),
        sa.Column("assignment_type", sa.String(16), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_position_assignments_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "employee_id"],
            ["mod_people.employees.tenant_id", "mod_people.employees.id"],
            ondelete="RESTRICT",
            name="fk_position_assignments_tenant_employee",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "position_id"],
            ["mod_people.positions.tenant_id", "mod_people.positions.id"],
            ondelete="RESTRICT",
            name="fk_position_assignments_tenant_position",
        ),
        sa.CheckConstraint(
            "assignment_type IN ('PRIMARY', 'ACTING', 'INTERIM')",
            name="ck_position_assignments_type",
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_position_assignments_date_order",
        ),
        sa.UniqueConstraint(
            "tenant_id", "id", name="uq_position_assignments_tenant_id_id"
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_position_assignments_tenant_employee_dates",
        "position_assignments",
        ["tenant_id", "employee_id", "start_date", "end_date"],
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_position_assignments_tenant_position_dates",
        "position_assignments",
        ["tenant_id", "position_id", "start_date", "end_date"],
        schema=_SCHEMA,
    )

    # ERP's partial uniques guarded only `end_date IS NULL`; two finite,
    # overlapping primary intervals could be inserted directly.  Serialize the
    # two identities in a stable order and reject every overlapping interval.
    op.execute(
        """
        CREATE FUNCTION mod_people.enforce_primary_assignment_no_overlap()
        RETURNS trigger
        LANGUAGE plpgsql
        SET search_path = pg_catalog
        AS $$
        BEGIN
            IF NEW.assignment_type <> 'PRIMARY' THEN
                RETURN NEW;
            END IF;

            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    NEW.tenant_id::text || ':employee:' || NEW.employee_id::text,
                    0
                )
            );
            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    NEW.tenant_id::text || ':position:' || NEW.position_id::text,
                    0
                )
            );

            IF EXISTS (
                SELECT 1
                FROM mod_people.position_assignments AS existing
                WHERE existing.tenant_id = NEW.tenant_id
                  AND existing.id <> NEW.id
                  AND existing.assignment_type = 'PRIMARY'
                  AND existing.employee_id = NEW.employee_id
                  AND daterange(
                        existing.start_date,
                        COALESCE(existing.end_date, 'infinity'::date),
                        '[]'
                      ) && daterange(
                        NEW.start_date,
                        COALESCE(NEW.end_date, 'infinity'::date),
                        '[]'
                      )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'unique_violation',
                    MESSAGE = 'primary assignment overlaps for employee';
            END IF;

            IF EXISTS (
                SELECT 1
                FROM mod_people.position_assignments AS existing
                WHERE existing.tenant_id = NEW.tenant_id
                  AND existing.id <> NEW.id
                  AND existing.assignment_type = 'PRIMARY'
                  AND existing.position_id = NEW.position_id
                  AND daterange(
                        existing.start_date,
                        COALESCE(existing.end_date, 'infinity'::date),
                        '[]'
                      ) && daterange(
                        NEW.start_date,
                        COALESCE(NEW.end_date, 'infinity'::date),
                        '[]'
                      )
            ) THEN
                RAISE EXCEPTION USING
                    ERRCODE = 'unique_violation',
                    MESSAGE = 'primary assignment overlaps for position';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE ALL ON FUNCTION "
        "mod_people.enforce_primary_assignment_no_overlap() FROM PUBLIC;"
    )
    op.execute(
        "CREATE TRIGGER position_assignments_primary_overlap "
        "BEFORE INSERT OR UPDATE OF tenant_id, employee_id, position_id, "
        "assignment_type, start_date, end_date "
        "ON mod_people.position_assignments FOR EACH ROW "
        "EXECUTE FUNCTION mod_people.enforce_primary_assignment_no_overlap();"
    )

    op.execute("ALTER TABLE mod_people.employees ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_people.employees FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY employees_tenant_isolation ON mod_people.employees "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_people.employees TO app_user;"
    )
    op.execute("ALTER TABLE mod_people.departments ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_people.departments FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY departments_tenant_isolation ON mod_people.departments "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_people.departments TO app_user;"
    )
    op.execute("ALTER TABLE mod_people.designations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_people.designations FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY designations_tenant_isolation ON mod_people.designations "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_people.designations TO app_user;"
    )
    op.execute("ALTER TABLE mod_people.employment_types ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_people.employment_types FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY employment_types_tenant_isolation "
        "ON mod_people.employment_types "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_people.employment_types TO app_user;"
    )
    op.execute("ALTER TABLE mod_people.positions ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_people.positions FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY positions_tenant_isolation ON mod_people.positions "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_people.positions TO app_user;"
    )
    op.execute("ALTER TABLE mod_people.position_assignments ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mod_people.position_assignments FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY position_assignments_tenant_isolation "
        "ON mod_people.position_assignments "
        "USING (tenant_id = public.app_current_tenant_id()) "
        "WITH CHECK (tenant_id = public.app_current_tenant_id());"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mod_people.position_assignments TO app_user;"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS position_assignments_primary_overlap "
        "ON mod_people.position_assignments;"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS " "mod_people.enforce_primary_assignment_no_overlap();"
    )
    for table in (
        "position_assignments",
        "positions",
        "employees",
        "employment_types",
        "designations",
        "departments",
    ):
        op.drop_table(table, schema=_SCHEMA)
    op.execute("DROP SCHEMA IF EXISTS mod_people;")
