"""Hosted parity canaries for native survey response validation."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dotmac_surveys import (
    Answer,
    InvalidAnswer,
    Question,
    QuestionType,
    _native,
    lifecycle,
    validate_answers,
)


@pytest.mark.parametrize("question_type", [QuestionType.RATING, QuestionType.NPS])
@pytest.mark.parametrize(
    "token",
    ["+1", "01", "-0", "1.0", "1_0", "\u0661", "\uff11", "NaN", "1\x001", "9" * 5000],
)
def test_numeric_tokens_must_be_canonical_ascii_integers(
    question_type: QuestionType, token: str
) -> None:
    question = Question("q", question_type, "Score")
    message = (
        "rating must be an integer from 1 through 5"
        if question_type is QuestionType.RATING
        else "NPS must be an integer from 0 through 10"
    )
    with pytest.raises(InvalidAnswer) as caught:
        validate_answers((question,), (Answer("q", token),))
    assert str(caught.value) == message


@pytest.mark.parametrize("score", range(11))
def test_canonical_numeric_bounds(score: int) -> None:
    nps = validate_answers(
        (Question("q", QuestionType.NPS, "NPS"),), (Answer("q", str(score)),)
    )
    assert nps.nps_value == score
    question = Question("q", QuestionType.RATING, "Rating")
    if 1 <= score <= 5:
        assert validate_answers((question,), (Answer("q", str(score)),)).rating == score
    else:
        with pytest.raises(InvalidAnswer, match="1 through 5"):
            validate_answers((question,), (Answer("q", str(score)),))


def test_duplicate_stops_lazy_input_before_unknown_or_question_validation() -> None:
    consumed: list[int] = []

    def answers() -> Iterator[Answer]:
        consumed.append(1)
        yield Answer("unknown", "one")
        consumed.append(2)
        yield Answer("unknown", "two")
        pytest.fail("duplicate validation consumed subsequent input")

    with pytest.raises(InvalidAnswer) as caught:
        validate_answers((Question("q", QuestionType.RATING, "Required"),), answers())
    assert str(caught.value) == 'question "unknown" was answered more than once'
    assert consumed == [1, 2]


def test_unknowns_are_sorted_and_reported_before_required_or_invalid_values() -> None:
    questions = (Question("q", QuestionType.RATING, "Required"),)
    unknown_keys = ["z", "a'quote", "surrogate\ud800", "é"]
    with pytest.raises(InvalidAnswer) as caught:
        validate_answers(questions, (Answer(key, "value") for key in unknown_keys))
    assert str(caught.value) == (
        f"response contains unknown question(s): {sorted(unknown_keys)}"
    )


def test_question_order_controls_first_error_and_normalized_output_order() -> None:
    questions = (
        Question("first", QuestionType.FREE_TEXT, "First label"),
        Question("second", QuestionType.RATING, "Second label"),
    )
    with pytest.raises(InvalidAnswer) as caught:
        validate_answers(questions, (Answer("second", "invalid"),))
    assert str(caught.value) == 'an answer is required for "First label"'
    result = validate_answers(
        questions, (Answer("second", "5"), Answer("first", " hello "))
    )
    assert list(result.answers.items()) == [("first", "hello"), ("second", "5")]


@pytest.mark.parametrize(
    "whitespace",
    [
        "\t",
        "\n",
        "\v",
        "\f",
        "\r",
        " ",
        "\x1c",
        "\x1d",
        "\x1e",
        "\x1f",
        "\x85",
        "\xa0",
        "\u1680",
        *[chr(i) for i in range(0x2000, 0x200B)],
        "\u2028",
        "\u2029",
        "\u202f",
        "\u205f",
        "\u3000",
    ],
)
def test_python_strip_whitespace_including_control_separators(whitespace: str) -> None:
    result = validate_answers(
        (Question("q", QuestionType.FREE_TEXT, "Text"),),
        (Answer("q", f"{whitespace}value{whitespace}"),),
    )
    assert result.answers == {"q": "value"}
    with pytest.raises(InvalidAnswer, match="required"):
        validate_answers(
            (Question("q", QuestionType.FREE_TEXT, "Text"),),
            (Answer("q", whitespace),),
        )


@pytest.mark.parametrize("character", ["😀", "é", "\ud800", "\x00"])
def test_text_limit_counts_python_codepoints_and_preserves_surrogates(
    character: str,
) -> None:
    question = Question("q", QuestionType.FREE_TEXT, "Label\ud800")
    accepted = character * 10_000
    assert validate_answers((question,), (Answer("q", accepted),)).answers == {
        "q": accepted
    }
    with pytest.raises(InvalidAnswer) as caught:
        validate_answers((question,), (Answer("q", accepted + character),))
    assert (
        str(caught.value) == 'answer for "Label\ud800" cannot exceed 10000 characters'
    )


def test_choice_membership_is_exact_after_strip_and_blank_optionals_are_omitted() -> (
    None
):
    questions = (
        Question(
            "choice", QuestionType.MULTIPLE_CHOICE, "Choice", options=("Yes", "No")
        ),
        Question("optional", QuestionType.FREE_TEXT, "Optional", required=False),
    )
    result = validate_answers(
        questions, (Answer("choice", " Yes "), Answer("optional", " "))
    )
    assert result.answers == {"choice": "Yes"}
    with pytest.raises(InvalidAnswer) as caught:
        validate_answers(questions, (Answer("choice", "yes"),))
    assert str(caught.value) == 'choose a configured option for "Choice"'


def test_optional_numeric_blanks_and_empty_definition() -> None:
    assert validate_answers((), ()).answers == {}
    result = validate_answers(
        (Question("q", QuestionType.RATING, "Rating", required=False),),
        (Answer("q", ""),),
    )
    assert result.answers == {}
    assert result.rating is None
    assert result.nps_value is None


def test_public_validation_consumes_one_native_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    native = _native.validate_batch

    def spy(
        *, questions: list[_native.Question], answers: Iterator[_native.Answer]
    ) -> _native.Result:
        calls.append(questions)
        return native(questions=questions, answers=answers)

    monkeypatch.setattr(lifecycle._native, "validate_batch", spy)
    result = validate_answers(
        (
            Question("rating", QuestionType.RATING, "Rating"),
            Question("text", QuestionType.FREE_TEXT, "Text"),
        ),
        (Answer("text", "hello"), Answer("rating", "4")),
    )
    assert result.rating == 4
    assert result.answers == {"rating": "4", "text": "hello"}
    assert len(calls) == 1


def test_choice_surrogates_are_preserved_and_zero_width_space_is_not_stripped() -> None:
    question = Question(
        "q",
        QuestionType.MULTIPLE_CHOICE,
        "Choice\ud800",
        options=("yes\ud800", "no"),
    )
    assert validate_answers((question,), (Answer("q", "yes\ud800"),)).answers == {
        "q": "yes\ud800"
    }
    text = Question("q", QuestionType.FREE_TEXT, "Text")
    assert validate_answers((text,), (Answer("q", "\u200b"),)).answers == {
        "q": "\u200b"
    }


def test_answer_iterator_failure_propagates_before_question_errors() -> None:
    def answers() -> Iterator[Answer]:
        yield Answer("unknown", "value")
        raise RuntimeError("answer stream failed")

    with pytest.raises(RuntimeError, match="answer stream failed"):
        validate_answers((Question("q", QuestionType.RATING, "Required"),), answers())
