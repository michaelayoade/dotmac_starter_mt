"""Hosted PostgreSQL proof of source participation and fresh identity translation.

Runs genuine Kernel and learning lineages, with restricted application actors.
These fresh fixtures do not qualify legacy Academy People adoption/cutover.
"""

from __future__ import annotations

import importlib
import uuid

import pytest
from dotmac_kernel.models import Party, PartyPerson
from dotmac_learning.participation_models import (
    Cohort,
    CohortTrack,
    CourseOffering,
    CoursePrerequisite,
    Enrollment,
    Track,
    TrackCourse,
)
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import (
    ALL_TABLES,
    KERNEL_VERSIONS,
    LEARNING_VERSIONS,
    REPO_ROOT,
    _course,
    _foreign_keys,
    _person,
    _refused,
    _scope,
    _tenant,
)
from tests.unit.test_learning_participation_models import (
    DEFAULTS,
    FIELDS,
    INDEXES,
    LENGTHS,
    NULLABLE,
    UNIQUES,
)

learning_database = learning_catalog.learning_database

BASE_TABLES = {
    "courses",
    "activities",
    "submissions",
    "scores",
    "course_completions",
    "certificates",
    "learning_events",
    "chapters",
    "question_banks",
    "questions",
    "chapter_reads",
    "subtopic_reads",
    "chapter_bookmarks",
    "chapter_notes",
}

TABLES = {
    "cohorts",
    "enrollments",
    "tracks",
    "track_courses",
    "cohort_tracks",
    "course_offerings",
    "course_prerequisites",
}


RELATIONS = {
    "cohorts": Cohort.__table__,
    "enrollments": Enrollment.__table__,
    "tracks": Track.__table__,
    "track_courses": TrackCourse.__table__,
    "cohort_tracks": CohortTrack.__table__,
    "course_offerings": CourseOffering.__table__,
    "course_prerequisites": CoursePrerequisite.__table__,
}
IDENTITY_RELATIONS = {
    "parties": Party.__table__,
    "party_persons": PartyPerson.__table__,
}


def _row(conn, table, tenant, **values):
    row = {"id": uuid.uuid4(), "tenant_id": tenant, **values}
    conn.execute(RELATIONS[table].insert().values(**row))
    return row["id"]


def _cohort(conn, tenant):
    return _row(conn, "cohorts", tenant, name="Cohort", discipline="engineering")


def _track(conn, tenant):
    return _row(conn, "tracks", tenant, slug=uuid.uuid4().hex, name="Track")


def _enrollment(conn, tenant, cohort, person, track=None):
    return _row(
        conn,
        "enrollments",
        tenant,
        cohort_id=cohort,
        person_id=person,
        track_id=track,
        role_in_cohort="student",
        status="active",
    )


def test_participation_tenant_isolation_and_identity_translation_refusals(
    learning_database,
):
    admin, app = (create_engine(url) for url in learning_database)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    try:
        with admin.begin() as conn:
            for tenant in (tenant_a, tenant_b):
                _tenant(conn, tenant)
            person_a, person_b = _person(conn, tenant_a), _person(conn, tenant_b)
            root_only = _person(conn, tenant_a, subtype=False)
        with app.begin() as conn:
            _scope(conn, tenant_a)
            cohort = _cohort(conn, tenant_a)
            track = _track(conn, tenant_a)
            course, requires = _course(conn, tenant_a), _course(conn, tenant_a)
            _row(conn, "cohort_tracks", tenant_a, cohort_id=cohort, track_id=track)
            _row(conn, "track_courses", tenant_a, track_id=track, course_id=course)
            _row(conn, "course_offerings", tenant_a, cohort_id=cohort, course_id=course)
            _row(
                conn,
                "course_prerequisites",
                tenant_a,
                course_id=course,
                requires_course_id=requires,
            )
            enrollment = _enrollment(conn, tenant_a, cohort, person_a, track)
            for invalid in (person_b, root_only, uuid.uuid4()):
                _refused(
                    conn,
                    (
                        "INSERT INTO mod_learning.enrollments (id, tenant_id, "
                        "cohort_id, person_id, role_in_cohort, status)"
                        " VALUES (:id, :tenant, :cohort, "
                        ":person, 'student', 'active')"
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant_a,
                        "cohort": cohort,
                        "person": invalid,
                    },
                    "23503",
                )
            # Preserve the stable UUID; manufacture no profile or substitute identity.
            assert (
                conn.scalar(
                    text("SELECT person_id FROM mod_learning.enrollments WHERE id=:id"),
                    {"id": enrollment},
                )
                == person_a
            )
        with app.begin() as conn:
            _scope(conn, tenant_b)
            for table in TABLES:
                assert (
                    conn.scalar(select(func.count()).select_from(RELATIONS[table])) == 0
                )
                assert (
                    conn.execute(
                        RELATIONS[table]
                        .update()
                        .values(updated_at=func.now())
                        .returning(RELATIONS[table].c.id)
                    ).all()
                    == []
                )
                assert (
                    conn.execute(
                        RELATIONS[table].delete().returning(RELATIONS[table].c.id)
                    ).all()
                    == []
                )
            cohort_b = _cohort(conn, tenant_b)
            for table, fields in (
                (
                    "enrollments",
                    {
                        "cohort_id": cohort,
                        "person_id": person_b,
                        "role_in_cohort": "student",
                        "status": "active",
                    },
                ),
                ("cohort_tracks", {"cohort_id": cohort_b, "track_id": track}),
                ("track_courses", {"track_id": track, "course_id": course}),
                ("course_offerings", {"cohort_id": cohort_b, "course_id": course}),
                (
                    "course_prerequisites",
                    {"course_id": course, "requires_course_id": requires},
                ),
            ):
                with pytest.raises(DBAPIError) as caught, conn.begin_nested():
                    _row(conn, table, tenant_b, **fields)
                assert getattr(caught.value.orig, "sqlstate", None) == "23503"
            _refused(
                conn,
                "INSERT INTO mod_learning.cohorts (id, tenant_id, name, "
                "discipline) VALUES (:id, :tenant, 'cross', 'x')",
                {"id": uuid.uuid4(), "tenant": tenant_a},
                "42501",
            )
        with app.begin() as conn:
            for table in TABLES:
                assert (
                    conn.scalar(select(func.count()).select_from(RELATIONS[table])) == 0
                )
    finally:
        app.dispose()
        admin.dispose()


@pytest.mark.parametrize(
    "identity_table,column", [("parties", "id"), ("party_persons", "party_id")]
)
def test_identity_translation_both_kernel_deletions_cascade_enrollment(
    learning_database, identity_table, column
):
    admin, app = (create_engine(url) for url in learning_database)
    tenant = uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            enrollment = _enrollment(conn, tenant, _cohort(conn, tenant), person)
        with admin.begin() as conn:
            relation = IDENTITY_RELATIONS[identity_table]
            conn.execute(relation.delete().where(relation.c[column] == person))
            assert (
                conn.scalar(
                    text("SELECT count(*) FROM mod_learning.enrollments WHERE id=:id"),
                    {"id": enrollment},
                )
                == 0
            )
            if identity_table == "party_persons":
                assert (
                    conn.scalar(
                        text("SELECT count(*) FROM public.parties WHERE id=:id"),
                        {"id": person},
                    )
                    == 1
                )
    finally:
        app.dispose()
        admin.dispose()


def test_null_track_no_action_membership_source_checks_defaults_and_updates(
    learning_database,
):
    admin, app = (create_engine(url) for url in learning_database)
    tenant = uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person, second_person = _person(conn, tenant), _person(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            cohort, track = _cohort(conn, tenant), _track(conn, tenant)
            # NULL track bypasses membership under MATCH SIMPLE.
            enrollment = _enrollment(conn, tenant, cohort, person)
            assert conn.execute(
                text(
                    "SELECT role_in_cohort, status, audience, employee_ref, "
                    "access_ends_at FROM mod_learning.enrollments WHERE id=:id"
                ),
                {"id": enrollment},
            ).one() == ("student", "active", None, None, None)
            _refused(
                conn,
                "UPDATE mod_learning.enrollments SET track_id=:track WHERE id=:id",
                {"track": track, "id": enrollment},
                "23503",
            )
            membership = _row(
                conn, "cohort_tracks", tenant, cohort_id=cohort, track_id=track
            )
            conn.execute(
                text(
                    "UPDATE mod_learning.enrollments SET track_id=:track WHERE id=:id"
                ),
                {"track": track, "id": enrollment},
            )
            _refused(
                conn,
                "DELETE FROM mod_learning.cohort_tracks WHERE id=:id",
                {"id": membership},
                "23503",
            )
            _refused(
                conn,
                "UPDATE mod_learning.cohort_tracks SET track_id=:track WHERE id=:id",
                {"id": membership, "track": _track(conn, tenant)},
                "23503",
            )
            for audience in ("unknown", "staff"):
                with pytest.raises(DBAPIError) as caught, conn.begin_nested():
                    conn.execute(
                        Enrollment.__table__.update()
                        .where(Enrollment.id == enrollment)
                        .values(audience=audience)
                    )
                assert getattr(caught.value.orig, "sqlstate", None) == "23514"
            conn.execute(
                text(
                    "UPDATE mod_learning.enrollments SET audience='staff', "
                    "employee_ref='ERP-1', access_ends_at='2030-01-01T00:00:00Z',"
                    " status='custom' WHERE id=:id"
                ),
                {"id": enrollment},
            )
            assert conn.execute(
                text(
                    "SELECT audience, employee_ref, status FROM "
                    "mod_learning.enrollments WHERE id=:id"
                ),
                {"id": enrollment},
            ).one() == ("staff", "ERP-1", "custom")
            # No invented threshold, order, status or prerequisite self-edge checks.
            conn.execute(
                text(
                    "UPDATE mod_learning.cohorts SET status='custom', "
                    "auto_accept_threshold=2, entrance_time_limit_minutes=-1, "
                    "entrance_bank_id=:bank WHERE id=:id"
                ),
                {"bank": uuid.uuid4(), "id": cohort},
            )
            course = _course(conn, tenant)
            tc = _row(conn, "track_courses", tenant, track_id=track, course_id=course)
            assert (
                conn.scalar(
                    text(
                        "SELECT order_index FROM mod_learning.track_courses WHERE"
                        " id=:id"
                    ),
                    {"id": tc},
                )
                == 0
            )
            conn.execute(
                text(
                    "UPDATE mod_learning.track_courses SET order_index=-1 WHERE id=:id"
                ),
                {"id": tc},
            )
            _row(
                conn,
                "course_prerequisites",
                tenant,
                course_id=course,
                requires_course_id=course,
            )
            offering = _row(
                conn, "course_offerings", tenant, cohort_id=cohort, course_id=course
            )
            assert conn.execute(
                text(
                    "SELECT status, starts_at, ends_at FROM "
                    "mod_learning.course_offerings WHERE id=:id"
                ),
                {"id": offering},
            ).one() == ("active", None, None)
            _refused(
                conn,
                (
                    "INSERT INTO mod_learning.enrollments (id, tenant_id, cohort_id, "
                    "person_id, role_in_cohort, status) VALUES (:i"
                    "d, :tenant, :cohort, :person, 'student', 'act"
                    "ive')"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "cohort": cohort,
                    "person": person,
                },
                "23505",
            )
            _enrollment(conn, tenant, cohort, second_person)
            conn.execute(
                text("UPDATE mod_learning.enrollments SET track_id=NULL WHERE id=:id"),
                {"id": enrollment},
            )
            conn.execute(
                text("DELETE FROM mod_learning.cohort_tracks WHERE id=:id"),
                {"id": membership},
            )
            conn.execute(
                text("DELETE FROM mod_learning.courses WHERE id=:id"), {"id": course}
            )
            for table in ("track_courses", "course_offerings", "course_prerequisites"):
                assert (
                    conn.scalar(select(func.count()).select_from(RELATIONS[table])) == 0
                )
            conn.execute(
                text("DELETE FROM mod_learning.cohorts WHERE id=:id"), {"id": cohort}
            )
            assert (
                conn.scalar(text("SELECT count(*) FROM mod_learning.enrollments")) == 0
            )
    finally:
        app.dispose()
        admin.dispose()


def test_actual_migrated_catalog_source_contract_and_identity_translation(
    learning_database,
):
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            assert set(inspector.get_table_names(schema="mod_learning")) == set(
                ALL_TABLES
            )
            total = 0
            for table in TABLES:
                assert not inspector.has_table(table, schema="public")
                columns = {
                    c["name"]: c
                    for c in inspector.get_columns(table, schema="mod_learning")
                }
                total += len(columns)
                assert set(columns) == set(FIELDS[table].split()) | {
                    "id",
                    "tenant_id",
                    "created_at",
                    "updated_at",
                }
                assert {
                    name for name, c in columns.items() if c["nullable"]
                } == NULLABLE.get(table, set())
                assert {
                    tuple(c["column_names"])
                    for c in inspector.get_unique_constraints(
                        table, schema="mod_learning"
                    )
                } == UNIQUES[table]
                assert {
                    tuple(i["column_names"])
                    for i in inspector.get_indexes(table, schema="mod_learning")
                    if not i.get("duplicates_constraint")
                } == INDEXES[table]
                for field, length in LENGTHS.get(table, {}).items():
                    assert columns[field]["type"].length == length
                for field, expected in DEFAULTS.get(table, {}).items():
                    assert (
                        columns[field]["default"].split("::")[0].strip("'") == expected
                    )
                if table == "enrollments":
                    assert columns["role_in_cohort"]["default"] is None
                    assert columns["status"]["default"] is None
                assert columns["id"]["default"] is None
                for field in ("created_at", "updated_at"):
                    assert columns[field]["default"] == "now()"
                    assert columns[field]["type"].timezone
                assert conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM "
                        "pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON "
                        "n.oid=c.relnamespace WHERE n.nspname='mod_learning' AND "
                        "c.relname=:table"
                    ),
                    {"table": table},
                ).one() == (True, True)
                policy = conn.execute(
                    text(
                        "SELECT qual, with_check FROM pg_catalog.pg_policies "
                        "WHERE schemaname='mod_learning' AND tablename=:table"
                    ),
                    {"table": table},
                ).one()
                assert all("app_current_tenant_id()" in p for p in policy)
                checks = {
                    c["name"]
                    for c in inspector.get_check_constraints(
                        table, schema="mod_learning"
                    )
                }
                assert checks == {
                    "enrollments": {
                        "ck_enrollments_audience",
                        "ck_enrollments_staff_has_employee_ref",
                    },
                    "cohorts": {"ck_cohorts_auto_accept_below_action"},
                    "tracks": {"ck_tracks_gating_mode"},
                    "course_prerequisites": {
                        "ck_course_prerequisites_condition_kind",
                        "ck_course_prerequisites_origin",
                    },
                }.get(table, set())
                for role in ("app_user", "platform_api"):
                    for privilege in (
                        "SELECT",
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "TRUNCATE",
                        "REFERENCES",
                        "TRIGGER",
                    ):
                        assert conn.scalar(
                            text(
                                "SELECT pg_catalog.has_table_privilege(CAST(:role"
                                " AS text), CAST(:table AS text), CAST(:privilege"
                                " AS text))"
                            ),
                            {
                                "role": role,
                                "table": f"mod_learning.{table}",
                                "privilege": privilege,
                            },
                        ) == (privilege in {"SELECT", "INSERT", "UPDATE", "DELETE"})
            assert total == 66
            source_edges = {
                "cohorts": set(),
                "tracks": set(),
                "cohort_tracks": {
                    (("tenant_id", "cohort_id"), "cohorts", ("tenant_id", "id")),
                    (("tenant_id", "track_id"), "tracks", ("tenant_id", "id")),
                },
                "track_courses": {
                    (("tenant_id", "track_id"), "tracks", ("tenant_id", "id")),
                    (("tenant_id", "course_id"), "courses", ("tenant_id", "id")),
                },
                "course_offerings": {
                    (("tenant_id", "cohort_id"), "cohorts", ("tenant_id", "id")),
                    (("tenant_id", "course_id"), "courses", ("tenant_id", "id")),
                },
                "course_prerequisites": {
                    (("tenant_id", "course_id"), "courses", ("tenant_id", "id")),
                    (
                        ("tenant_id", "requires_course_id"),
                        "courses",
                        ("tenant_id", "id"),
                    ),
                },
            }
            for table, edges in source_edges.items():
                assert _foreign_keys(conn, table) == {
                    (("tenant_id",), "public", "tenants", ("id",), "CASCADE"),
                    *(
                        (local, "mod_learning", target, remote, "CASCADE")
                        for local, target, remote in edges
                    ),
                }
            assert _foreign_keys(conn, "enrollments") == {
                (("tenant_id",), "public", "tenants", ("id",), "CASCADE"),
                (
                    ("tenant_id", "cohort_id"),
                    "mod_learning",
                    "cohorts",
                    ("tenant_id", "id"),
                    "CASCADE",
                ),
                (
                    ("tenant_id", "cohort_id", "track_id"),
                    "mod_learning",
                    "cohort_tracks",
                    ("tenant_id", "cohort_id", "track_id"),
                    None,
                ),
                (
                    ("tenant_id", "person_id"),
                    "public",
                    "parties",
                    ("tenant_id", "id"),
                    "CASCADE",
                ),
                (("person_id",), "public", "party_persons", ("party_id",), "CASCADE"),
            }
            assert conn.execute(
                text(
                    "SELECT confdeltype, confupdtype, confmatchtype "
                    "FROM pg_catalog.pg_constraint "
                    "WHERE conrelid=CAST(:relation AS pg_catalog.regclass) "
                    "AND conname=:constraint"
                ),
                {
                    "relation": "mod_learning.enrollments",
                    "constraint": "fk_enrollments_tenant_cohort_track",
                },
            ).one() == ("a", "a", "s")
            assert not any(
                "entrance_bank_id" in fk[0] for fk in _foreign_keys(conn, "cohorts")
            )
    finally:
        engine.dispose()


def test_missing_genuine_profile_prerequisite_refuses_before_participation_ddl(
    learning_database,
):
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    from alembic import command
    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = importlib.import_module(
        "dotmac_learning.migrations.versions.lr_0003_participation"
    )
    # Retire descendants through the real chain before testing this revision's
    # own prerequisite refusal. Later assessment tables must not contaminate
    # the fourteen-table observation after the participation downgrade.
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL_VERSIONS} {LEARNING_VERSIONS}")
    command.downgrade(cfg, "lr_0003_participation")
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(text("DROP TABLE public.party_persons CASCADE"))
                with Operations.context(MigrationContext.configure(conn)):
                    revision.downgrade()
                    assert set(
                        inspect(conn).get_table_names(schema="mod_learning")
                    ) == set(BASE_TABLES)
                    with pytest.raises(
                        PrerequisiteNotSatisfiedError, match="party_person"
                    ):
                        revision.upgrade()
                assert set(inspect(conn).get_table_names(schema="mod_learning")) == set(
                    BASE_TABLES
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_participation_downgrade_retains_previous_fourteen_tables(learning_database):
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL_VERSIONS} {LEARNING_VERSIONS}")
    command.downgrade(cfg, "lr_0002_curriculum_reading")
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            assert set(inspector.get_table_names(schema="mod_learning")) == set(
                BASE_TABLES
            )
            for table in ("tenants", "parties", "party_persons"):
                assert inspector.has_table(table, schema="public")
    finally:
        engine.dispose()
