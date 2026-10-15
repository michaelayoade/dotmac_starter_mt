"""Pure Survey lifecycle, answer validation, and aggregate calculations."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

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


def calculate_metrics(
    *,
    ratings: Iterable[int | None],
    nps_values: Iterable[int | None],
) -> SurveyMetrics:
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
    nps_score: Decimal | None = None
    if present_nps:
        promoters = sum(value >= 9 for value in present_nps)
        detractors = sum(value <= 6 for value in present_nps)
        nps_score = (
            (Decimal(promoters - detractors) / Decimal(len(present_nps))) * Decimal(100)
        ).quantize(Decimal("0.01"))
    return SurveyMetrics(len(rating_rows), average, nps_score)


__all__ = ["calculate_metrics", "transition_survey", "validate_answers"]
