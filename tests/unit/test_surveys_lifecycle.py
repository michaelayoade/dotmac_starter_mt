"""Behavior canaries ported from Sub's authoritative Survey owner."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from dotmac_surveys import (
    Answer,
    InvalidAnswer,
    InvalidSurveyDefinition,
    InvalidSurveyTransition,
    Question,
    QuestionType,
    StaleSurveyState,
    SurveyDefinition,
    SurveyStatus,
    calculate_metrics,
    transition_survey,
    validate_answers,
)


def test_definition_normalizes_identity_and_preserves_question_order() -> None:
    definition = SurveyDefinition(
        name="  Customer feedback  ",
        description="   ",
        public_slug=" Customer_Feedback ",
        thank_you_message="  Thank you  ",
        questions=(
            Question("rating", QuestionType.RATING, " Rate us "),
            Question("comment", QuestionType.FREE_TEXT, " Comments ", required=False),
        ),
    )

    assert definition.name == "Customer feedback"
    assert definition.description is None
    assert definition.public_slug == "customer-feedback"
    assert definition.thank_you_message == "Thank you"
    assert [question.key for question in definition.questions] == [
        "rating",
        "comment",
    ]
    assert definition.questions[0].label == "Rate us"


@pytest.mark.parametrize(
    "slug",
    ("-leading", "trailing-", "repeated--hyphen", "unsafe/slash"),
)
def test_definition_rejects_malformed_public_slugs(slug: str) -> None:
    with pytest.raises(InvalidSurveyDefinition, match="public slug"):
        SurveyDefinition(name="Feedback", public_slug=slug)


def test_question_contract_rejects_duplicate_keys_and_invalid_choices() -> None:
    with pytest.raises(InvalidSurveyDefinition, match="duplicated"):
        SurveyDefinition(
            name="Feedback",
            questions=(
                Question("q1", QuestionType.RATING, "First"),
                Question("q1", QuestionType.FREE_TEXT, "Second"),
            ),
        )

    with pytest.raises(InvalidSurveyDefinition, match="2 to 50"):
        Question(
            "choice",
            QuestionType.MULTIPLE_CHOICE,
            "Choose",
            options=("Only one",),
        )

    with pytest.raises(InvalidSurveyDefinition, match="unique"):
        Question(
            "choice",
            QuestionType.MULTIPLE_CHOICE,
            "Choose",
            options=("Yes", " yes "),
        )


def test_non_choice_question_discards_irrelevant_options() -> None:
    question = Question(
        "rating",
        QuestionType.RATING,
        "Rate us",
        options=("discard", "these"),
    )

    assert question.options == ()


def test_one_aggregate_never_mixes_two_rating_question_series() -> None:
    with pytest.raises(InvalidSurveyDefinition, match="at most one"):
        SurveyDefinition(
            name="Ambiguous feedback",
            questions=(
                Question("speed", QuestionType.RATING, "Rate speed"),
                Question("support", QuestionType.RATING, "Rate support"),
            ),
        )


def test_lifecycle_requires_questions_and_refuses_stale_or_terminal_changes() -> None:
    with pytest.raises(InvalidSurveyTransition, match="at least one"):
        transition_survey(
            SurveyStatus.DRAFT,
            SurveyStatus.ACTIVE,
            expected=SurveyStatus.DRAFT,
            has_questions=False,
        )

    with pytest.raises(StaleSurveyState, match="expected paused"):
        transition_survey(
            SurveyStatus.DRAFT,
            SurveyStatus.ACTIVE,
            expected=SurveyStatus.PAUSED,
            has_questions=True,
        )

    with pytest.raises(InvalidSurveyTransition, match="terminal"):
        transition_survey(
            SurveyStatus.CLOSED,
            SurveyStatus.ACTIVE,
            expected=SurveyStatus.CLOSED,
            has_questions=True,
        )


def test_paused_survey_may_reactivate_but_closed_survey_may_not() -> None:
    assert (
        transition_survey(
            SurveyStatus.PAUSED,
            SurveyStatus.ACTIVE,
            expected=SurveyStatus.PAUSED,
            has_questions=True,
        )
        is SurveyStatus.ACTIVE
    )


def test_answer_validation_is_authoritative() -> None:
    questions = (
        Question("rating", QuestionType.RATING, "Rate us"),
        Question("recommend", QuestionType.NPS, "Recommend us", required=False),
        Question(
            "reason",
            QuestionType.MULTIPLE_CHOICE,
            "Why?",
            options=("Speed", "Support"),
        ),
        Question("comment", QuestionType.FREE_TEXT, "Comment", required=False),
    )

    with pytest.raises(InvalidAnswer, match="required"):
        validate_answers(questions, ())
    with pytest.raises(InvalidAnswer, match="unknown"):
        validate_answers(questions, (Answer("missing", "value"),))
    with pytest.raises(InvalidAnswer, match="1 through 5"):
        validate_answers(
            questions,
            (
                Answer("rating", "0"),
                Answer("reason", "Speed"),
            ),
        )

    reviewed = validate_answers(
        questions,
        (
            Answer("rating", "5"),
            Answer("recommend", "10"),
            Answer("reason", "Support"),
            Answer("comment", "  Helpful team  "),
        ),
    )

    assert reviewed.answers == {
        "rating": "5",
        "recommend": "10",
        "reason": "Support",
        "comment": "Helpful team",
    }
    assert reviewed.rating == 5
    assert reviewed.nps_value == 10


def test_metrics_use_exact_decimal_arithmetic() -> None:
    metrics = calculate_metrics(
        ratings=(5, 4, None, None, None),
        nps_values=(10, 9, 6, 7, None),
    )

    assert metrics.total_responses == 5
    assert metrics.avg_rating == Decimal("4.50")
    assert metrics.nps_score == Decimal("25.00")


def test_definition_expiry_requires_timezone_aware_instants() -> None:
    with pytest.raises(InvalidSurveyDefinition, match="timezone-aware"):
        SurveyDefinition(
            name="Feedback",
            expires_at=datetime(2026, 8, 19),
        )

    definition = SurveyDefinition(
        name="Feedback",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    assert definition.expires_at is not None
