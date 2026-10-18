"""Hosted PostgreSQL migration canaries; no ORM-created substitute storage."""

# SQL identifiers below come only from this file's fixed table/column sets.
# ruff: noqa: S608

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

from tests import test_learning_results as result_tests
from tests.test_learning_catalog import (
    KERNEL_VERSIONS,
    LEARNING_VERSIONS,
    REPO_ROOT,
    _refused,
    _scope,
)

learning_database = result_tests.learning_database
result_database = result_tests.result_database
TABLES = (
    "offering_activities",
    "class_sessions",
    "announcements",
    "success_queue_entries",
)


# Literal Academy 096 DDL oracle, independent of live ORM metadata.
FIELDS = {
    "offering_activities": "offering_id activity_id release_at due_at",
    "class_sessions": (
        "cohort_id offering_id session_type title starts_at ends_at "
        "instructor_person_id location join_url notes status"
    ),
    "announcements": "cohort_id author_person_id title body_md body_html",
    "success_queue_entries": (
        "person_id cohort_id reason_kind supporting_facts severity detected_at "
        "assigned_to recommended_action status resolved_at resolved_by score_hint"
    ),
}

NULLABLE = {
    "offering_activities": {"release_at", "due_at"},
    "class_sessions": {
        "offering_id",
        "ends_at",
        "instructor_person_id",
        "location",
        "join_url",
        "notes",
    },
    "announcements": {"cohort_id"},
    "success_queue_entries": {"cohort_id", "assigned_to", "resolved_at", "resolved_by"},
}

INDEXES = {
    "offering_activities": {
        "ix_offering_activities_tenant_id": ("tenant_id",),
        "ix_offering_activities_offering_id": ("offering_id",),
        "ix_offering_activities_activity_id": ("activity_id",),
    },
    "class_sessions": {
        "ix_class_sessions_tenant_id": ("tenant_id",),
        "ix_class_sessions_cohort_start": ("tenant_id", "cohort_id", "starts_at"),
        "ix_class_sessions_instructor": ("tenant_id", "instructor_person_id"),
    },
    "announcements": {
        "ix_announcements_tenant_id": ("tenant_id",),
        "ix_announcements_cohort_id": ("cohort_id",),
    },
    "success_queue_entries": {
        "ix_success_queue_tenant_id": ("tenant_id",),
        "ix_success_queue_person_id": ("person_id",),
        "ix_success_queue_cohort_id": ("cohort_id",),
        "ix_success_queue_status": ("tenant_id", "status", "severity"),
        "uq_success_queue_open_person_reason": (
            "tenant_id",
            "person_id",
            "reason_kind",
        ),
    },
}

LENGTHS = {
    "offering_activities": {},
    "class_sessions": {
        "session_type": 20,
        "title": 200,
        "location": 160,
        "join_url": 500,
        "status": 20,
    },
    "announcements": {"title": 200},
    "success_queue_entries": {"reason_kind": 30, "severity": 10, "status": 15},
}


def _insert(conn, table, tenant, person, cohort, offering, activity, **overrides):
    assert table in TABLES
    assert set(overrides) <= {
        "id",
        "tenant_id",
        "created_at",
        "updated_at",
        *FIELDS[table].split(),
    }
    values = {
        "offering_activities": {"offering_id": offering, "activity_id": activity},
        "class_sessions": {
            "cohort_id": cohort,
            "title": "Live",
            "starts_at": "2026-01-01T00:00:00Z",
        },
        "announcements": {
            "author_person_id": person,
            "title": "News",
            "body_md": "**Hi**",
            "body_html": "<b>Hi</b>",
        },
        "success_queue_entries": {
            "person_id": person,
            "reason_kind": "inactivity",
            "detected_at": "2026-01-01T00:00:00Z",
        },
    }[table]
    values = {"id": uuid4(), "tenant_id": tenant, **values, **overrides}
    conn.execute(
        text(
            f"INSERT INTO mod_learning.{table} ({', '.join(values)}) "
            f"VALUES ({', '.join(':' + key for key in values)})"
        ),
        values,
    )
    return values["id"]


def _parents(conn, tenant, course):
    cohort, offering = uuid4(), uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_learning.cohorts (id, tenant_id, name, discipline) "
            "VALUES (:id, :tenant, 'Cohort', 'engineering')"
        ),
        {"id": cohort, "tenant": tenant},
    )
    conn.execute(
        text(
            "INSERT INTO mod_learning.course_offerings "
            "(id, tenant_id, cohort_id, course_id) "
            "VALUES (:id, :tenant, :cohort, :course)"
        ),
        {"id": offering, "tenant": tenant, "cohort": cohort, "course": course},
    )
    return cohort, offering


def test_all_four_tables_real_app_role_isolation_defaults_and_grants(result_database):
    admin, app, tenant, person, course, activity, other, *_ = result_database
    with app.begin() as conn:
        _scope(conn, tenant)
        role = conn.execute(
            text(
                "SELECT r.rolsuper, r.rolbypassrls FROM pg_roles r "
                "WHERE rolname=current_user"
            )
        ).one()
        assert conn.scalar(text("SELECT current_user")) == "app_user"
        assert role == (False, False)
        cohort, offering = _parents(conn, tenant, course)
        ids = {
            table: _insert(conn, table, tenant, person, cohort, offering, activity)
            for table in TABLES
        }
        row = conn.execute(
            text(
                "SELECT supporting_facts, severity, recommended_action, status, "
                "score_hint FROM mod_learning.success_queue_entries"
            )
        ).one()
        assert row == ({}, "medium", "", "open", 0.0)
        assert conn.execute(
            text("SELECT session_type, status FROM mod_learning.class_sessions")
        ).one() == ("live_class", "scheduled")
        for table in TABLES:
            with pytest.raises(DBAPIError) as caught, conn.begin_nested():
                _insert(conn, table, other, person, cohort, offering, activity)
            assert caught.value.orig.sqlstate == "42501"
            _refused(
                conn,
                f"UPDATE mod_learning.{table} SET tenant_id=:other WHERE id=:id",
                {"other": other, "id": ids[table]},
                "42501",
            )
    with app.begin() as conn:
        _scope(conn, other)
        for table in TABLES:
            assert conn.scalar(text(f"SELECT count(*) FROM mod_learning.{table}")) == 0
            assert (
                conn.execute(
                    text(
                        f"UPDATE mod_learning.{table} SET updated_at=now() WHERE id=:id"
                    ),
                    {"id": ids[table]},
                ).rowcount
                == 0
            )
            assert (
                conn.execute(
                    text(f"DELETE FROM mod_learning.{table} WHERE id=:id"),
                    {"id": ids[table]},
                ).rowcount
                == 0
            )
    with app.begin() as conn:
        for table in TABLES:
            assert conn.scalar(text(f"SELECT count(*) FROM mod_learning.{table}")) == 0
    with admin.connect() as conn:
        for table, count in zip(TABLES, (8, 15, 9, 16), strict=True):
            assert len(inspect(conn).get_columns(table, schema="mod_learning")) == count
            assert conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE oid=CAST(:table AS regclass)"
                ),
                {"table": "mod_learning." + table},
            ).one() == (True, True)
            for role in ("app_user", "platform_api"):
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert conn.scalar(
                        text("SELECT has_table_privilege(:role, :table, :privilege)"),
                        {
                            "role": role,
                            "table": "mod_learning." + table,
                            "privilege": privilege,
                        },
                    )


@pytest.mark.parametrize(
    "table,column",
    (("announcements", "author_person_id"), ("success_queue_entries", "person_id")),
)
def test_genuine_profile_and_tenant_party_dual_fks(result_database, table, column):
    _, app, tenant, person, course, activity, _, foreign_person, root_only, *_ = (
        result_database
    )
    with app.begin() as conn:
        _scope(conn, tenant)
        cohort, offering = _parents(conn, tenant, course)
        for invalid in (foreign_person, root_only, uuid4()):
            with pytest.raises(DBAPIError) as caught, conn.begin_nested():
                _insert(
                    conn,
                    table,
                    tenant,
                    person,
                    cohort,
                    offering,
                    activity,
                    **{column: invalid},
                )
            assert caught.value.orig.sqlstate == "23503"


def test_open_only_unique_and_unconstrained_logical_references(result_database):
    _, app, tenant, person, course, activity, *_ = result_database
    with app.begin() as conn:
        _scope(conn, tenant)
        cohort, offering = _parents(conn, tenant, course)
        logical = uuid4()
        _insert(
            conn,
            "class_sessions",
            tenant,
            person,
            cohort,
            offering,
            activity,
            offering_id=logical,
            instructor_person_id=logical,
        )
        _insert(
            conn,
            "success_queue_entries",
            tenant,
            person,
            cohort,
            offering,
            activity,
            cohort_id=logical,
            assigned_to=logical,
            resolved_by=logical,
        )
        with pytest.raises(DBAPIError) as caught, conn.begin_nested():
            _insert(
                conn,
                "success_queue_entries",
                tenant,
                person,
                cohort,
                offering,
                activity,
            )
        assert caught.value.orig.sqlstate == "23505"
        for status in ("acknowledged", "acknowledged", "resolved", "resolved"):
            _insert(
                conn,
                "success_queue_entries",
                tenant,
                person,
                cohort,
                offering,
                activity,
                status=status,
            )
        _insert(conn, "offering_activities", tenant, person, cohort, offering, activity)
        with pytest.raises(DBAPIError) as caught, conn.begin_nested():
            _insert(
                conn, "offering_activities", tenant, person, cohort, offering, activity
            )
        assert caught.value.orig.sqlstate == "23505"
        for table, overrides in (
            ("class_sessions", {"cohort_id": logical}),
            ("announcements", {"cohort_id": logical}),
            ("offering_activities", {"offering_id": logical}),
        ):
            with pytest.raises(DBAPIError) as caught, conn.begin_nested():
                _insert(
                    conn, table, tenant, person, cohort, offering, activity, **overrides
                )
            assert caught.value.orig.sqlstate == "23503"


def test_downgrade_reupgrade_preserves_kernel_audit_and_cohort_mode(learning_database):
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL_VERSIONS} {LEARNING_VERSIONS}")
    command.downgrade(cfg, "lr_0004_assessment")
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            catalog = inspect(conn)
            assert not set(TABLES) & set(catalog.get_table_names(schema="mod_learning"))
            assert "delivery_mode" in {
                col["name"]
                for col in catalog.get_columns("cohorts", schema="mod_learning")
            }
            assert catalog.has_table("audit_events", schema="public")
        command.upgrade(cfg, "learning@head")
        with engine.connect() as conn:
            assert set(TABLES) <= set(
                inspect(conn).get_table_names(schema="mod_learning")
            )
    finally:
        engine.dispose()


def test_exact_catalog_columns_indexes_foreign_keys_and_policy(learning_database):
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            catalog = inspect(conn)
            for table in TABLES:
                columns = catalog.get_columns(table, schema="mod_learning")
                expected = {
                    "id",
                    "tenant_id",
                    "created_at",
                    "updated_at",
                    *FIELDS[table].split(),
                }
                assert {column["name"] for column in columns} == expected
                assert {
                    column["name"] for column in columns if column["nullable"]
                } == NULLABLE[table]
                assert catalog.get_pk_constraint(table, schema="mod_learning")[
                    "constrained_columns"
                ] == ["id"]
                expected_uniques = {("tenant_id", "id")}
                if table == "offering_activities":
                    expected_uniques.add(("tenant_id", "offering_id", "activity_id"))
                assert {
                    tuple(constraint["column_names"])
                    for constraint in catalog.get_unique_constraints(
                        table, schema="mod_learning"
                    )
                } == expected_uniques
                assert catalog.get_check_constraints(table, schema="mod_learning") == []
                native_types = dict(
                    conn.execute(
                        text(
                            "SELECT a.attname, "
                            "pg_catalog.format_type(a.atttypid, a.atttypmod) "
                            "FROM pg_catalog.pg_attribute a "
                            "JOIN pg_catalog.pg_class c ON c.oid=a.attrelid "
                            "JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                            "WHERE n.nspname='mod_learning' AND c.relname=:table "
                            "AND a.attnum>0 AND NOT a.attisdropped"
                        ),
                        {"table": table},
                    )
                    .tuples()
                    .all()
                )
                for name in expected:
                    if (
                        name == "id"
                        or name.endswith("_id")
                        or name in {"assigned_to", "resolved_by"}
                    ):
                        expected_type = "uuid"
                    elif name in LENGTHS[table]:
                        expected_type = f"character varying({LENGTHS[table][name]})"
                    elif name.endswith("_at"):
                        expected_type = "timestamp with time zone"
                    elif name == "supporting_facts":
                        expected_type = "jsonb"
                    elif name == "score_hint":
                        expected_type = "double precision"
                    else:
                        expected_type = "text"
                    assert native_types[name] == expected_type
                expected_defaults = {
                    "created_at": {"now()"},
                    "updated_at": {"now()"},
                }
                if table == "class_sessions":
                    expected_defaults.update(
                        session_type={"'live_class'::character varying"},
                        status={"'scheduled'::character varying"},
                    )
                elif table == "success_queue_entries":
                    expected_defaults.update(
                        supporting_facts={"'{}'::jsonb"},
                        severity={"'medium'::character varying"},
                        recommended_action={"''::text"},
                        status={"'open'::character varying"},
                        score_hint={"'0'::double precision", "0"},
                    )
                for column in columns:
                    assert column["default"] in expected_defaults.get(
                        column["name"], {None}
                    )
                    if column["name"] in LENGTHS[table]:
                        assert column["type"].length == LENGTHS[table][column["name"]]
                    if column["name"] in ("created_at", "updated_at"):
                        assert column["default"] == "now()"
                indexes = catalog.get_indexes(table, schema="mod_learning")
                # PostgreSQL also reports the indexes backing UNIQUE constraints.
                indexes = [
                    index for index in indexes if not index.get("duplicates_constraint")
                ]
                assert {
                    index["name"]: tuple(index["column_names"]) for index in indexes
                } == INDEXES[table]
                for index in indexes:
                    partial = index.get("dialect_options", {}).get("postgresql_where")
                    if index["name"] == "uq_success_queue_open_person_reason":
                        assert index["unique"]
                        assert "status" in partial and "'open'" in partial
                    else:
                        assert not index["unique"] and partial is None
                fks = catalog.get_foreign_keys(table, schema="mod_learning")
                assert all(fk["options"].get("ondelete") == "CASCADE" for fk in fks)
                shapes = {
                    (
                        tuple(fk["constrained_columns"]),
                        fk["referred_schema"],
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                    )
                    for fk in fks
                }
                expected_fks = {(("tenant_id",), "public", "tenants", ("id",))}
                for column, parent in {
                    "offering_activities": (
                        ("offering_id", "course_offerings"),
                        ("activity_id", "activities"),
                    ),
                    "class_sessions": (("cohort_id", "cohorts"),),
                    "announcements": (("cohort_id", "cohorts"),),
                    "success_queue_entries": (),
                }[table]:
                    expected_fks.add(
                        (
                            ("tenant_id", column),
                            "mod_learning",
                            parent,
                            ("tenant_id", "id"),
                        )
                    )
                if table in ("announcements", "success_queue_entries"):
                    column = (
                        "author_person_id" if table == "announcements" else "person_id"
                    )
                    expected_fks.add(
                        (
                            ("tenant_id", column),
                            "public",
                            "parties",
                            ("tenant_id", "id"),
                        )
                    )
                    expected_fks.add(
                        ((column,), "public", "party_persons", ("party_id",))
                    )
                assert shapes == expected_fks
                policies = conn.execute(
                    text(
                        "SELECT cmd, permissive, roles, qual, with_check "
                        "FROM pg_policies "
                        "WHERE schemaname='mod_learning' AND tablename=:table"
                    ),
                    {"table": table},
                ).all()
                assert len(policies) == 1
                cmd, permissive, roles, using, checking = policies[0]
                assert (cmd, permissive, roles) == ("ALL", "PERMISSIVE", ["public"])
                assert using == checking
                assert "tenant_id = app_current_tenant_id()" in using
    finally:
        engine.dispose()


def test_real_damaged_audit_provider_is_refused_without_learning_ddl(learning_database):
    from importlib import import_module

    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = import_module("dotmac_learning.migrations.versions.lr_0005_tenant_audit")
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn, conn.begin():
            before = set(inspect(conn).get_table_names(schema="mod_learning"))
            # A correct table name with broken privileges must also fail.
            conn.execute(text("GRANT DELETE ON public.audit_events TO app_user"))
            with Operations.context(MigrationContext.configure(conn)):
                with pytest.raises(PrerequisiteNotSatisfiedError, match="app_user"):
                    revision.upgrade()
            assert set(inspect(conn).get_table_names(schema="mod_learning")) == before
            conn.rollback()
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "identity_table,key", (("parties", "id"), ("party_persons", "party_id"))
)
def test_both_translated_identity_delete_paths_cascade(
    result_database, identity_table, key
):
    admin, app, tenant, person, course, activity, *_ = result_database
    with app.begin() as conn:
        _scope(conn, tenant)
        cohort, offering = _parents(conn, tenant, course)
        for table in ("announcements", "success_queue_entries"):
            _insert(conn, table, tenant, person, cohort, offering, activity)
    with admin.begin() as conn:
        assert (identity_table, key) in (
            ("parties", "id"),
            ("party_persons", "party_id"),
        )
        conn.execute(
            text(f"DELETE FROM public.{identity_table} WHERE {key}=:person"),
            {"person": person},
        )
    with app.begin() as conn:
        _scope(conn, tenant)
        for table in ("announcements", "success_queue_entries"):
            assert conn.scalar(text(f"SELECT count(*) FROM mod_learning.{table}")) == 0
