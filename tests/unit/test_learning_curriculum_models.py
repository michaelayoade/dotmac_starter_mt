"""Immutable Academy 096 curriculum contracts, without a database runtime.

Oracle: course.py, assessment.py, reading.py, bookmark.py and migrations
0002/0004/0019/0024/0030/0043/0048. Migration details override ORM drift.
"""

import pytest
from dotmac_kernel.models import Tenant
from dotmac_learning.curriculum_models import (
    CURRICULUM_MODELS,
    Chapter,
    ChapterBookmark,
    ChapterNote,
    ChapterRead,
    Question,
    QuestionBank,
    QuestionBankVersion,
    SubtopicRead,
)
from dotmac_learning.models import Activity
from sqlalchemy import UniqueConstraint

MODELS = {
    "chapters": Chapter,
    "question_banks": QuestionBank,
    "question_bank_versions": QuestionBankVersion,
    "questions": Question,
    "chapter_reads": ChapterRead,
    "subtopic_reads": SubtopicRead,
    "chapter_bookmarks": ChapterBookmark,
    "chapter_notes": ChapterNote,
}
# Required fields beyond the common identity/timestamps; exact nullability.
FIELDS = {
    "chapters": {
        "course_id",
        "number",
        "title",
        "part",
        "body_html",
        "body_md",
        "source_hash",
        "order_index",
    },
    "question_banks": {
        "course_id",
        "chapter_number",
        "kind",
        "version",
        "current_version_id",
    },
    "question_bank_versions": {
        "bank_id",
        "version_no",
        "state",
        "origin",
        "content_hash",
        "lint_violations",
        "base_version_id",
        "draft_revision",
        "created_by_actor",
        "published_by",
        "published_at",
        "retired_by",
        "retired_at",
        "retire_reason",
        "superseded_by_version_id",
    },
    "questions": {
        "bank_id",
        "bank_version_id",
        "ext_id",
        "stem",
        "type",
        "options",
        "correct",
        "rubric_category",
        "category",
        "explanation",
        "weight",
    },
    "chapter_reads": {"person_id", "chapter_id"},
    "subtopic_reads": {"person_id", "chapter_id", "subtopic_slug"},
    "chapter_bookmarks": {"person_id", "course_id", "chapter_id"},
    "chapter_notes": {"person_id", "course_id", "chapter_id", "body"},
}
UNIQUES = {
    "chapters": {("tenant_id", "course_id", "number")},
    "question_banks": {("tenant_id", "id")},
    "question_bank_versions": {
        ("tenant_id", "id"),
        ("tenant_id", "bank_id", "version_no"),
    },
    "questions": {("tenant_id", "bank_version_id", "ext_id")},
    "chapter_reads": {("tenant_id", "person_id", "chapter_id")},
    "subtopic_reads": {("tenant_id", "person_id", "chapter_id", "subtopic_slug")},
    "chapter_bookmarks": {("tenant_id", "person_id", "chapter_id")},
    "chapter_notes": {("tenant_id", "person_id", "chapter_id")},
}
INDEXES = {
    "chapters": {"tenant_id", "course_id"},
    "question_banks": {"tenant_id", "course_id", "current_version_id"},
    "question_bank_versions": {"tenant_id", "bank_id"},
    "questions": {"tenant_id", "bank_id", "bank_version_id"},
    "chapter_reads": {"tenant_id", "person_id", "chapter_id"},
    "subtopic_reads": {"tenant_id", "person_id", "chapter_id"},
    "chapter_bookmarks": {"tenant_id", "person_id", "course_id", "chapter_id"},
    "chapter_notes": {"tenant_id", "person_id", "course_id", "chapter_id"},
}


def test_exact_curriculum_metadata_and_kernel_tenancy():
    assert set(CURRICULUM_MODELS) == set(MODELS.values())
    for name, model in MODELS.items():
        table = model.__table__
        assert table.schema == "mod_learning"
        assert set(table.c.keys()) == FIELDS[name] | {
            "id",
            "tenant_id",
            "created_at",
            "updated_at",
        }
        assert {c.name for c in table.c if c.nullable} == {
            "question_banks": {"chapter_number", "current_version_id"},
            "question_bank_versions": {
                "version_no",
                "base_version_id",
                "created_by_actor",
                "published_by",
                "published_at",
                "retired_by",
                "retired_at",
                "retire_reason",
                "superseded_by_version_id",
            },
            "questions": {"category", "bank_version_id"},
        }.get(name, set())
        assert table.c.id.primary_key
        assert table.c.id.default is not None
        assert any(
            fk.column is Tenant.__table__.c.id and fk.ondelete == "CASCADE"
            for fk in table.c.tenant_id.foreign_keys
        )
        for field in ("created_at", "updated_at"):
            assert table.c[field].type.timezone
            assert table.c[field].server_default is not None
        assert table.c.updated_at.onupdate is not None
        assert {
            tuple(c.name for c in uq.columns)
            for uq in table.constraints
            if isinstance(uq, UniqueConstraint)
        } == UNIQUES[name]
        assert {tuple(c.name for c in ix.columns) for ix in table.indexes} == {
            (c,) for c in INDEXES[name]
        } | {
            "question_banks": {("tenant_id", "course_id", "chapter_number", "kind")},
            "question_bank_versions": {("tenant_id", "bank_id")},
        }.get(name, set())


@pytest.mark.parametrize(
    ("model", "local", "remote"),
    [
        (
            Chapter,
            ("tenant_id", "course_id"),
            ("mod_learning.courses.tenant_id", "mod_learning.courses.id"),
        ),
        (
            Question,
            ("tenant_id", "bank_id"),
            ("mod_learning.question_banks.tenant_id", "mod_learning.question_banks.id"),
        ),
        (ChapterRead, ("chapter_id",), ("mod_learning.chapters.id",)),
        (SubtopicRead, ("chapter_id",), ("mod_learning.chapters.id",)),
        (ChapterBookmark, ("course_id",), ("mod_learning.courses.id",)),
        (ChapterBookmark, ("chapter_id",), ("mod_learning.chapters.id",)),
        (ChapterNote, ("course_id",), ("mod_learning.courses.id",)),
        (ChapterNote, ("chapter_id",), ("mod_learning.chapters.id",)),
    ],
)
def test_source_reference_edges(model, local, remote):
    matches = [
        fk
        for fk in model.__table__.foreign_key_constraints
        if tuple(fk.column_keys) == local
    ]
    assert len(matches) == 1
    assert tuple(e.target_fullname for e in matches[0].elements) == remote
    assert matches[0].ondelete == "CASCADE"


def test_no_inferred_person_bank_or_activity_edges():
    for model in (ChapterRead, SubtopicRead, ChapterBookmark, ChapterNote):
        assert not model.__table__.c.person_id.foreign_keys
    assert not QuestionBank.__table__.c.course_id.foreign_keys
    assert not Activity.__table__.c.course_id.foreign_keys
    assert not Activity.__table__.c.bank_id.foreign_keys
    assert not Activity.__table__.c.bank_version_id.foreign_keys
    assert "chapter_id" not in Activity.__table__.c
    assert "chapter_number" in Activity.__table__.c
    assert {
        len(m.__table__.foreign_key_constraints)
        for m in (Chapter, ChapterRead, SubtopicRead)
    } == {2}
    # lr_0010: questions also reference their bank version; banks
    # reference their current version (deferred, use_alter cycle).
    assert len(Question.__table__.foreign_key_constraints) == 3
    assert len(QuestionBank.__table__.foreign_key_constraints) == 2
    assert len(QuestionBankVersion.__table__.foreign_key_constraints) == 2
    assert (
        len(ChapterBookmark.__table__.foreign_key_constraints)
        == len(ChapterNote.__table__.foreign_key_constraints)
        == 3
    )


def test_source_types_lengths_and_defaults():
    for model, expected in (
        (Chapter, {"title": 200, "part": 20, "source_hash": 64}),
        (QuestionBank, {"kind": 10}),
        (Question, {"ext_id": 60, "type": 12, "rubric_category": 12, "category": 40}),
        (SubtopicRead, {"subtopic_slug": 64}),
    ):
        for field, length in expected.items():
            assert model.__table__.c[field].type.length == length
    for model, expected in (
        (
            Chapter,
            {
                "part": "",
                "body_html": "",
                "body_md": "",
                "source_hash": "",
                "order_index": 0,
            },
        ),
        (QuestionBank, {"version": 1}),
        (Question, {"explanation": ""}),
        (ChapterNote, {"body": ""}),
    ):
        for field, value in expected.items():
            assert model.__table__.c[field].default.arg == value
    assert Question.__table__.c.weight.default is None
    for field in ("options", "correct"):
        column = Question.__table__.c[field]
        assert column.type.__class__.__name__ == "JSONB"
        first, second = column.default.arg(None), column.default.arg(None)
        assert first == second == []
        assert first is not second


def test_question_json_uses_native_postgres_and_compiles_for_sqlite():
    from sqlalchemy import JSON
    from sqlalchemy.dialects import postgresql, sqlite
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.schema import CreateTable

    for name in ("options", "correct"):
        column_type = Question.__table__.c[name].type
        assert isinstance(column_type, JSONB)
        assert isinstance(column_type.dialect_impl(postgresql.dialect()), JSONB)
        assert isinstance(column_type.dialect_impl(sqlite.dialect()), JSON)
        assert column_type.compile(dialect=postgresql.dialect()) == "JSONB"
        assert column_type.compile(dialect=sqlite.dialect()) == "JSON"
    ddl = str(CreateTable(Question.__table__).compile(dialect=sqlite.dialect()))
    assert "options JSON" in ddl
    assert "correct JSON" in ddl
