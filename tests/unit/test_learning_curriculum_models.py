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
    SubtopicRead,
)
from dotmac_learning.models import Activity
from sqlalchemy import UniqueConstraint

MODELS = {
    "chapters": Chapter,
    "question_banks": QuestionBank,
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
    "question_banks": {"course_id", "chapter_number", "kind", "version"},
    "questions": {
        "bank_id",
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
    "questions": {("tenant_id", "bank_id", "ext_id")},
    "chapter_reads": {("tenant_id", "person_id", "chapter_id")},
    "subtopic_reads": {("tenant_id", "person_id", "chapter_id", "subtopic_slug")},
    "chapter_bookmarks": {("tenant_id", "person_id", "chapter_id")},
    "chapter_notes": {("tenant_id", "person_id", "chapter_id")},
}
INDEXES = {
    "chapters": {"tenant_id", "course_id"},
    "question_banks": {"tenant_id", "course_id"},
    "questions": {"tenant_id", "bank_id"},
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
            "question_banks": {"chapter_number"},
            "questions": {"category"},
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
        }


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
    assert "chapter_id" not in Activity.__table__.c
    assert "chapter_number" in Activity.__table__.c
    assert {
        len(m.__table__.foreign_key_constraints)
        for m in (Chapter, Question, ChapterRead, SubtopicRead)
    } == {2}
    assert len(QuestionBank.__table__.foreign_key_constraints) == 1
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
        (Question, {"explanation": "", "weight": 1}),
        (ChapterNote, {"body": ""}),
    ):
        for field, value in expected.items():
            assert model.__table__.c[field].default.arg == value
    for field in ("options", "correct"):
        column = Question.__table__.c[field]
        assert column.type.__class__.__name__ == "JSONB"
        first, second = column.default.arg(None), column.default.arg(None)
        assert first == second == []
        assert first is not second
