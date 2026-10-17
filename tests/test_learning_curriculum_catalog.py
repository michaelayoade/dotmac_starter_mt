"""Hosted canaries for source-derived curriculum/reading storage.

Uses the real migrated Kernel + learning fixture, never metadata.create_all.
Academy 096 has SIMPLE chapter FKs for reads/bookmarks/notes. RLS isolates
rows by their own tenant; these source FKs do not prove tenant alignment.
"""

from __future__ import annotations

import importlib
import uuid

import pytest
from dotmac_learning.curriculum_models import CURRICULUM_MODELS
from sqlalchemy import (
    bindparam,
    column,
    create_engine,
    func,
    inspect,
    select,
    table,
    text,
)
from sqlalchemy.exc import DBAPIError

from tests import test_learning_catalog as learning_catalog_fixtures
from tests.test_learning_catalog import (
    TABLES as FIRST_TABLES,
)
from tests.test_learning_catalog import (
    _course,
    _refused,
    _scope,
    _tenant,
)

learning_database = learning_catalog_fixtures.learning_database

TABLES = (
    "chapters",
    "question_banks",
    "questions",
    "chapter_reads",
    "subtopic_reads",
    "chapter_bookmarks",
    "chapter_notes",
)
# Closed source-owned relations without ORM client defaults: database defaults
# remain under test, and callers cannot supply arbitrary relation identifiers.
RELATIONS = {
    model.__tablename__: table(
        model.__tablename__,
        *(column(name) for name in model.__table__.c.keys()),
        schema="mod_learning",
    )
    for model in CURRICULUM_MODELS
}
FK_ORACLE = {
    "chapters": {
        (("tenant_id", "course_id"), "mod_learning", "courses", ("tenant_id", "id"))
    },
    "question_banks": set(),
    "questions": {
        (
            ("tenant_id", "bank_id"),
            "mod_learning",
            "question_banks",
            ("tenant_id", "id"),
        )
    },
    "chapter_reads": {(("chapter_id",), "mod_learning", "chapters", ("id",))},
    "subtopic_reads": {(("chapter_id",), "mod_learning", "chapters", ("id",))},
    "chapter_bookmarks": {
        (("course_id",), "mod_learning", "courses", ("id",)),
        (("chapter_id",), "mod_learning", "chapters", ("id",)),
    },
    "chapter_notes": {
        (("course_id",), "mod_learning", "courses", ("id",)),
        (("chapter_id",), "mod_learning", "chapters", ("id",)),
    },
}
UNIQUE_ORACLE = {
    "chapters": {("tenant_id", "course_id", "number")},
    "question_banks": {("tenant_id", "id")},
    "questions": {("tenant_id", "bank_id", "ext_id")},
    "chapter_reads": {("tenant_id", "person_id", "chapter_id")},
    "subtopic_reads": {("tenant_id", "person_id", "chapter_id", "subtopic_slug")},
    "chapter_bookmarks": {("tenant_id", "person_id", "chapter_id")},
    "chapter_notes": {("tenant_id", "person_id", "chapter_id")},
}
INDEX_ORACLE = {
    "chapters": {"tenant_id", "course_id"},
    "question_banks": {"tenant_id", "course_id"},
    "questions": {"tenant_id", "bank_id"},
    "chapter_reads": {"tenant_id", "person_id", "chapter_id"},
    "subtopic_reads": {"tenant_id", "person_id", "chapter_id"},
    "chapter_bookmarks": {"tenant_id", "person_id", "course_id", "chapter_id"},
    "chapter_notes": {"tenant_id", "person_id", "course_id", "chapter_id"},
}
# PostgreSQL format_type, nullability, default expression. Identity/timestamps
# are checked separately; these are literal source DDL values, not ORM output.
COLUMN_ORACLE = {
    "chapters": {
        "course_id": ("uuid", False, None),
        "number": ("integer", False, None),
        "title": ("character varying(200)", False, None),
        "part": ("character varying(20)", False, "''::character varying"),
        "body_html": ("text", False, "''::text"),
        "body_md": ("text", False, "''::text"),
        "source_hash": ("character varying(64)", False, "''::character varying"),
        "order_index": ("integer", False, "0"),
    },
    "question_banks": {
        "course_id": ("uuid", False, None),
        "chapter_number": ("integer", True, None),
        "kind": ("character varying(10)", False, None),
        "version": ("integer", False, "1"),
    },
    "questions": {
        "bank_id": ("uuid", False, None),
        "ext_id": ("character varying(60)", False, None),
        "stem": ("text", False, None),
        "type": ("character varying(12)", False, None),
        "options": ("jsonb", False, "'[]'::jsonb"),
        "correct": ("jsonb", False, "'[]'::jsonb"),
        "rubric_category": ("character varying(12)", False, None),
        "category": ("character varying(40)", True, None),
        "explanation": ("text", False, "''::text"),
        "weight": ("integer", False, "1"),
    },
    "chapter_reads": {
        "person_id": ("uuid", False, None),
        "chapter_id": ("uuid", False, None),
    },
    "subtopic_reads": {
        "person_id": ("uuid", False, None),
        "chapter_id": ("uuid", False, None),
        "subtopic_slug": ("character varying(64)", False, None),
    },
    "chapter_bookmarks": {
        "person_id": ("uuid", False, None),
        "course_id": ("uuid", False, None),
        "chapter_id": ("uuid", False, None),
    },
    "chapter_notes": {
        "person_id": ("uuid", False, None),
        "course_id": ("uuid", False, None),
        "chapter_id": ("uuid", False, None),
        "body": ("text", False, "''::text"),
    },
}


def _chapter(conn, tenant, course, number=1):
    chapter = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_learning.chapters (id, tenant_id, "
            "course_id, number, title) VALUES (:id, :tenant, "
            ":course, :number, 'Chapter')"
        ),
        {"id": chapter, "tenant": tenant, "course": course, "number": number},
    )
    return chapter


def _bank(conn, tenant, course):
    bank = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_learning.question_banks (id, "
            "tenant_id, course_id, kind) VALUES (:id, :tenant, "
            ":course, 'chapter')"
        ),
        {"id": bank, "tenant": tenant, "course": course},
    )
    return bank


def _question(conn, tenant, bank, ext="Q1"):
    question = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO mod_learning.questions (id, tenant_id, "
            "bank_id, ext_id, stem, type, rubric_category) VALUES "
            "(:id, :tenant, :bank, :ext, 'Why?', 'single', 'recall')"
        ),
        {"id": question, "tenant": tenant, "bank": bank, "ext": ext},
    )
    return question


def _observation_insert(table_name):
    relation = RELATIONS[table_name]
    values = {
        "id": bindparam("id"),
        "tenant_id": bindparam("tenant"),
        "person_id": bindparam("person"),
        "chapter_id": bindparam("chapter"),
    }
    if table_name in ("chapter_bookmarks", "chapter_notes"):
        values["course_id"] = bindparam("course")
    if table_name == "subtopic_reads":
        values["subtopic_slug"] = "heading"
    return relation.insert().values(**values)


def _statement_refused(conn, statement, params, state):
    with pytest.raises(DBAPIError) as caught, conn.begin_nested():
        conn.execute(statement, params)
    assert getattr(caught.value.orig, "sqlstate", None) == state


def test_curriculum_real_catalog_matches_immutable_source(learning_database):
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            assert set(inspector.get_table_names(schema="mod_learning")) == set(
                FIRST_TABLES
            ) | set(TABLES)
            for table in TABLES:
                assert not inspector.has_table(table, schema="public")
                fks = inspector.get_foreign_keys(table, schema="mod_learning")
                assert {
                    (
                        tuple(fk["constrained_columns"]),
                        fk["referred_schema"],
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                    )
                    for fk in fks
                } == FK_ORACLE[table] | {(("tenant_id",), "public", "tenants", ("id",))}
                assert all(fk["options"]["ondelete"] == "CASCADE" for fk in fks)
                assert {
                    tuple(u["column_names"])
                    for u in inspector.get_unique_constraints(
                        table, schema="mod_learning"
                    )
                } == UNIQUE_ORACLE[table]
                assert {
                    (i["name"], tuple(i["column_names"]))
                    for i in inspector.get_indexes(table, schema="mod_learning")
                    if not i.get("duplicates_constraint")
                } == {(f"ix_{table}_{c}", (c,)) for c in INDEX_ORACLE[table]}
                columns = conn.execute(
                    text(
                        "SELECT a.attname, pg_catalog.format_type(a.atttypid, "
                        "a.atttypmod), NOT a.attnotnull, "
                        "pg_catalog.pg_get_expr(d.adbin, "
                        "d.adrelid) FROM pg_catalog.pg_attribute a "
                        "LEFT JOIN pg_catalog.pg_attrdef d "
                        "ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE "
                        "a.attrelid=CAST(:table AS regclass) AND a.attnum>0 AND "
                        "NOT a.attisdropped"
                    ),
                    {"table": f"mod_learning.{table}"},
                ).all()
                expected = COLUMN_ORACLE[table] | {
                    "id": ("uuid", False, None),
                    "tenant_id": ("uuid", False, None),
                    "created_at": ("timestamp with time zone", False, "now()"),
                    "updated_at": ("timestamp with time zone", False, "now()"),
                }
                assert {
                    name: (kind, nullable, default)
                    for name, kind, nullable, default in columns
                } == expected
                assert conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM "
                        "pg_catalog.pg_class WHERE oid=CAST(:table AS regclass)"
                    ),
                    {"table": f"mod_learning.{table}"},
                ).one() == (True, True)
                policy = conn.execute(
                    text(
                        "SELECT policyname, roles, cmd, qual, with_check FROM "
                        "pg_catalog.pg_policies "
                        "WHERE schemaname='mod_learning' AND "
                        "tablename=:table"
                    ),
                    {"table": table},
                ).one()
                assert policy.policyname == f"{table}_tenant_isolation"
                assert policy.roles == ["public"] and policy.cmd == "ALL"
                for expr in (policy.qual, policy.with_check):
                    assert "tenant_id" in expr and "app_current_tenant_id()" in expr
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
                        expected_grant = privilege in ("SELECT", "INSERT") or (
                            table != "subtopic_reads"
                            and privilege in ("UPDATE", "DELETE")
                        )
                        assert (
                            conn.scalar(
                                text(
                                    "SELECT pg_catalog.has_table_privilege"
                                    "(:role, :table, :privilege)"
                                ),
                                {
                                    "role": role,
                                    "table": f"mod_learning.{table}",
                                    "privilege": privilege,
                                },
                            )
                            == expected_grant
                        )
    finally:
        engine.dispose()


def test_curriculum_two_tenant_rows_and_source_reference_semantics(learning_database):
    admin, app = (
        create_engine(learning_database[0]),
        create_engine(learning_database[1]),
    )
    a, b, plain_person = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, a)
            _tenant(conn, b)
        with app.begin() as conn:
            _scope(conn, a)
            course = _course(conn, a)
            chapter = _chapter(conn, a, course)
            bank = _bank(
                conn, a, uuid.uuid4()
            )  # Source course reference is logical only.
            _question(conn, a, bank)
            for table in TABLES[3:]:
                conn.execute(
                    _observation_insert(table),
                    {
                        "id": uuid.uuid4(),
                        "tenant": a,
                        "person": plain_person,
                        "chapter": chapter,
                        "course": course,
                    },
                )
            for table in TABLES:
                assert (
                    conn.scalar(select(func.count()).select_from(RELATIONS[table])) == 1
                )
        with app.begin() as conn:
            _scope(conn, b)
            for table in TABLES:
                assert (
                    conn.scalar(select(func.count()).select_from(RELATIONS[table])) == 0
                )
            _refused(
                conn,
                (
                    "INSERT INTO mod_learning.chapters (id, tenant_id, "
                    "course_id, number, title) VALUES (:id, :tenant, "
                    ":course, 2, 'Cross')"
                ),
                {"id": uuid.uuid4(), "tenant": b, "course": course},
                "23503",
            )
            _refused(
                conn,
                (
                    "INSERT INTO mod_learning.questions (id, tenant_id, "
                    "bank_id, ext_id, stem, type, rubric_category) VALUES "
                    "(:id, :tenant, :bank, 'X', 'x', 'single', 'recall')"
                ),
                {"id": uuid.uuid4(), "tenant": b, "bank": bank},
                "23503",
            )
            # SIMPLE source FKs accept existing IDs regardless of parent tenancy.
            # This deliberately characterizes the historical limitation.
            for table in TABLES[3:]:
                params = {
                    "id": uuid.uuid4(),
                    "tenant": b,
                    "person": plain_person,
                    "chapter": chapter,
                    "course": course,
                }
                conn.execute(_observation_insert(table), params)
                _statement_refused(
                    conn,
                    _observation_insert(table),
                    params | {"id": uuid.uuid4()},
                    "23505",
                )
                _statement_refused(
                    conn,
                    _observation_insert(table),
                    params | {"id": uuid.uuid4(), "chapter": uuid.uuid4()},
                    "23503",
                )
                _statement_refused(
                    conn,
                    _observation_insert(table),
                    params | {"id": uuid.uuid4(), "tenant": a},
                    "42501",
                )
            for table in TABLES:
                if table != "subtopic_reads":
                    assert (
                        conn.execute(
                            RELATIONS[table]
                            .delete()
                            .where(RELATIONS[table].c.tenant_id == bindparam("tenant"))
                            .returning(RELATIONS[table].c.id),
                            {"tenant": a},
                        ).all()
                        == []
                    )
        with app.begin() as conn:
            for table in TABLES:
                assert (
                    conn.scalar(select(func.count()).select_from(RELATIONS[table])) == 0
                )
            _refused(
                conn,
                (
                    "INSERT INTO mod_learning.question_banks (id, "
                    "tenant_id, course_id, kind) VALUES (:id, :tenant, "
                    ":course, 'chapter')"
                ),
                {"id": uuid.uuid4(), "tenant": a, "course": uuid.uuid4()},
                "42501",
            )
    finally:
        app.dispose()
        admin.dispose()


def test_defaults_json_shape_uniqueness_cascades_and_subtopic_grants(learning_database):
    admin, app = (
        create_engine(learning_database[0]),
        create_engine(learning_database[1]),
    )
    tenant, person = uuid.uuid4(), uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            course = _course(conn, tenant)
            chapter = _chapter(conn, tenant, course)
            bank = _bank(conn, tenant, course)
            question = _question(conn, tenant, bank)
            assert conn.execute(
                text(
                    "SELECT part, body_html, body_md, source_hash, "
                    "order_index FROM mod_learning.chapters"
                )
            ).one() == ("", "", "", "", 0)
            assert conn.execute(
                text("SELECT chapter_number, version FROM mod_learning.question_banks")
            ).one() == (None, 1)
            assert conn.execute(
                text(
                    "SELECT options, correct, category, explanation, weight "
                    "FROM mod_learning.questions"
                )
            ).one() == ([], [], None, "", 1)
            # Preserve heterogeneous historical JSONB lists, without DB shape CHECKs.
            conn.execute(
                text(
                    "UPDATE mod_learning.questions SET "
                    'options=\'[{"id":"A","text":"yes"},"legacy"]\'::jsonb, '
                    "correct='[\"A\",1]'::jsonb WHERE id=:id"
                ),
                {"id": question},
            )
            assert conn.execute(
                text("SELECT options, correct FROM mod_learning.questions")
            ).one() == ([{"id": "A", "text": "yes"}, "legacy"], ["A", 1])
            _refused(
                conn,
                (
                    "INSERT INTO mod_learning.chapters (id, tenant_id, "
                    "course_id, number, title) VALUES (:id, :tenant, "
                    ":course, 1, 'Duplicate')"
                ),
                {"id": uuid.uuid4(), "tenant": tenant, "course": course},
                "23505",
            )
            _refused(
                conn,
                (
                    "INSERT INTO mod_learning.questions (id, tenant_id, "
                    "bank_id, ext_id, stem, type, rubric_category) VALUES "
                    "(:id, :tenant, :bank, 'Q1', 'x', 'single', 'recall')"
                ),
                {"id": uuid.uuid4(), "tenant": tenant, "bank": bank},
                "23505",
            )
            for table in TABLES[3:]:
                conn.execute(
                    _observation_insert(table),
                    {
                        "id": uuid.uuid4(),
                        "tenant": tenant,
                        "person": person,
                        "chapter": chapter,
                        "course": course,
                    },
                )
            assert (
                conn.scalar(text("SELECT body FROM mod_learning.chapter_notes")) == ""
            )
            for sql in (
                "UPDATE mod_learning.subtopic_reads SET subtopic_slug='changed'",
                "DELETE FROM mod_learning.subtopic_reads",
            ):
                _refused(conn, sql, {}, "42501")
            for table in ("chapter_reads", "chapter_bookmarks", "chapter_notes"):
                conn.execute(
                    RELATIONS[table].update().values(person_id=bindparam("person")),
                    {"person": uuid.uuid4()},
                )
            conn.execute(
                text("DELETE FROM mod_learning.chapters WHERE id=:id"), {"id": chapter}
            )
            for table in TABLES[3:]:
                assert (
                    conn.scalar(select(func.count()).select_from(RELATIONS[table])) == 0
                )
            # Banks have no course FK, and are not cascaded by course deletion.
            conn.execute(
                text("DELETE FROM mod_learning.courses WHERE id=:id"), {"id": course}
            )
            assert (
                conn.scalar(text("SELECT count(*) FROM mod_learning.question_banks"))
                == 1
            )
            conn.execute(
                text("DELETE FROM mod_learning.question_banks WHERE id=:id"),
                {"id": bank},
            )
            assert conn.scalar(text("SELECT count(*) FROM mod_learning.questions")) == 0
    finally:
        app.dispose()
        admin.dispose()


def test_curriculum_missing_provider_is_refused_before_ddl(learning_database):
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = importlib.import_module(
        "dotmac_learning.migrations.versions.lr_0002_curriculum_reading"
    )
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            transaction = conn.begin()
            try:
                conn.execute(
                    text("DROP FUNCTION public.app_current_tenant_id() CASCADE")
                )
                with Operations.context(MigrationContext.configure(conn)):
                    with pytest.raises(PrerequisiteNotSatisfiedError):
                        revision.upgrade()
                assert set(inspect(conn).get_table_names(schema="mod_learning")) == set(
                    FIRST_TABLES
                ) | set(TABLES)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()


def test_curriculum_downgrade_preserves_first_slice_and_kernel(learning_database):
    from alembic import command
    from alembic.config import Config
    from tests.test_learning_catalog import (
        KERNEL_VERSIONS,
        LEARNING_VERSIONS,
        REPO_ROOT,
    )

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("version_locations", f"{KERNEL_VERSIONS} {LEARNING_VERSIONS}")
    command.downgrade(cfg, "lr_0001_learning")
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            assert set(inspector.get_table_names(schema="mod_learning")) == set(
                FIRST_TABLES
            )
            assert inspector.has_table("tenants", schema="public")
            assert inspector.has_table("parties", schema="public")
            assert inspector.has_table("party_persons", schema="public")
    finally:
        engine.dispose()
