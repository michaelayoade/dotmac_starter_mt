"""Source characterization for normalized curriculum input and bank rules."""

import hashlib
from uuid import uuid4

import pytest
from dotmac_learning.curriculum import chapter_source_hash, lint_bank
from dotmac_learning.curriculum_contracts import (
    BankDocument,
    BankQuestion,
    ChapterSource,
    CourseCreate,
    FigureSource,
)
from pydantic import ValidationError


def test_hash_uses_raw_utf8_and_sorted_referenced_figure_bytes_or_missing():
    source = ChapterSource(
        raw_utf8=b"---\nchapter: 1\n---\n# Raw  text\n",
        figures=(
            FigureSource(id="B", content=None),
            FigureSource(id="A", content=b"png"),
        ),
    )
    figures = hashlib.sha256(b"ApngB<missing>").hexdigest().encode()
    assert (
        chapter_source_hash(source)
        == hashlib.sha256(source.raw_utf8 + figures).hexdigest()
    )
    assert chapter_source_hash(source) != chapter_source_hash(
        source.model_copy(update={"raw_utf8": b"# normalized"})
    )


def test_contracts_forbid_unrecognized_command_fields():
    with pytest.raises(ValidationError):
        CourseCreate(
            tenant_id=uuid4(), slug="x", title="X", discipline="x", status="published"
        )


def _balanced():
    return BankDocument(
        course="foundation",
        chapter=3,
        kind="chapter",
        version=7,
        questions=tuple(
            BankQuestion(
                id=str(i),
                stem="S",
                type="single",
                rubric_category=(
                    "recall" if i < 2 else "application" if i < 7 else "analysis"
                ),
                options=["AAAA", "BBBB", "CCCC", "DDDD"],
                correct=["AAAA"],
            )
            for i in range(10)
        ),
    )


def test_ported_lint_keeps_source_rubric_tolerance_and_policy_rules():
    doc = _balanced()
    assert lint_bank(doc) == []
    assert any(
        "nothing is held back" in x
        for x in lint_bank(doc.model_copy(update={"policy": {"pool": 11}}))
    )
    assert any(
        "fraction between 0 and 1" in x
        for x in lint_bank(doc.model_copy(update={"policy": {"pass_threshold": 70}}))
    )
    assert not any(
        "policy" in x
        for x in lint_bank(doc.model_copy(update={"policy": {"pool": 10}}))
    )


def test_ported_lint_keeps_length_and_malformed_option_characterization():
    doc = _balanced()
    questions = tuple(
        q.model_copy(
            update={
                "options": ["The full careful answer", "No", "Yes", "Maybe"],
                "correct": ["The full careful answer"],
            }
        )
        for q in doc.questions
    )
    assert any(
        "longest option" in x
        for x in lint_bank(doc.model_copy(update={"questions": questions}))
    )
    bad = doc.questions[0].model_copy(update={"options": [{"Word": "rest"}, "B"]})
    assert any(
        "YAML mapping" in x
        for x in lint_bank(
            doc.model_copy(update={"questions": (bad,) + doc.questions[1:]})
        )
    )
