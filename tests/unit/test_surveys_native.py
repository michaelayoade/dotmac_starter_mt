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


# The displaced implementation is retained only as the hosted differential oracle.
def _reference_metrics(*, ratings, nps_values):
    from decimal import Decimal

    from dotmac_surveys.contracts import SurveyMetrics

    rating_rows = tuple(ratings)
    nps_rows = tuple(nps_values)
    if len(rating_rows) != len(nps_rows):
        raise ValueError("rating and NPS projections must describe the same responses")
    present_ratings = [Decimal(value) for value in rating_rows if value is not None]
    average = (
        (sum(present_ratings, Decimal(0)) / Decimal(len(present_ratings))).quantize(
            Decimal("0.01")
        )
        if present_ratings
        else None
    )
    present_nps = [value for value in nps_rows if value is not None]
    nps_score = None
    if present_nps:
        promoters = sum(value >= 9 for value in present_nps)
        detractors = sum(value <= 6 for value in present_nps)
        nps_score = (
            (Decimal(promoters - detractors) / Decimal(len(present_nps))) * Decimal(100)
        ).quantize(Decimal("0.01"))
    return SurveyMetrics(len(rating_rows), average, nps_score)


def _metric_outcome(function, ratings, nps_values, context):
    from decimal import getcontext, localcontext

    with localcontext(context):
        try:
            result = function(ratings=ratings, nps_values=nps_values)
            outcome = (
                "ok",
                result.total_responses,
                str(result.avg_rating) if result.avg_rating is not None else None,
                str(result.nps_score) if result.nps_score is not None else None,
            )
        except Exception as error:
            outcome = ("error", type(error), error.args)
        return outcome, dict(getcontext().flags)


@pytest.mark.parametrize(
    "rounding",
    [
        "ROUND_CEILING",
        "ROUND_FLOOR",
        "ROUND_UP",
        "ROUND_DOWN",
        "ROUND_HALF_UP",
        "ROUND_HALF_DOWN",
        "ROUND_HALF_EVEN",
        "ROUND_05UP",
    ],
)
@pytest.mark.parametrize("precision", [1, 2, 4, 28])
@pytest.mark.parametrize("trapped", [False, True])
def test_metric_context_rounding_flags_and_traps_match_reference(
    rounding: str, precision: int, trapped: bool
) -> None:
    from decimal import Context, Inexact, InvalidOperation, Rounded

    from dotmac_surveys import calculate_metrics

    context = Context(prec=precision, rounding=rounding)
    context.traps[InvalidOperation] = trapped
    context.traps[Inexact] = trapped
    context.traps[Rounded] = trapped
    ratings = (5, 2, None, 4, 1, 3, 4)
    nps = (10, 9, 7, None, 6, 0, 8)
    assert _metric_outcome(calculate_metrics, ratings, nps, context) == _metric_outcome(
        _reference_metrics, ratings, nps, context
    )


@pytest.mark.parametrize(
    ("ratings", "nps"),
    [
        ((), ()),
        ((None, None), (None, None)),
        ((1, None, 5), (None, -100, 100)),
        ((10**100, -(10**100), 1), (10**6000, -(10**6000), 7)),
        (("sNaN", "NaN"), (None, None)),
        (("Infinity", "-Infinity"), (10, 0)),
        (("0.000000001", "0.000000002"), (10, 8)),
        ((True, False), (True, False)),
        ((1.25, "2.75"), (10.5, 6.5)),
    ],
)
def test_metric_empty_sparse_extreme_and_accepted_conversion_parity(
    ratings, nps
) -> None:
    from decimal import Context, Inexact, InvalidOperation

    from dotmac_surveys import calculate_metrics

    context = Context(prec=5, Emin=-4, Emax=4, clamp=1)
    context.traps[InvalidOperation] = False
    context.flags[Inexact] = True
    assert _metric_outcome(calculate_metrics, ratings, nps, context) == _metric_outcome(
        _reference_metrics, ratings, nps, context
    )


def test_metric_iterables_materialize_before_length_check_and_decimal_conversion() -> (
    None
):
    from dotmac_surveys import calculate_metrics

    seen: list[str] = []

    def ratings():
        seen.append("ratings")
        yield "not a Decimal"

    def nps():
        seen.append("nps")
        yield 10
        yield 9

    with pytest.raises(ValueError, match="same responses"):
        calculate_metrics(ratings=ratings(), nps_values=nps())
    assert seen == ["ratings", "nps"]


def test_metric_decimal_conversions_all_finish_before_rating_arithmetic() -> None:
    from decimal import InvalidOperation, localcontext

    from dotmac_surveys import calculate_metrics

    with localcontext() as context:
        context.clear_flags()
        context.prec = 1
        context.traps[InvalidOperation] = True
        with pytest.raises(TypeError):
            calculate_metrics(ratings=(10**100, object()), nps_values=(None, None))
        assert not context.flags[InvalidOperation]


def test_metric_rating_trap_precedes_nps_comparison_error() -> None:
    from decimal import InvalidOperation, localcontext

    from dotmac_surveys import calculate_metrics

    with localcontext() as context:
        context.prec = 1
        context.traps[InvalidOperation] = True
        with pytest.raises(InvalidOperation):
            calculate_metrics(ratings=(5,), nps_values=(object(),))
        assert context.flags[InvalidOperation]


def test_metric_rating_flags_are_visible_before_nps_comparison_failure() -> None:
    from decimal import Context, InvalidOperation

    from dotmac_surveys import calculate_metrics

    context = Context(prec=3)
    context.traps[InvalidOperation] = False
    ratings = (1, 2, 2)
    nps = (10, object(), 0)
    assert _metric_outcome(calculate_metrics, ratings, nps, context) == _metric_outcome(
        _reference_metrics, ratings, nps, context
    )


def test_metric_native_batch_is_consumed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from dotmac_surveys import calculate_metrics

    native = _native.calculate_metrics
    calls = []

    def spy(*args, **kwargs):
        calls.append(1)
        return native(*args, **kwargs)

    monkeypatch.setattr(lifecycle._native, "calculate_metrics", spy)
    result = calculate_metrics(ratings=(5, 4, None), nps_values=(10, 0, None))
    assert str(result.avg_rating) == "4.50"
    assert str(result.nps_score) == "0.00"
    assert calls == [1]


@pytest.mark.parametrize("token", ["NaN", "sNaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("trapped", [False, True])
def test_metric_nps_decimal_comparison_signals_follow_rating_phase(
    token: str, trapped: bool
) -> None:
    from decimal import Context, Decimal, InvalidOperation

    from dotmac_surveys import calculate_metrics

    context = Context(prec=6)
    context.traps[InvalidOperation] = trapped
    ratings = (1, 2, 2)
    nps = (10, Decimal(token), 0)
    assert _metric_outcome(calculate_metrics, ratings, nps, context) == _metric_outcome(
        _reference_metrics, ratings, nps, context
    )


def test_metric_float_conversion_trap_precedes_arithmetic_and_nps_errors() -> None:
    from decimal import FloatOperation, InvalidOperation, localcontext

    from dotmac_surveys import calculate_metrics

    with localcontext() as context:
        context.clear_flags()
        context.prec = 1
        context.traps[FloatOperation] = True
        with pytest.raises(FloatOperation):
            calculate_metrics(ratings=(10**100, 1.25), nps_values=(object(), None))
        assert context.flags[FloatOperation]
        assert not context.flags[InvalidOperation]


@pytest.mark.parametrize("mutation", ["precision", "trap", "replace_context"])
def test_metric_nps_uses_ambient_context_after_rich_comparisons(mutation: str) -> None:
    from decimal import Context, Inexact, getcontext, setcontext

    from dotmac_surveys import calculate_metrics

    class ContextChangingInt(int):
        def __ge__(self, other):
            if mutation == "precision":
                getcontext().prec = 1
            elif mutation == "trap":
                getcontext().traps[Inexact] = True
            else:
                replacement = getcontext().copy()
                replacement.prec = 1
                setcontext(replacement)
            return super().__ge__(other)

    context = Context(prec=28)
    ratings = (None, None, None)
    nps = (ContextChangingInt(10), 9, 7)
    assert _metric_outcome(calculate_metrics, ratings, nps, context) == _metric_outcome(
        _reference_metrics, ratings, nps, context
    )
