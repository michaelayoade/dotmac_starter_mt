"""Pure Survey lifecycle, answer validation, and aggregate calculations."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Clamped,
    Context,
    ConversionSyntax,
    Decimal,
    DecimalException,
    DivisionByZero,
    DivisionImpossible,
    DivisionUndefined,
    FloatOperation,
    Inexact,
    InvalidContext,
    InvalidOperation,
    Overflow,
    Rounded,
    Subnormal,
    Underflow,
    getcontext,
)

from dotmac_surveys import _native
from dotmac_surveys.contracts import (
    Answer,
    InvalidAnswer,
    InvalidSurveyTransition,
    Question,
    QuestionType,
    StaleSurveyState,
    SurveyMetrics,
    SurveyStatus,
    ValidatedResponse,
)

_TRANSITIONS = {
    SurveyStatus.DRAFT: frozenset({SurveyStatus.ACTIVE, SurveyStatus.CLOSED}),
    SurveyStatus.ACTIVE: frozenset({SurveyStatus.PAUSED, SurveyStatus.CLOSED}),
    SurveyStatus.PAUSED: frozenset({SurveyStatus.ACTIVE, SurveyStatus.CLOSED}),
    SurveyStatus.CLOSED: frozenset(),
}


def transition_survey(
    current: SurveyStatus,
    requested: SurveyStatus,
    *,
    expected: SurveyStatus,
    has_questions: bool,
) -> SurveyStatus:
    if current is not expected:
        raise StaleSurveyState(
            f"survey state expected {expected.value}, found {current.value}"
        )
    if current is SurveyStatus.CLOSED:
        raise InvalidSurveyTransition("closed is terminal")
    if requested is SurveyStatus.ACTIVE and not has_questions:
        raise InvalidSurveyTransition("an active survey requires at least one question")
    if requested not in _TRANSITIONS[current]:
        raise InvalidSurveyTransition(
            f"cannot transition survey from {current.value} to {requested.value}"
        )
    return requested


def validate_answers(
    questions: Sequence[Question], answers: Iterable[Answer]
) -> ValidatedResponse:
    # Keep Python Unicode semantics, including lone surrogates, at the byte
    # bridge. Native code owns every validation decision and returns indices.
    question_rows = list(questions)
    primitive_questions: list[_native.Question] = [
        (
            question.key.encode("utf-8", "surrogatepass"),
            1
            if question.type is QuestionType.RATING
            else 2
            if question.type is QuestionType.NPS
            else 3
            if question.type is QuestionType.MULTIPLE_CHOICE
            else 0,
            question.required,
            [option.encode("utf-8", "surrogatepass") for option in question.options],
        )
        for question in question_rows
    ]
    submitted: list[tuple[str, str]] = []

    def primitive_answers() -> Iterable[_native.Answer]:
        for answer in answers:
            value = answer.value.strip()
            submitted.append((answer.key, value))
            yield (
                answer.key.encode("utf-8", "surrogatepass"),
                value.encode("utf-8", "surrogatepass"),
                len(value),
            )

    normalized, rating, nps_value, error = _native.validate_batch(
        questions=primitive_questions, answers=primitive_answers()
    )
    if error is not None:
        code, indices = error
        if code == "duplicate":
            key = submitted[indices[0]][0]
            raise InvalidAnswer(f'question "{key}" was answered more than once')
        if code == "unknown":
            unknown = [submitted[index][0] for index in indices]
            raise InvalidAnswer(f"response contains unknown question(s): {unknown}")
        label = question_rows[indices[0]].label
        if code == "required":
            raise InvalidAnswer(f'an answer is required for "{label}"')
        if code == "rating":
            raise InvalidAnswer("rating must be an integer from 1 through 5")
        if code == "nps":
            raise InvalidAnswer("NPS must be an integer from 0 through 10")
        if code == "choice":
            raise InvalidAnswer(f'choose a configured option for "{label}"')
        if code == "length":
            raise InvalidAnswer(f'answer for "{label}" cannot exceed 10000 characters')
        raise RuntimeError(f"unsupported native answer validation result {code!r}")
    return ValidatedResponse(
        {
            question_rows[question_index].key: submitted[answer_index][1]
            for question_index, answer_index in normalized
        },
        rating,
        nps_value,
    )


# Shared decimal-core's wire order follows CPython signal/condition priority.
_SIGNALS = (
    InvalidOperation,
    FloatOperation,
    DivisionByZero,
    Overflow,
    Underflow,
    Subnormal,
    Inexact,
    Rounded,
    Clamped,
)
_INVALID_CONDITIONS = (
    InvalidOperation,
    ConversionSyntax,
    DivisionImpossible,
    DivisionUndefined,
    InvalidContext,
)
_ROUNDINGS = (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_UP,
    ROUND_DOWN,
    ROUND_HALF_UP,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_05UP,
)


def _replay_metric_status(
    context: Context, traps: int, status: int, failure: _native.ArithmeticFailure
) -> None:
    for index, signal in enumerate(_SIGNALS):
        if status & (1 << index):
            context.flags[signal] = True
    if status & (1 << 9):
        raise MemoryError
    if failure is None:
        return
    failed_signals, failed_conditions = failure
    trapped = [
        signal
        for index, signal in enumerate(_SIGNALS)
        if failed_signals & traps & (1 << index)
    ]
    if not trapped:
        return
    details: list[type[DecimalException]] = []
    for signal in trapped:
        if signal is InvalidOperation:
            details.extend(
                condition
                for index, condition in enumerate(_INVALID_CONDITIONS)
                if failed_conditions & (1 << index)
            )
            if not failed_conditions:
                details.append(InvalidOperation)
        else:
            details.append(signal)
    raise trapped[0](details)


def calculate_metrics(
    *,
    ratings: Iterable[int | None],
    nps_values: Iterable[int | None],
) -> SurveyMetrics:
    rating_rows = tuple(ratings)
    nps_rows = tuple(nps_values)
    if len(rating_rows) != len(nps_rows):
        raise ValueError("rating and NPS projections must describe the same responses")

    # Conversion errors and FloatOperation precede all arithmetic, as before.
    present_ratings = [Decimal(value) for value in rating_rows if value is not None]
    context = getcontext()
    traps = sum(
        1 << index for index, signal in enumerate(_SIGNALS) if context.traps[signal]
    )

    def predicates() -> Iterable[tuple[bool, bool]]:
        # These are lazy type-boundary comparisons, not metric arithmetic.
        # Consume all promoter comparisons before any detractor comparisons;
        # arbitrary-sized ints and existing rich-comparison errors stay exact.
        for value in nps_rows:
            if value is not None:
                yield True, value >= 9
        for value in nps_rows:
            if value is not None:
                yield False, value <= 6

    def replay_rating(status: int, failure: _native.ArithmeticFailure) -> None:
        _replay_metric_status(context, traps, status, failure)

    def capture_nps_context() -> tuple[_native.ContextSpec, int]:
        nonlocal context, traps
        # Rich comparisons may mutate or replace the active context. NPS
        # arithmetic and its final signal replay must observe that new context.
        context = getcontext()
        traps = sum(
            1 << index for index, signal in enumerate(_SIGNALS) if context.traps[signal]
        )
        return (
            (
                context.prec,
                _ROUNDINGS.index(context.rounding),
                context.Emin,
                context.Emax,
                context.clamp,
            ),
            traps,
        )

    average, score, status, failure = _native.calculate_metrics(
        ratings=[str(value) for value in present_ratings],
        nps_predicates=predicates(),
        context=(
            context.prec,
            _ROUNDINGS.index(context.rounding),
            context.Emin,
            context.Emax,
            context.clamp,
        ),
        traps=traps,
        replay_rating=replay_rating,
        capture_nps_context=capture_nps_context,
    )
    _replay_metric_status(context, traps, status, failure)
    return SurveyMetrics(
        len(rating_rows),
        Decimal(average) if average is not None else None,
        Decimal(score) if score is not None else None,
    )


__all__ = ["calculate_metrics", "transition_survey", "validate_answers"]
