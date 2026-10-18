"""Independent Academy 096 field and reference oracles for assessment storage."""

from dotmac_kernel.models import Tenant
from dotmac_learning.attempt_models import (
    ATTEMPT_MODELS,
    ATTEMPT_TABLES,
    ActivityAttempt,
    AttemptGrant,
)
from dotmac_learning.models import Activity
from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

FIELDS = {
    "activity_attempts": (
        "activity_id person_id question_ext_ids started_at submitted_at"
    ),
    "attempt_grants": "activity_id person_id extra_attempts reason granted_by",
}
NULLABLE = {"activity_attempts": {"submitted_at"}, "attempt_grants": {"granted_by"}}
INDEXES = {
    name: {
        ("tenant_id",),
        ("activity_id",),
        ("person_id",),
        ("tenant_id", "activity_id", "person_id"),
    }
    for name in FIELDS
}


def test_exact_eighteen_source_fields_and_logical_identity_references():
    assert ATTEMPT_MODELS == (ActivityAttempt, AttemptGrant)
    assert ATTEMPT_TABLES == ("activity_attempts", "attempt_grants")
    assert sum(len(model.__table__.c) for model in ATTEMPT_MODELS) == 18
    for model in ATTEMPT_MODELS:
        table = model.__table__
        assert table.schema == "mod_learning"
        assert set(table.c.keys()) == set(FIELDS[table.name].split()) | {
            "id",
            "tenant_id",
            "created_at",
            "updated_at",
        }
        assert {c.name for c in table.c if c.nullable} == NULLABLE[table.name]
        assert not table.c.person_id.foreign_keys
        assert not any(isinstance(c, CheckConstraint) for c in table.constraints)
        assert {
            tuple(c.name for c in u.columns)
            for u in table.constraints
            if isinstance(u, UniqueConstraint)
        } == {("tenant_id", "id")}
        assert {tuple(c.name for c in ix.columns) for ix in table.indexes} == INDEXES[
            table.name
        ]
        fks = {tuple(fk.column_keys): fk for fk in table.foreign_key_constraints}
        assert set(fks) == {("tenant_id",), ("tenant_id", "activity_id")}
        assert [e.column for e in fks[("tenant_id",)].elements] == [
            Tenant.__table__.c.id
        ]
        assert [e.column for e in fks[("tenant_id", "activity_id")].elements] == [
            Activity.__table__.c.tenant_id,
            Activity.__table__.c.id,
        ]
        assert all(fk.ondelete == "CASCADE" for fk in fks.values())
        assert table.c.id.default is not None
        assert table.c.id.server_default is None
        assert table.c.updated_at.onupdate is not None
        for field in ("created_at", "updated_at"):
            assert table.c[field].type.timezone
            assert str(table.c[field].server_default.arg) == "now()"
    assert not AttemptGrant.__table__.c.granted_by.foreign_keys


def test_source_json_defaults_and_partial_open_uniqueness():
    attempt = ActivityAttempt.__table__
    assert isinstance(attempt.c.question_ext_ids.type, JSONB)
    assert str(attempt.c.question_ext_ids.server_default.arg) == "'[]'"
    assert attempt.c.question_ext_ids.default is not None
    assert attempt.c.started_at.server_default is None
    assert attempt.c.started_at.type.timezone and attempt.c.submitted_at.type.timezone
    partial = next(i for i in attempt.indexes if i.name == "uq_activity_attempts_open")
    assert partial.unique
    assert str(partial.dialect_options["postgresql"]["where"]) == "submitted_at IS NULL"
    lookup = next(
        i
        for i in AttemptGrant.__table__.indexes
        if i.name == "ix_attempt_grants_lookup"
    )
    assert not lookup.unique
    assert AttemptGrant.__table__.c.extra_attempts.default is None
    assert str(AttemptGrant.__table__.c.extra_attempts.server_default.arg) == "1"


def test_all_three_kernel_prerequisites_precede_any_ddl(monkeypatch):
    import importlib

    import pytest

    revision = importlib.import_module(
        "dotmac_learning.migrations.versions.lr_0004_assessment"
    )
    assert revision.down_revision == "lr_0003_participation"
    assert set(revision.REQUIRES) == {
        "tenant_scope_catalog.v1",
        "module_database_roles.v1",
        "party_person_catalog.v1",
    }
    sentinel = object()
    calls = []

    class Refused(Exception):
        pass

    def guard(bind, requirements):
        assert bind is sentinel
        assert requirements == revision.REQUIRES
        calls.append("guard")
        raise Refused

    class Operations:
        @staticmethod
        def get_bind():
            return sentinel

        def __getattr__(self, name):
            pytest.fail(f"DDL reached before prerequisite refusal: {name}")

    monkeypatch.setattr(revision, "op", Operations())
    monkeypatch.setattr(revision, "require_prerequisites", guard)
    with pytest.raises(Refused):
        revision.upgrade()
    assert calls == ["guard"]


def test_attempt_json_retains_postgres_type_and_sqlite_ddl_compatibility():
    from sqlalchemy import JSON
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.schema import CreateTable

    column_type = ActivityAttempt.__table__.c.question_ext_ids.type
    assert isinstance(column_type, JSONB)
    assert isinstance(column_type.dialect_impl(postgresql.dialect()), JSONB)
    assert isinstance(column_type.dialect_impl(sqlite.dialect()), JSON)
    assert column_type.compile(dialect=postgresql.dialect()) == "JSONB"
    assert column_type.compile(dialect=sqlite.dialect()) == "JSON"
    for dialect in (postgresql.dialect(), sqlite.dialect()):
        ddl = str(CreateTable(ActivityAttempt.__table__).compile(dialect=dialect))
        assert "DEFAULT '[]'" in ddl
    assert "question_ext_ids JSON" in str(
        CreateTable(ActivityAttempt.__table__).compile(dialect=sqlite.dialect())
    )
