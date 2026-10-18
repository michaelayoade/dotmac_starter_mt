"""Authoring options preserve source JSON configuration; hosted CI only."""

import pytest
from dotmac_learning import BankDocument, BankQuestion, lint_bank
from pydantic import ValidationError


def _question(**kwargs):
    return BankQuestion(
        id="Q", stem="Question", type="numeric", rubric_category="recall", **kwargs
    )


def test_omitted_bank_options_keep_independent_list_defaults():
    first, second = _question(), _question()
    assert first.options == second.options == []
    assert first.options is not second.options
    first.options.append("changed")
    assert second.options == []


@pytest.mark.parametrize(
    "options",
    [
        {"tolerance": 0.5},
        {"regex": True},
        {"nested": [None, False, 0, 2.5, "", {"enabled": True}]},
    ],
)
def test_bank_options_preserve_dictionary_configuration(options):
    question = _question(options=options)
    assert type(question.options) is dict and question.options == options
    assert question.model_dump()["options"] == options


def test_bank_options_preserve_existing_list_and_other_validation():
    options = ["A", "B", None, {"nested": True}]
    assert _question(options=options).options == options
    with pytest.raises(ValidationError):
        _question(options={"invalid": object()})
    with pytest.raises(ValidationError):
        BankQuestion(
            id="Q",
            stem="Question",
            type="numeric",
            rubric_category="recall",
            unexpected=True,
        )
    with pytest.raises(ValidationError):
        BankQuestion(id="Q", type="numeric", rubric_category="recall")


def test_public_linter_accepts_balanced_numeric_and_regex_configuration_objects():
    document = BankDocument(
        course="options",
        chapter=1,
        kind="chapter",
        questions=tuple(
            BankQuestion(
                id=str(i),
                stem="Question",
                type="numeric" if i % 2 == 0 else "short_text",
                rubric_category="recall"
                if i < 2
                else "application"
                if i < 7
                else "analysis",
                options={"tolerance": 0.5} if i % 2 == 0 else {"regex": True},
                correct=42 if i % 2 == 0 else ["go+al"],
            )
            for i in range(10)
        ),
    )
    assert lint_bank(document) == []
