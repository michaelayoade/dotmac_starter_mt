"""Pure Survey lifecycle, answer validation, and aggregate calculations."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import Decimal

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
    submitted: dict[str, str] = {}
    for answer in answers:
        if answer.key in submitted:
            raise InvalidAnswer(f'question "{answer.key}" was answered more than once')
        submitted[answer.key] = answer.value

    known = {question.key for question in questions}
    unknown = sorted(set(submitted) - known)
    if unknown:
        raise InvalidAnswer(f"response contains unknown question(s): {unknown}")

    normalized: dict[str, str] = {}
    rating: int | None = None
    nps_value: int | None = None
    for question in questions:
        value = submitted.get(question.key, "").strip()
        if not value:
            if question.required:
                raise InvalidAnswer(f'an answer is required for "{question.label}"')
            continue
        if question.type is QuestionType.RATING:
            try:
                numeric = int(value)
            except ValueError as exc:
                raise InvalidAnswer(
                    "rating must be an integer from 1 through 5"
                ) from exc
            if str(numeric) != value or not 1 <= numeric <= 5:
                raise InvalidAnswer("rating must be an integer from 1 through 5")
            rating = numeric
        elif question.type is QuestionType.NPS:
            try:
                numeric = int(value)
            except ValueError as exc:
                raise InvalidAnswer("NPS must be an integer from 0 through 10") from exc
            if str(numeric) != value or not 0 <= numeric <= 10:
                raise InvalidAnswer("NPS must be an integer from 0 through 10")
            nps_value = numeric
        elif question.type is QuestionType.MULTIPLE_CHOICE:
            if value not in question.options:
                raise InvalidAnswer(
                    f'choose a configured option for "{question.label}"'
                )
        elif len(value) > 10_000:
            raise InvalidAnswer(
                f'answer for "{question.label}" cannot exceed 10000 characters'
            )
        normalized[question.key] = value
    return ValidatedResponse(normalized, rating, nps_value)


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
