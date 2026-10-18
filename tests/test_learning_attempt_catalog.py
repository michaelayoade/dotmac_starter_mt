"""Hosted genuine-migration PostgreSQL canaries; never local test execution."""

from __future__ import annotations

import importlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError

from tests import test_learning_catalog as learning_catalog
from tests.test_learning_catalog import _course, _scope, _tenant
from tests.test_learning_participation_catalog import (
    BASE_TABLES,
)
from tests.test_learning_participation_catalog import (
    TABLES as PARTICIPATION_TABLES,
)
from tests.unit.test_learning_attempt_models import FIELDS, INDEXES, NULLABLE

learning_database = learning_catalog.learning_database
TABLES = set(FIELDS)
PREVIOUS_TABLES = BASE_TABLES | PARTICIPATION_TABLES

# Closed Core relations deliberately have no Python or server defaults: inserts
# therefore observe actual migrated database behavior rather than ORM behavior.
_metadata = MetaData()
RELATIONS = {
    name: Table(
        name,
        _metadata,
        Column("id", Uuid),
        Column("tenant_id", Uuid),
        Column("activity_id", Uuid),
        Column("person_id", Uuid),
        *(
            [
                Column("question_ext_ids", JSONB),
                Column("started_at", DateTime(timezone=True)),
                Column("submitted_at", DateTime(timezone=True)),
            ]
            if name == "activity_attempts"
            else [
                Column("extra_attempts", Integer),
                Column("reason", Text),
                Column("granted_by", Uuid),
            ]
        ),
        Column("created_at", DateTime(timezone=True)),
        Column("updated_at", DateTime(timezone=True)),
        schema="mod_learning",
    )
    for name in TABLES
}
ACTIVITIES = Table(
    "activities",
    _metadata,
    Column("id", Uuid),
    Column("tenant_id", Uuid),
    Column("course_id", Uuid),
    Column("type", Text),
    Column("title", Text),
    schema="mod_learning",
)
TENANTS = Table("tenants", _metadata, Column("id", Uuid), schema="public")


def _activity(conn, tenant):
    activity = uuid.uuid4()
    conn.execute(
        ACTIVITIES.insert().values(
            id=activity,
            tenant_id=tenant,
            course_id=_course(conn, tenant),
            type="mcq_test",
            title="Assessment",
        )
    )
    return activity


def _row(conn, table, tenant, activity, person=None, **fields):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": tenant,
        "activity_id": activity,
        "person_id": person or uuid.uuid4(),
        **fields,
    }
    if table == "activity_attempts":
        values.setdefault("started_at", datetime.now(UTC))
    else:
        values.setdefault("reason", "Source resit")
    conn.execute(RELATIONS[table].insert().values(**values))
    return values["id"]


def _reject(conn, statement, state):
    with pytest.raises(DBAPIError) as caught, conn.begin_nested():
        conn.execute(statement)
    assert getattr(caught.value.orig, "sqlstate", None) == state


def test_prior_three_learning_migrations_and_assessment_only_downgrade(
    learning_database,
):
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = importlib.import_module(
        "dotmac_learning.migrations.versions.lr_0004_assessment"
    )
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn, conn.begin() as transaction:
            with Operations.context(MigrationContext.configure(conn)):
                revision.downgrade()
                assert (
                    set(inspect(conn).get_table_names(schema="mod_learning"))
                    == PREVIOUS_TABLES
                )
                for table in ("tenants", "parties", "party_persons"):
                    assert inspect(conn).has_table(table, schema="public")
                revision.upgrade()
                assert (
                    set(inspect(conn).get_table_names(schema="mod_learning"))
                    == PREVIOUS_TABLES | TABLES
                )
            transaction.rollback()
    finally:
        engine.dispose()


def test_tenant_rls_actual_activity_fk_and_logical_people(learning_database):
    admin, app = (create_engine(url) for url in learning_database)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant_a)
            _tenant(conn, tenant_b)
        with app.begin() as conn:
            _scope(conn, tenant_a)
            activity_a = _activity(conn, tenant_a)
            for table in TABLES:
                _row(
                    conn,
                    table,
                    tenant_a,
                    activity_a,
                    **(
                        {"granted_by": uuid.uuid4()}
                        if table == "attempt_grants"
                        else {}
                    ),
                )
        with app.begin() as conn:
            _scope(conn, tenant_b)
            activity_b = _activity(conn, tenant_b)
            for table in TABLES:
                relation = RELATIONS[table]
                assert conn.scalar(select(func.count()).select_from(relation)) == 0
                assert (
                    conn.execute(
                        relation.update()
                        .values(updated_at=func.now())
                        .returning(relation.c.id)
                    ).all()
                    == []
                )
                assert (
                    conn.execute(relation.delete().returning(relation.c.id)).all() == []
                )
                fields = (
                    {"started_at": datetime.now(UTC)}
                    if table == "activity_attempts"
                    else {"reason": "Cross"}
                )
                base = {
                    "id": uuid.uuid4(),
                    "tenant_id": tenant_b,
                    "activity_id": activity_a,
                    "person_id": uuid.uuid4(),
                    **fields,
                }
                _reject(conn, relation.insert().values(**base), "23503")
                _reject(
                    conn,
                    relation.insert().values(**{**base, "tenant_id": tenant_a}),
                    "42501",
                )
                own = _row(conn, table, tenant_b, activity_b)
                _reject(
                    conn,
                    relation.update()
                    .where(relation.c.id == own)
                    .values(activity_id=activity_a),
                    "23503",
                )
                _reject(
                    conn,
                    relation.update()
                    .where(relation.c.id == own)
                    .values(tenant_id=tenant_a),
                    "42501",
                )
                assert (
                    conn.execute(
                        relation.update()
                        .where(relation.c.id == own)
                        .values(updated_at=func.now())
                        .returning(relation.c.id)
                    ).scalar_one()
                    == own
                )
                assert (
                    conn.execute(
                        relation.delete()
                        .where(relation.c.id == own)
                        .returning(relation.c.id)
                    ).scalar_one()
                    == own
                )
        with app.begin() as conn:
            for table in TABLES:
                assert (
                    conn.scalar(select(func.count()).select_from(RELATIONS[table])) == 0
                )
    finally:
        app.dispose()
        admin.dispose()


def test_actual_defaults_json_history_required_fields_and_open_uniqueness(
    learning_database,
):
    admin, app = (create_engine(url) for url in learning_database)
    tenant = uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            activity, person = _activity(conn, tenant), uuid.uuid4()
            attempt, grant = RELATIONS["activity_attempts"], RELATIONS["attempt_grants"]
            opened = _row(conn, "activity_attempts", tenant, activity, person)
            stored = conn.execute(
                select(
                    attempt.c.question_ext_ids,
                    attempt.c.submitted_at,
                    attempt.c.created_at,
                    attempt.c.updated_at,
                ).where(attempt.c.id == opened)
            ).one()
            assert stored[:2] == ([], None)
            assert stored.created_at is not None
            assert stored.created_at == stored.updated_at
            base = {
                "id": uuid.uuid4(),
                "tenant_id": tenant,
                "activity_id": activity,
                "person_id": person,
                "started_at": datetime.now(UTC),
            }
            _reject(conn, attempt.insert().values(**base), "23505")
            conn.execute(
                attempt.update()
                .where(attempt.c.id == opened)
                .values(submitted_at=func.now())
            )
            for _ in range(2):
                _row(
                    conn,
                    "activity_attempts",
                    tenant,
                    activity,
                    person,
                    submitted_at=datetime.now(UTC),
                )
            _row(conn, "activity_attempts", tenant, activity, person)
            for payload in (
                None,
                "scalar",
                42,
                True,
                {"historical": "object"},
                ["q2", "q1"],
            ):
                key = _row(
                    conn,
                    "activity_attempts",
                    tenant,
                    activity,
                    question_ext_ids=JSONB.NULL if payload is None else payload,
                )
                assert (
                    conn.scalar(
                        select(attempt.c.question_ext_ids).where(attempt.c.id == key)
                    )
                    == payload
                )
            _reject(
                conn,
                attempt.insert().values(
                    **{**base, "person_id": uuid.uuid4(), "started_at": None}
                ),
                "23502",
            )
            missing_start = {k: v for k, v in base.items() if k != "started_at"}
            _reject(conn, attempt.insert().values(**missing_start), "23502")
            _reject(
                conn,
                attempt.insert().values(
                    **{
                        **base,
                        "person_id": uuid.uuid4(),
                        "question_ext_ids": text("NULL"),
                    }
                ),
                "23502",
            )
            key = _row(conn, "attempt_grants", tenant, activity, person)
            assert conn.execute(
                select(
                    grant.c.extra_attempts, grant.c.reason, grant.c.granted_by
                ).where(grant.c.id == key)
            ).one() == (1, "Source resit", None)
            prior_updated_at = conn.scalar(
                select(grant.c.updated_at).where(grant.c.id == key)
            )
            conn.execute(
                grant.update().where(grant.c.id == key).values(reason="Edited")
            )
            assert (
                conn.scalar(select(grant.c.updated_at).where(grant.c.id == key))
                == prior_updated_at
            )
            for table, required in (
                (attempt, {"started_at": datetime.now(UTC)}),
                (grant, {"reason": "Required"}),
            ):
                for field in ("created_at", "updated_at"):
                    _reject(
                        conn,
                        table.insert().values(
                            id=uuid.uuid4(),
                            tenant_id=tenant,
                            activity_id=activity,
                            person_id=uuid.uuid4(),
                            **required,
                            **{field: None},
                        ),
                        "23502",
                    )
            _reject(
                conn,
                grant.insert().values(
                    id=uuid.uuid4(),
                    tenant_id=tenant,
                    activity_id=activity,
                    person_id=person,
                ),
                "23502",
            )
            for amount in (-2147483648, -1, 0, 2147483647):
                key = _row(
                    conn,
                    "attempt_grants",
                    tenant,
                    activity,
                    person,
                    extra_attempts=amount,
                    reason="",
                    granted_by=uuid.uuid4(),
                )
                assert (
                    conn.scalar(select(grant.c.extra_attempts).where(grant.c.id == key))
                    == amount
                )
    finally:
        app.dispose()
        admin.dispose()


@pytest.mark.parametrize("root", ["activity", "tenant"])
def test_actual_cascades(learning_database, root):
    admin, app = (create_engine(url) for url in learning_database)
    tenant = uuid.uuid4()
    try:
        with admin.begin() as conn:
            _tenant(conn, tenant)
        with app.begin() as conn:
            _scope(conn, tenant)
            activity = _activity(conn, tenant)
            for table in TABLES:
                _row(conn, table, tenant, activity)
            if root == "activity":
                conn.execute(ACTIVITIES.delete().where(ACTIVITIES.c.id == activity))
                for table in TABLES:
                    assert (
                        conn.scalar(select(func.count()).select_from(RELATIONS[table]))
                        == 0
                    )
        if root == "tenant":
            with admin.begin() as conn:
                conn.execute(TENANTS.delete().where(TENANTS.c.id == tenant))
                for table in TABLES:
                    assert (
                        conn.scalar(
                            select(func.count())
                            .select_from(RELATIONS[table])
                            .where(RELATIONS[table].c.tenant_id == tenant)
                        )
                        == 0
                    )
    finally:
        app.dispose()
        admin.dispose()


def test_actual_catalog_fields_indexes_foreign_keys_security_and_grants(
    learning_database,
):
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn:
            inspector = inspect(conn)
            assert (
                set(inspector.get_table_names(schema="mod_learning"))
                == PREVIOUS_TABLES | TABLES
            )
            for table in TABLES:
                assert not inspector.has_table(table, schema="public")
                columns = {
                    c["name"]: c
                    for c in inspector.get_columns(table, schema="mod_learning")
                }
                assert set(columns) == set(FIELDS[table].split()) | {
                    "id",
                    "tenant_id",
                    "created_at",
                    "updated_at",
                }
                assert {
                    name for name, c in columns.items() if c["nullable"]
                } == NULLABLE[table]
                assert columns["id"]["default"] is None
                for field in ("created_at", "updated_at"):
                    assert columns[field]["default"] == "now()"
                    assert columns[field]["type"].timezone
                assert not inspector.get_check_constraints(table, schema="mod_learning")
                assert {
                    tuple(u["column_names"])
                    for u in inspector.get_unique_constraints(
                        table, schema="mod_learning"
                    )
                } == {("tenant_id", "id")}
                indexes = [
                    i
                    for i in inspector.get_indexes(table, schema="mod_learning")
                    if not i.get("duplicates_constraint")
                ]
                assert {tuple(i["column_names"]) for i in indexes} == INDEXES[table]
                compound = next(i for i in indexes if len(i["column_names"]) == 3)
                assert bool(compound["unique"]) == (table == "activity_attempts")
                if table == "activity_attempts":
                    assert (
                        compound["dialect_options"]["postgresql_where"].strip("()")
                        == "submitted_at IS NULL"
                    )
                    assert columns["question_ext_ids"]["default"] == "'[]'::jsonb"
                    assert isinstance(columns["question_ext_ids"]["type"], JSONB)
                    assert columns["started_at"]["default"] is None
                else:
                    assert (
                        columns["extra_attempts"]["default"].split("::")[0].strip("'")
                        == "1"
                    )
                assert {
                    (
                        tuple(fk["constrained_columns"]),
                        fk["referred_schema"],
                        fk["referred_table"],
                        tuple(fk["referred_columns"]),
                        fk["options"].get("ondelete"),
                    )
                    for fk in inspector.get_foreign_keys(table, schema="mod_learning")
                } == {
                    (("tenant_id",), "public", "tenants", ("id",), "CASCADE"),
                    (
                        ("tenant_id", "activity_id"),
                        "mod_learning",
                        "activities",
                        ("tenant_id", "id"),
                        "CASCADE",
                    ),
                }
                assert conn.execute(
                    text(
                        "SELECT relrowsecurity, relforcerowsecurity FROM "
                        "pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n "
                        "ON n.oid=c.relnamespace WHERE n.nspname='mod_learning' "
                        "AND c.relname=:table"
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
                assert (
                    conn.scalar(
                        text(
                            "SELECT count(*) FROM pg_catalog.pg_trigger WHERE "
                            "tgrelid=CAST(:relation AS pg_catalog.regclass) "
                            "AND NOT tgisinternal"
                        ),
                        {"relation": f"mod_learning.{table}"},
                    )
                    == 0
                )
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
                                "SELECT pg_catalog.has_table_privilege("
                                "CAST(:role AS text), CAST(:relation AS text), "
                                "CAST(:privilege AS text))"
                            ),
                            {
                                "role": role,
                                "relation": f"mod_learning.{table}",
                                "privilege": privilege,
                            },
                        ) == (privilege in {"SELECT", "INSERT", "UPDATE", "DELETE"})
    finally:
        engine.dispose()


@pytest.mark.parametrize("damage", ["tenant", "person"])
def test_kernel_prerequisite_refusal_before_any_assessment_ddl(
    learning_database, damage
):
    from dotmac_kernel.migrations.verify import PrerequisiteNotSatisfiedError

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    revision = importlib.import_module(
        "dotmac_learning.migrations.versions.lr_0004_assessment"
    )
    engine = create_engine(learning_database[0])
    try:
        with engine.connect() as conn, conn.begin() as transaction:
            with Operations.context(MigrationContext.configure(conn)):
                revision.downgrade()
                if damage == "tenant":
                    conn.execute(
                        text("DROP FUNCTION public.app_current_tenant_id() CASCADE")
                    )
                elif damage == "person":
                    conn.execute(text("DROP TABLE public.party_persons CASCADE"))
                with pytest.raises(PrerequisiteNotSatisfiedError):
                    revision.upgrade()
                assert (
                    set(inspect(conn).get_table_names(schema="mod_learning"))
                    == PREVIOUS_TABLES
                )
            transaction.rollback()
    finally:
        engine.dispose()
