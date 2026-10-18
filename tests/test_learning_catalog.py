"""Hosted PostgreSQL canaries for the first seven canonical learning tables.

The fixture runs the real Kernel and learning lineages as app_admin; actors
connect as app_user. It neither stamps providers nor constructs ORM metadata.
"""

from __future__ import annotations

import importlib
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotmac_kernel.models import Party, PartyPerson
from dotmac_learning.models import TENANT_MODELS
from sqlalchemy import (
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

from tests.test_forms_workflow_isolation import _superuser_url, _url_for

REPO_ROOT = Path(__file__).resolve().parent.parent
KERNEL_VERSIONS = (
    REPO_ROOT / "packages/dotmac-kernel/src/dotmac_kernel/migrations/versions"
)
LEARNING_VERSIONS = (
    REPO_ROOT / "packages/dotmac-learning/src/dotmac_learning/migrations/versions"
)
_TABLE_RELATIONS = {model.__table__.name: model.__table__ for model in TENANT_MODELS}
TABLES = (
    "courses",
    "activities",
    "submissions",
    "scores",
    "course_completions",
    "certificates",
    "learning_events",
)
ALL_TABLES = (
    *TABLES,
    "chapters",
    "question_banks",
    "questions",
    "chapter_reads",
    "subtopic_reads",
    "chapter_bookmarks",
    "chapter_notes",
    "cohorts",
    "enrollments",
    "tracks",
    "track_courses",
    "cohort_tracks",
    "course_offerings",
    "course_prerequisites",
    "activity_attempts",
    "attempt_grants",
    "offering_activities",
    "class_sessions",
    "announcements",
    "success_queue_entries",
)


@pytest.fixture
def learning_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, str]]:
    from alembic import command
    from alembic.config import Config

    superuser = _superuser_url()
    name = f"learning_catalog_{uuid.uuid4().hex[:12]}"
    server = create_engine(superuser, isolation_level="AUTOCOMMIT")
    with server.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin_url = _url_for(superuser, name, user="app_admin")
    try:
        setup = create_engine(_url_for(superuser, name), isolation_level="AUTOCOMMIT")
        try:
            with setup.connect() as conn:
                conn.execute(text("ALTER SCHEMA public OWNER TO app_admin"))
                conn.execute(text(f'GRANT CREATE ON DATABASE "{name}" TO app_admin'))
                conn.execute(
                    text(
                        f'GRANT CONNECT ON DATABASE "{name}" TO app_user, platform_api'
                    )
                )
        finally:
            setup.dispose()
        cfg = Config(str(REPO_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
        cfg.set_main_option(
            "version_locations", f"{KERNEL_VERSIONS} {LEARNING_VERSIONS}"
        )
        monkeypatch.setenv("MIGRATION_DATABASE_URL", admin_url)
        command.upgrade(cfg, "kernel@head")
        check = create_engine(admin_url)
        try:
            with check.connect() as conn:
                assert not inspect(conn).has_schema("mod_learning")
        finally:
            check.dispose()
        command.upgrade(cfg, "learning@head")
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
            conn.execute(text(f'DROP DATABASE "{name}"'))
        server.dispose()


def _tenant(conn: Connection, tenant: uuid.UUID) -> None:
    conn.execute(
        text(
            "INSERT INTO public.tenants (id, slug, name) VALUES (:id, :slug, "
            "'Learning')"
        ),
        {"id": tenant, "slug": tenant.hex},
    )


def _person(
    conn: Connection,
    tenant: uuid.UUID,
    *,
    subtype: bool = True,
    party_type: str = "person",
) -> uuid.UUID:
    person = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO public.parties (id, tenant_id, party_type, display_name) "
            "VALUES (:id, :tenant, :kind, 'Learner')"
        ),
        {"id": person, "tenant": tenant, "kind": party_type},
    )
    if subtype:
        conn.execute(
            text(
                "INSERT INTO public.party_persons (party_id, first_name, last_name) "
                "VALUES (:id, 'Learning', 'Person')"
            ),
            {"id": person},
        )
    return person


def _scope(conn: Connection, tenant: uuid.UUID) -> None:
    conn.execute(
        text("SELECT set_config('app.current_tenant', :tenant, true)"),
        {"tenant": str(tenant)},
    )


def _course(conn: Connection, tenant: uuid.UUID) -> uuid.UUID:
    course = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_learning.courses "
            "(id, tenant_id, slug, title, discipline, source_ref) "
            "VALUES (:id, :tenant, :slug, 'Course', 'engineering', 'fixture')"
        ),
        {"id": course, "tenant": tenant, "slug": course.hex},
    )
    return course


def _event(
    conn: Connection,
    tenant: uuid.UUID,
    person: uuid.UUID,
    *,
    kind: str = "course_viewed",
    subject: uuid.UUID | None = None,
) -> uuid.UUID:
    event = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_learning.learning_events "
            "(id, tenant_id, person_id, kind, subject_id, occurred_at) "
            "VALUES (:id, :tenant, :person, :kind, :subject, now())"
        ),
        {
            "id": event,
            "tenant": tenant,
            "person": person,
            "kind": kind,
            "subject": subject,
        },
    )
    return event


def _refused(conn: Connection, sql: str, params: dict, state: str) -> None:
    with pytest.raises(DBAPIError) as caught, conn.begin_nested():
        conn.execute(text(sql), params)
    assert getattr(caught.value.orig, "sqlstate", None) == state


def test_restricted_actor_isolation_and_cross_tenant_references(
    learning_database,
) -> None:
    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant_a)
            _tenant(conn, tenant_b)
            person_a = _person(conn, tenant_a)
            person_b = _person(conn, tenant_b)
        with app.begin() as conn:
            _scope(conn, tenant_a)
            assert conn.scalar(text("SELECT current_user")) == "app_user"
            course_a = _course(conn, tenant_a)
            activity = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_learning.activities "
                    "(id, tenant_id, course_id, title) VALUES (:id, :tenant, "
                    ":course, 'Activity')"
                ),
                {"id": activity, "tenant": tenant_a, "course": course_a},
            )
            submission_a = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_learning.submissions (id, tenant_id, "
                    "activity_id, person_id) "
                    "VALUES (:id, :tenant, :activity, :person)"
                ),
                {
                    "id": submission_a,
                    "tenant": tenant_a,
                    "activity": activity,
                    "person": person_a,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_learning.scores (id, tenant_id, submission_id, "
                    "score, max_score, fraction, passed) "
                    "VALUES (:id, :tenant, :submission, 1, 1, 1, true)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant_a, "submission": submission_a},
            )
            conn.execute(
                text(
                    "INSERT INTO mod_learning.course_completions (id, tenant_id, "
                    "person_id, course_id) "
                    "VALUES (:id, :tenant, :person, :course)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_a,
                    "person": person_a,
                    "course": course_a,
                },
            )
            conn.execute(
                text(
                    "INSERT INTO mod_learning.certificates (id, tenant_id, "
                    "person_id, course_id, serial, issued_at) "
                    "VALUES (:id, :tenant, :person, :course, 'CERT-A', now())"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_a,
                    "person": person_a,
                    "course": course_a,
                },
            )
            _event(conn, tenant_a, person_a)
            for table in TABLES:
                assert (
                    conn.scalar(
                        select(func.count()).select_from(_TABLE_RELATIONS[table])
                    )
                    == 1
                )
        with app.begin() as conn:
            _scope(conn, tenant_b)
            for table in TABLES:
                assert (
                    conn.scalar(
                        select(func.count()).select_from(_TABLE_RELATIONS[table])
                    )
                    == 0
                )
            _refused(
                conn,
                "INSERT INTO mod_learning.submissions (id, tenant_id, activity_id, "
                "person_id) "
                "VALUES (:id, :tenant, :activity, :person)",
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_b,
                    "activity": activity,
                    "person": person_b,
                },
                "23503",
            )
            _refused(
                conn,
                "INSERT INTO mod_learning.course_completions (id, tenant_id, "
                "course_id, person_id) "
                "VALUES (:id, :tenant, :course, :person)",
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_b,
                    "course": course_a,
                    "person": person_b,
                },
                "23503",
            )
            _refused(
                conn,
                "INSERT INTO mod_learning.scores (id, tenant_id, submission_id, "
                "score, max_score, fraction, passed) "
                "VALUES (:id, :tenant, :submission, 1, 1, 1, true)",
                {"id": uuid.uuid4(), "tenant": tenant_b, "submission": submission_a},
                "23503",
            )
            _refused(
                conn,
                "INSERT INTO mod_learning.certificates (id, tenant_id, person_id, "
                "course_id, serial, issued_at) "
                "VALUES (:id, :tenant, :person, :course, 'CROSS', now())",
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant_b,
                    "person": person_b,
                    "course": course_a,
                },
                "23503",
            )
            _refused(
                conn,
                "INSERT INTO mod_learning.learning_events "
                "(id, tenant_id, person_id, kind, occurred_at) VALUES (:id, :tenant, "
                ":person, 'view', now())",
                {"id": uuid.uuid4(), "tenant": tenant_b, "person": person_a},
                "23503",
            )
            assert (
                conn.execute(
                    text(
                        "UPDATE mod_learning.courses SET title='cross' WHERE id=:id "
                        "RETURNING id"
                    ),
                    {"id": course_a},
                ).all()
                == []
            )
            _refused(
                conn,
                "INSERT INTO mod_learning.courses "
                "(id, tenant_id, slug, title, discipline, source_ref) VALUES (:id, "
                ":tenant, 'bad', 'Bad', 'x', 'x')",
                {"id": uuid.uuid4(), "tenant": tenant_a},
                "42501",
            )
        with app.begin() as conn:
            for table in TABLES:
                assert (
                    conn.scalar(
                        select(func.count()).select_from(_TABLE_RELATIONS[table])
                    )
                    == 0
                )
    finally:
        app.dispose()
        admin.dispose()


def test_events_require_real_person_subtype_and_are_append_only(
    learning_database,
) -> None:
    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    tenant = uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
            root_only = _person(conn, tenant, subtype=False)
            organization = _person(
                conn, tenant, subtype=False, party_type="organization"
            )
        with app.begin() as conn:
            _scope(conn, tenant)
            event = _event(conn, tenant, person)
            for bad_person in (root_only, organization, uuid.uuid4()):
                with pytest.raises(DBAPIError) as caught, conn.begin_nested():
                    _event(conn, tenant, bad_person)
                assert getattr(caught.value.orig, "sqlstate", None) == "23503"
            for sql in (
                "UPDATE mod_learning.learning_events SET kind = 'changed' WHERE id = "
                ":id",
                "DELETE FROM mod_learning.learning_events WHERE id = :id",
            ):
                _refused(conn, sql, {"id": event}, "42501")
            subject = uuid.uuid4()
            _event(conn, tenant, person, kind="chapter_completed", subject=subject)
            with pytest.raises(DBAPIError) as caught, conn.begin_nested():
                _event(conn, tenant, person, kind="chapter_completed", subject=subject)
            assert getattr(caught.value.orig, "sqlstate", None) == "23505"
            # Views are intentionally repeatable; nullable subjects retain
            # PostgreSQL NULL semantics.
            _event(conn, tenant, person, subject=subject)
            _event(conn, tenant, person, subject=subject)
            _event(conn, tenant, person, kind="chapter_completed")
            _event(conn, tenant, person, kind="chapter_completed")
    finally:
        app.dispose()
        admin.dispose()


@pytest.mark.parametrize("delete_first", ["party_persons", "parties"])
def test_both_real_identity_delete_paths_cascade_events(
    learning_database, delete_first
) -> None:
    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    tenant = uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
            person = _person(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            _event(conn, tenant, person)
        with admin.begin() as conn:
            relation, key = {
                "party_persons": (
                    PartyPerson.__table__,
                    PartyPerson.__table__.c.party_id,
                ),
                "parties": (Party.__table__, Party.__table__.c.id),
            }[delete_first]
            conn.execute(
                relation.delete().where(key == person),
            )
            assert (
                conn.scalar(text("SELECT count(*) FROM mod_learning.learning_events"))
                == 0
            )
            if delete_first == "party_persons":
                assert (
                    conn.scalar(
                        text("SELECT count(*) FROM public.parties WHERE id = :id"),
                        {"id": person},
                    )
                    == 1
                )
    finally:
        app.dispose()
        admin.dispose()


def test_fresh_catalog_constraints_defaults_indexes_and_grants(
    learning_database,
) -> None:
    admin_url, app_url = learning_database
    admin, app = create_engine(admin_url), create_engine(app_url)
    try:
        with admin.connect() as conn:
            inspector = inspect(conn)
            assert set(inspector.get_table_names(schema="mod_learning")) == set(
                ALL_TABLES
            )
            assert not inspector.has_table("courses", schema="public")
            for table in TABLES:
                columns = {
                    c["name"]: c
                    for c in inspector.get_columns(table, schema="mod_learning")
                }
                assert columns["tenant_id"]["nullable"] is False
                nullable = {
                    "courses": set(),
                    "activities": {
                        "chapter_number",
                        "bank_id",
                        "max_attempts",
                        "question_count",
                    },
                    "submissions": set(),
                    "scores": {"override_reason"},
                    "course_completions": {"completed_at"},
                    "certificates": set(),
                    "learning_events": {"course_id", "subject_id"},
                }
                assert {
                    name for name, c in columns.items() if c["nullable"]
                } == nullable[table]
                uniques = {
                    "courses": {("tenant_id", "id"), ("tenant_id", "slug")},
                    "activities": {("tenant_id", "id")},
                    "submissions": {
                        ("tenant_id", "id"),
                        ("tenant_id", "activity_id", "person_id", "attempt_no"),
                    },
                    "scores": set(),
                    "course_completions": {
                        ("tenant_id", "id"),
                        ("tenant_id", "person_id", "course_id"),
                    },
                    "certificates": {
                        ("tenant_id", "id"),
                        ("tenant_id", "person_id", "course_id"),
                        ("tenant_id", "serial"),
                    },
                    "learning_events": set(),
                }
                assert {
                    tuple(c["column_names"])
                    for c in inspector.get_unique_constraints(
                        table, schema="mod_learning"
                    )
                } == uniques[table]
                indexed = {
                    "courses": ("tenant_id",),
                    "activities": ("tenant_id", "course_id", "bank_id"),
                    "submissions": ("tenant_id", "activity_id", "person_id"),
                    "scores": ("tenant_id", "submission_id"),
                    "course_completions": ("tenant_id", "person_id", "course_id"),
                    "certificates": ("tenant_id", "person_id", "course_id"),
                    "learning_events": ("tenant_id", "person_id"),
                }
                actual_indexes = {
                    i["name"]: i["column_names"]
                    for i in inspector.get_indexes(table, schema="mod_learning")
                }
                for column in indexed[table]:
                    assert actual_indexes[f"ix_{table}_{column}"] == [column]
                assert columns["id"]["default"] is None
                assert columns["created_at"]["default"] is not None
                assert columns["updated_at"]["default"] is not None
                assert conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid=c.relnamespace "
                        "WHERE n.nspname='mod_learning' AND c.relname=:table"
                    ),
                    {"table": table},
                ).one() == (True, True)
                policy = conn.execute(
                    text(
                        "SELECT qual, with_check FROM pg_policies WHERE "
                        "schemaname='mod_learning' AND tablename=:table"
                    ),
                    {"table": table},
                ).one()
                assert all(
                    "app_current_tenant_id()" in predicate for predicate in policy
                )
                for role in ("app_user", "platform_api"):
                    assert conn.scalar(
                        text(
                            "SELECT has_schema_privilege(CAST(:role AS text), "
                            "'mod_learning', 'USAGE')"
                        ),
                        {"role": role},
                    )
                    for privilege in (
                        "SELECT",
                        "INSERT",
                        "UPDATE",
                        "DELETE",
                        "TRUNCATE",
                        "REFERENCES",
                        "TRIGGER",
                    ):
                        held = conn.scalar(
                            text(
                                "SELECT has_table_privilege(CAST(:role AS text), "
                                "CAST(:table AS text), CAST(:privilege AS text))"
                            ),
                            {
                                "role": role,
                                "table": f"mod_learning.{table}",
                                "privilege": privilege,
                            },
                        )
                        assert held == (
                            privilege
                            in (
                                ("SELECT", "INSERT")
                                if table == "learning_events"
                                else ("SELECT", "INSERT", "UPDATE", "DELETE")
                            )
                        )
            event_fks = inspector.get_foreign_keys(
                "learning_events", schema="mod_learning"
            )
            targets = {
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_schema"],
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk["options"].get("ondelete"),
                )
                for fk in event_fks
            }
            assert (
                ("tenant_id", "person_id"),
                "public",
                "parties",
                ("tenant_id", "id"),
                "CASCADE",
            ) in targets
            assert (
                ("person_id",),
                "public",
                "party_persons",
                ("party_id",),
                "CASCADE",
            ) in targets
            for table, plain_refs in (
                ("activities", {"course_id", "bank_id"}),
                ("submissions", {"person_id"}),
                ("course_completions", {"person_id"}),
                ("certificates", {"person_id"}),
                ("learning_events", {"course_id", "subject_id"}),
            ):
                assert not any(
                    plain_refs.intersection(fk["constrained_columns"])
                    for fk in inspector.get_foreign_keys(table, schema="mod_learning")
                )
            completion_columns = {
                c["name"]
                for c in inspector.get_columns(
                    "course_completions", schema="mod_learning"
                )
            }
            assert "erp_synced_at" not in completion_columns
            index = next(
                i
                for i in inspector.get_indexes("learning_events", schema="mod_learning")
                if i["name"] == "uq_learning_events_once_per_subject"
            )
            assert index["unique"]
            assert index["column_names"] == [
                "tenant_id",
                "person_id",
                "kind",
                "subject_id",
            ]
            assert "chapter_completed" in str(
                index["dialect_options"]["postgresql_where"]
            )
            assert "certificate_earned" in str(
                index["dialect_options"]["postgresql_where"]
            )
        tenant, person = uuid.uuid4(), uuid.uuid4()
        with admin.begin() as conn:
            _tenant(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            course = _course(conn, tenant)
            assert conn.execute(
                text("SELECT version, status, listed FROM mod_learning.courses")
            ).one() == (1, "published", False)
            activity, submission = uuid.uuid4(), uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO mod_learning.activities (id, tenant_id, course_id, "
                    "title) "
                    "VALUES (:id, :tenant, :course, 'Activity')"
                ),
                {"id": activity, "tenant": tenant, "course": course},
            )
            assert conn.execute(
                text(
                    "SELECT type, pass_threshold, max_attempts, grading, weight, "
                    "question_count, assessment_mode "
                    "FROM mod_learning.activities"
                )
            ).one() == ("mcq_test", 0.0, None, "auto", 1.0, None, "graded")
            conn.execute(
                text(
                    "INSERT INTO mod_learning.submissions (id, tenant_id, "
                    "activity_id, person_id) "
                    "VALUES (:id, :tenant, :activity, :person)"
                ),
                {
                    "id": submission,
                    "tenant": tenant,
                    "activity": activity,
                    "person": person,
                },
            )
            assert conn.execute(
                text("SELECT answers, attempt_no FROM mod_learning.submissions")
            ).one() == ({}, 1)
            conn.execute(
                text(
                    "INSERT INTO mod_learning.scores (id, tenant_id, submission_id, "
                    "score, max_score, fraction, passed) "
                    "VALUES (:id, :tenant, :submission, 1, 1, 1, true)"
                ),
                {"id": uuid.uuid4(), "tenant": tenant, "submission": submission},
            )
            assert conn.execute(
                text(
                    "SELECT per_item, source, override_reason FROM mod_learning.scores"
                )
            ).one() == ([], "auto", None)
            conn.execute(
                text(
                    "INSERT INTO mod_learning.course_completions (id, tenant_id, "
                    "person_id, course_id) "
                    "VALUES (:id, :tenant, :person, :course)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "person": person,
                    "course": course,
                },
            )
            assert conn.execute(
                text(
                    "SELECT status, pct, completed_at FROM "
                    "mod_learning.course_completions"
                )
            ).one() == ("in_progress", 0.0, None)
            _refused(
                conn, "UPDATE mod_learning.courses SET tenant_id=NULL", {}, "23502"
            )
            _refused(
                conn,
                "INSERT INTO mod_learning.submissions (id, tenant_id, activity_id, "
                "person_id) "
                "VALUES (:id, :tenant, :activity, :person)",
                {
                    "id": uuid.uuid4(),
                    "tenant": tenant,
                    "activity": activity,
                    "person": person,
                },
                "23505",
            )
            conn.execute(
                text("DELETE FROM mod_learning.activities WHERE id=:id"),
                {"id": activity},
            )
            assert (
                conn.scalar(text("SELECT count(*) FROM mod_learning.submissions")) == 0
            )
            assert conn.scalar(text("SELECT count(*) FROM mod_learning.scores")) == 0
    finally:
        app.dispose()
        admin.dispose()


def test_missing_real_identity_catalog_is_refused_before_ddl(learning_database) -> None:
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = importlib.import_module(
        "dotmac_learning.migrations.versions.lr_0001_learning"
    )
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                # Damage a real migrated provider, then prove the migration's
                # first action refuses it. Without the check, CREATE SCHEMA
                # would instead fail on the already-present canonical schema.
                conn.execute(text("DROP TABLE public.party_persons CASCADE"))
                with Operations.context(MigrationContext.configure(conn)):
                    with pytest.raises(
                        PrerequisiteNotSatisfiedError, match="party_person"
                    ):
                        revision.upgrade()
                assert set(inspect(conn).get_table_names(schema="mod_learning")) == set(
                    ALL_TABLES
                )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_downgrade_removes_only_learning_canonical_storage(learning_database) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL_VERSIONS} {LEARNING_VERSIONS}")
    command.downgrade(cfg, "learning@base")
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            assert not inspector.has_schema("mod_learning")
            assert inspector.has_table("tenants", schema="public")
            assert inspector.has_table("parties", schema="public")
            assert inspector.has_table("party_persons", schema="public")
    finally:
        engine.dispose()
