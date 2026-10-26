"""Hosted unit canaries for the unvalidated Academy decoding boundary."""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_learning import (
    BankDoc,
    BankDocumentHeader,
    balance_targets,
    decoded_doc_from_db,
    lint_bank,
    propose_balance,
)
from dotmac_learning.bank_activity_contracts import (
    BankActivityTerms,
    ImportBankWithActivityCommand,
)
from dotmac_learning.curriculum_contracts import BankDocument, BankQuestion
from pydantic import ValidationError


def _balanced():
    return BankDoc(
        course="bank-course",
        chapter=2,
        kind="chapter",
        version=1,
        questions=[
            {
                "id": str(i),
                "stem": "Q",
                "type": "truefalse",
                "rubric_category": category,
                "correct": ["true"],
            }
            for i, category in enumerate(
                ["recall"] * 2 + ["application"] * 5 + ["analysis"] * 3
            )
        ],
    )


def test_raw_command_preserves_mutable_doc_and_nested_decoder_values():
    doc = _balanced()
    marker = date(2026, 10, 17)
    doc.questions[0].update(weight=1.9, competency=marker, options=[marker])
    doc.policy["pool"] = marker
    tenant = uuid4()
    command = ImportBankWithActivityCommand(
        tenant_id=str(tenant),
        doc=doc,
        terms=BANK_ACTIVITY_TERMS,
        missing_question_weight=1,
    )
    assert command.tenant_id == tenant and command.doc is doc
    assert command.doc.questions is doc.questions and command.doc.policy is doc.policy
    assert command.doc.questions[0]["options"][0] is marker
    doc.questions[0]["weight"] = "bad"
    assert command.doc.questions[0]["weight"] == "bad"
    with pytest.raises(ValidationError):
        ImportBankWithActivityCommand(
            tenant_id=tenant,
            doc=doc,
            unknown=True,
            terms=BANK_ACTIVITY_TERMS,
            missing_question_weight=1,
        )
    with pytest.raises(ValidationError):
        ImportBankWithActivityCommand(
            tenant_id="bad",
            doc=doc,
            terms=BANK_ACTIVITY_TERMS,
            missing_question_weight=1,
        )
    # A mapping still takes the existing normalized path; raw acceptance is
    # specifically for the public dataclass instance, not dataclass coercion.
    with pytest.raises(ValidationError):
        ImportBankWithActivityCommand(
            tenant_id=tenant,
            doc={
                "course": "bank-course",
                "kind": "chapter",
                "questions": [
                    {
                        "id": "q",
                        "stem": "Q",
                        "type": "numeric",
                        "rubric_category": "recall",
                        "correct": marker,
                    }
                ],
            },
            terms=BANK_ACTIVITY_TERMS,
            missing_question_weight=1,
        )


def test_normalized_document_identity_and_lint_are_retained():
    raw = _balanced()
    doc = BankDocument(
        course=raw.course,
        chapter=raw.chapter,
        kind=raw.kind,
        questions=tuple(BankQuestion(**{**q, "weight": 1}) for q in raw.questions),
    )
    assert (
        ImportBankWithActivityCommand(
            tenant_id=uuid4(),
            doc=doc,
            terms=BANK_ACTIVITY_TERMS,
            missing_question_weight=1,
        ).doc
        is doc
    )
    assert lint_bank(doc) == lint_bank(raw) == []


def test_raw_lint_keeps_ordered_messages_and_source_boolean_policy_rules():
    doc = BankDoc(
        "x",
        1,
        "chapter",
        1,
        [{"id": "q", "rubric_category": "bad"}],
        {"z": 1, "pool": 0, "max_attempts": 0, "pass_threshold": True, "mode": "oops"},
    )
    assert lint_bank(doc) == [
        "q: invalid rubric_category 'bad'",
        "rubric mix off: recall 0% (target 20% ±10%)",
        "rubric mix off: application 0% (target 50% ±10%)",
        "rubric mix off: analysis 0% (target 30% ±10%)",
        "policy: unknown key(s) z",
        "policy: pool must be a positive integer, got 0",
        "policy: max_attempts must be a positive integer, got 0",
        "policy: pass_threshold must be a number, got True",
        "policy: mode must be one of exam, graded, practice, got 'oops'",
    ]
    doc = _balanced()
    doc.policy.update(pool=True, max_attempts=True, pass_threshold=True)
    assert lint_bank(doc) == ["policy: pass_threshold must be a number, got True"]


def test_public_balance_helpers_keep_existing_targets_and_minimum_proposal():
    answer = "12345678901234567890"
    doc = BankDoc(
        "x",
        1,
        "chapter",
        1,
        [
            {
                "id": "q",
                "type": "single",
                "correct": [answer],
                "options": [answer, "a", "bb", "ccc"],
            }
        ],
    )
    assert balance_targets(doc) == [
        {
            "id": "q",
            "answer_len": 20,
            "distractors": [
                {"text": "a", "now": 1, "target": 17},
                {"text": "bb", "now": 2, "target": 20},
                {"text": "ccc", "now": 3, "target": 24},
            ],
        }
    ]
    assert propose_balance(doc) == [
        {
            "id": "q",
            "option": "ccc",
            "needed": 18,
            "suggestion": "ccc, as a general rule",
            "note": None,
        }
    ]
    assert doc.questions[0]["options"] == [answer, "a", "bb", "ccc"]


@pytest.mark.parametrize("empty", (False, True))
def test_raw_read_uses_only_supplied_header_and_preserves_source_read_timing(empty):
    events = []
    tenant, bank_id = uuid4(), uuid4()
    values = {
        "tenant_id": tenant,
        "id": bank_id,
        "chapter_number": None,
        "kind": "legacy",
        "version": 19,
    }

    class Header:
        # Exactly the five read-only properties; no course_id or ORM dependency.
        @property
        def tenant_id(self):
            events.append("tenant_id")
            return values["tenant_id"]

        @property
        def id(self):
            events.append("id")
            return values["id"]

        @property
        def chapter_number(self):
            events.append("chapter_number")
            return values["chapter_number"]

        @property
        def kind(self):
            events.append("kind")
            return values["kind"]

        @property
        def version(self):
            events.append("version")
            return values["version"]

    payload = {"pattern": r"^a\d+$", "flags": [None, True]}
    row = SimpleNamespace(
        ext_id="q",
        stem=None,
        type="regex",
        options=None,
        correct=payload,
        rubric_category=None,
        category=None,
        explanation=None,
        weight=1.9,
    )

    class Rows:
        def all(self):
            events.append("all")
            return [] if empty else [row]

    class ReadSession:
        def scalars(self, statement):
            events.append("scalars")
            sql = str(statement).lower()
            assert "question_banks" not in sql
            assert "order by mod_learning.questions.ext_id" in sql
            assert statement.compile().params == {
                "tenant_id_1": tenant,
                "bank_id_1": bank_id,
            }
            return Rows()

    header: BankDocumentHeader = Header()
    doc = decoded_doc_from_db(ReadSession(), bank=header, course_slug="supplied")
    assert events == [
        "tenant_id",
        "id",
        "scalars",
        "all",
        "chapter_number",
        "kind",
        "version",
    ]
    assert (doc.course, doc.chapter, doc.kind, doc.version, doc.policy) == (
        "supplied",
        None,
        "legacy",
        19,
        {},
    )
    if empty:
        assert doc.questions == []
    else:
        assert doc.questions == [
            {
                "id": "q",
                "stem": None,
                "type": "regex",
                "options": None,
                "correct": payload,
                "rubric_category": None,
                "category": None,
                "explanation": None,
                "weight": 1.9,
            }
        ]
        assert doc.questions[0]["correct"] is payload
        doc.questions[0]["metadata"] = "mutable"
        assert doc.questions[0]["metadata"] == "mutable"


BANK_ACTIVITY_TERMS = BankActivityTerms(
    pass_threshold_by_kind={"chapter": 0.0, "mid": 0.60, "final": 0.70},
    fallback_pass_threshold=0.0,
    new_activity_assessment_mode="graded",
    new_activity_grading="auto",
    new_activity_weight=1.0,
    new_activity_scoring_policy="best",
    new_activity_min_attempt_interval_seconds=0,
)
