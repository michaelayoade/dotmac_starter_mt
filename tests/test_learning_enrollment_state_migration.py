"""Hosted real-lineage default retirement, row and security parity canaries."""

from uuid import uuid4

import pytest
from dotmac_learning.participation_models import Enrollment
from sqlalchemy import create_engine, insert, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import (
    KERNEL_VERSIONS,
    LEARNING_VERSIONS,
    REPO_ROOT,
    _person,
    _refused,
    _scope,
    _tenant,
)
from tests.test_learning_participation_catalog import _cohort

learning_database = learning_catalog.learning_database


def _catalog(conn):
    # OIDs and complete definitions catch table recreation or security drift.
    return {
        "table": conn.execute(
            text(
                "SELECT oid, relrowsecurity, relforcerowsecurity, "
                "relacl::text FROM pg_catalog.pg_class WHERE "
                "oid='mod_learning.enrollments'::regclass"
            )
        ).all(),
        "constraints": conn.execute(
            text(
                "SELECT oid, conname, pg_get_constraintdef(oid) FROM "
                "pg_catalog.pg_constraint WHERE "
                "conrelid='mod_learning.enrollments'::regclass ORDER BY oid"
            )
        ).all(),
        "indexes": conn.execute(
            text(
                "SELECT indexrelid, pg_get_indexdef(indexrelid) FROM "
                "pg_catalog.pg_index WHERE "
                "indrelid='mod_learning.enrollments'::regclass ORDER BY "
                "indexrelid"
            )
        ).all(),
        "policies": conn.execute(
            text(
                "SELECT polname, polcmd, polpermissive, polroles, "
                "pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, "
                "polrelid) FROM pg_catalog.pg_policy WHERE "
                "polrelid='mod_learning.enrollments'::regclass ORDER BY "
                "polname"
            )
        ).all(),
        "privileges": conn.execute(
            text(
                "SELECT role, has_table_privilege(role, 'mod_l"
                "earning.enrollments', 'SELECT'), "
                "has_table_privilege(role, 'mod_learning.enrollments', 'INSERT'), "
                "has_table_privilege(role, 'mod_learning.enrollments', 'UPDATE'), "
                "has_table_privilege(role, 'mod_learning.enrollments', 'DELETE') "
                "FROM (VALUES ('app_user'), ('platform_api')) "
                "AS actors(role) ORDER BY role"
            )
        ).all(),
        "columns": [
            (c["name"], str(c["type"]), c["nullable"])
            for c in inspect(conn).get_columns("enrollments", schema="mod_learning")
        ],
    }


def test_schema_only_retirement_preserves_historical_rows_and_catalog(
    learning_database,
):
    from alembic import command
    from alembic.config import Config

    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL_VERSIONS} {LEARNING_VERSIONS}")
    tenant = uuid4()
    try:
        # Downgrade is only a schema test, not recovery of accepted product facts.
        command.downgrade(cfg, "lr_0006_delivery_support")
        with admin.begin() as conn:
            _tenant(conn, tenant)
            people = [_person(conn, tenant) for _ in range(6)]
        with app.begin() as conn:
            _scope(conn, tenant)
            cohort = _cohort(conn, tenant)
            for person, role, status in zip(
                people[:4],
                ("student", "instructor", "", "界" * 20),
                ("active", "dropped", "  ", "unknown-business"),
                strict=True,
            ):
                conn.execute(
                    text(
                        "INSERT INTO mod_learning.enrollments (id, tenant_id, "
                        "cohort_id, person_id, role_in_cohort, status)"
                        " VALUES (:id, "
                        ":tenant, :cohort, :person, :role, :status)"
                    ),
                    {
                        "id": uuid4(),
                        "tenant": tenant,
                        "cohort": cohort,
                        "person": person,
                        "role": role,
                        "status": status,
                    },
                )
            before_rows = conn.execute(
                text(
                    "SELECT row_to_json(e)::text FROM mod_learning.enrollments e "
                    "ORDER BY id"
                )
            ).all()
        with admin.connect() as conn:
            before_catalog = _catalog(conn)
        command.upgrade(cfg, "learning@head")
        with admin.connect() as conn:
            assert _catalog(conn) == before_catalog
            columns = {
                c["name"]: c
                for c in inspect(conn).get_columns("enrollments", schema="mod_learning")
            }
            assert columns["role_in_cohort"]["default"] is None
            assert columns["status"]["default"] is None
            heads = set(conn.scalars(text("SELECT version_num FROM alembic_version")))
            assert "lr_0007_enrollment_state_policy" in heads
            assert "lr_0006_delivery_support" not in heads
        with app.begin() as conn:
            _scope(conn, tenant)
            assert (
                conn.execute(
                    text(
                        "SELECT row_to_json(e)::text FROM mod_learning"
                        ".enrollments e "
                        "ORDER BY id"
                    )
                ).all()
                == before_rows
            )
            for omitted in ("role_in_cohort", "status"):
                # A valid distinct identity prevents FK/unique failures masking 23502.
                supplied = "status" if omitted == "role_in_cohort" else "role_in_cohort"
                sql = {
                    "status": (
                        "INSERT INTO mod_learning.enrollments (id, tenant_id, "
                        "cohort_id, person_id, status) VALUES (:id, :tenant, "
                        ":cohort, :person, 'active')"
                    ),
                    "role_in_cohort": (
                        "INSERT INTO mod_learning.enrollments (id, tenant_id, "
                        "cohort_id, person_id, role_in_cohort) VALUES (:id, :tenant, "
                        ":cohort, :person, 'student')"
                    ),
                }[supplied]
                _refused(
                    conn,
                    sql,
                    {
                        "id": uuid4(),
                        "tenant": tenant,
                        "cohort": cohort,
                        "person": people[4 if omitted == "role_in_cohort" else 5],
                    },
                    "23502",
                )
        with Session(bind=app) as db, db.begin():
            _scope(db.connection(), tenant)
            for omitted in ("role_in_cohort", "status"):
                facts = {
                    "id": uuid4(),
                    "tenant_id": tenant,
                    "cohort_id": cohort,
                    "person_id": people[4 if omitted == "role_in_cohort" else 5],
                    "role_in_cohort": "student",
                    "status": "active",
                }
                del facts[omitted]
                with pytest.raises(IntegrityError) as failure, db.begin_nested():
                    db.execute(insert(Enrollment).values(**facts))
                assert failure.value.orig.sqlstate == "23502"
    finally:
        app.dispose()
        admin.dispose()
