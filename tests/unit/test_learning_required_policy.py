"""Required policy inputs and raw material preservation; hosted execution only."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotmac_learning.bank_activity_contracts import BankActivityTerms
from dotmac_learning.curriculum_contracts import (
    BankQuestion,
    CourseCreate,
    ManualImportHeader,
)
from dotmac_learning.exam_engine import (
    INVALID_NEAR_CHANCE,
    INVALID_TOO_FAST,
    ValidityTerms,
    check_validity,
    duration_floor,
    near_chance_floor,
)
from dotmac_learning.grading import grade_submission
from dotmac_learning.references import (
    ActivityPatch,
    ActivityWrite,
    CoursePatch,
    CourseWrite,
)
from pydantic import ValidationError


def test_create_policy_is_required_and_patch_omission_stays_absent():
    identity = {"id": uuid4(), "tenant_id": uuid4()}
    course = dict(**identity, slug="x", title="X", discipline="x", source_ref="x")
    activity = dict(**identity, course_id=uuid4(), title="X")
    for model, values in ((CourseWrite, course), (ActivityWrite, activity)):
        with pytest.raises(ValidationError):
            model(**values)
    c = CourseWrite(**course, status="custom", listed=True)
    a = ActivityWrite(
        **activity,
        type="lab",
        pass_threshold=0.81,
        assessment_mode="exam",
        grading="manual",
        weight=2.75,
    )
    assert (c.status, c.listed) == ("custom", True)
    assert (a.type, a.pass_threshold, a.assessment_mode, a.grading, a.weight) == (
        "lab",
        0.81,
        "exam",
        "manual",
        2.75,
    )
    for model in (CoursePatch, ActivityPatch):
        assert model(**identity).model_dump(exclude_unset=True) == identity
    for model, values in (
        (
            CourseCreate,
            {"tenant_id": uuid4(), "slug": "x", "title": "x", "discipline": "x"},
        ),
        (
            ManualImportHeader,
            {
                "tenant_id": uuid4(),
                "slug": "x",
                "title": "x",
                "discipline": "x",
                "source_ref": "x",
            },
        ),
        (
            BankQuestion,
            {"id": "x", "stem": "x", "type": "numeric", "rubric_category": "recall"},
        ),
    ):
        with pytest.raises(ValidationError):
            model(**values)


@pytest.mark.parametrize("bad", [True, "1", float("nan"), float("inf"), -1])
def test_validity_terms_reject_nonfinite_wrong_types_and_negative_timing(bad):
    with pytest.raises((TypeError, ValueError)):
        ValidityTerms(bad, 1.4, 0.2, 8.0)


def test_configured_validity_tracks_paper_and_preserves_no_mcq_fallback():
    terms = ValidityTerms(2.5, 1.2, 0.18, 17.0)
    paper = [
        SimpleNamespace(options=["yes", "no"]),
        SimpleNamespace(options=[1, 2, 3, 4]),
    ]
    assert duration_floor(paper, seconds_per_question=terms.seconds_per_question) == 5.0
    assert near_chance_floor(paper, terms=terms) == pytest.approx(0.45)
    assert check_validity(0.46, 5.0, terms=terms, questions=paper) == (True, None)
    assert check_validity(0.45, 5.0, terms=terms, questions=paper) == (
        False,
        INVALID_NEAR_CHANCE,
    )
    for paper in (
        None,
        [],
        [SimpleNamespace(options=None)],
        [SimpleNamespace(options={"tolerance": 1})],
    ):
        assert check_validity(0.18, 20, terms=terms, questions=paper) == (
            False,
            INVALID_NEAR_CHANCE,
        )
    assert check_validity(0.9, 16, terms=terms) == (False, INVALID_TOO_FAST)
    assert check_validity(0.9, 17, terms=terms) == (True, None)
    with pytest.raises(FrozenInstanceError):
        terms.near_chance_margin = 2


def test_bank_activity_terms_copy_and_freeze_caller_mapping():
    source = {"final": 0.91}
    terms = BankActivityTerms(source, 0.27, "practice", "manual", 2.5, "best", 0)
    source["final"] = 0.1
    assert terms.pass_threshold_by_kind["final"] == 0.91
    assert (
        terms.pass_threshold_by_kind.get("unknown", terms.fallback_pass_threshold)
        == 0.27
    )
    with pytest.raises(TypeError):
        terms.pass_threshold_by_kind["final"] = 0.2
    with pytest.raises(FrozenInstanceError):
        terms.new_activity_weight = 1


def test_grader_missing_weight_is_injected_without_normalizing_explicit_raw_values():
    raw = [
        {"ext_id": "missing", "correct": ["A"], "options": ["A", "B"]},
        {"ext_id": "odd", "correct": ["A"], "weight": "2.5", "options": [True, None]},
        {"ext_id": "negative", "correct": [], "weight": -2},
    ]
    result = grade_submission(
        {"missing": ["A"], "odd": ["A"]}, raw, 0.8, missing_question_weight=7
    )
    assert result.max_score == 7.5
    assert result.score == 7.5
    assert [item["weight"] for item in result.per_item] == [7, "2.5", -2]
    assert "weight" not in raw[0] and raw[1]["weight"] == "2.5"
    with pytest.raises(ValueError, match="missing_question_weight"):
        grade_submission({}, [{"broken": object()}], 0, missing_question_weight=True)


def test_required_reference_fields_preserve_explicit_legacy_values_and_mutability():
    identity = {"id": uuid4(), "tenant_id": uuid4()}
    course = CourseWrite(
        **identity,
        slug="x",
        title="X",
        discipline="x",
        source_ref="x",
        status="",
        listed=1,
    )
    activity = ActivityWrite(
        **identity,
        course_id=uuid4(),
        title="X",
        type="",
        pass_threshold="1.1",
        assessment_mode="",
        grading="",
        weight="0",
    )
    assert (course.status, course.listed) == ("", True)
    assert (
        activity.type,
        activity.pass_threshold,
        activity.assessment_mode,
        activity.grading,
        activity.weight,
    ) == ("", 1.1, "", "", 0.0)
    course.status = "changed"
    activity.weight = -1.0
    assert course.status == "changed" and activity.weight == -1.0
    patch = ActivityPatch(**identity, pass_threshold="1.1", weight="0")
    assert (patch.pass_threshold, patch.weight) == (1.1, 0.0)


@pytest.mark.parametrize(
    "supplied,expected", [(0, 0), (-1, -1), ("2", 2), (2.0, 2), (True, 1)]
)
def test_required_normalized_question_weight_keeps_original_typed_coercion(
    supplied, expected
):
    question = BankQuestion(
        id="q", stem="Q", type="numeric", rubric_category="recall", weight=supplied
    )
    assert question.weight == expected and type(question.weight) is int


@pytest.mark.parametrize(
    "supplied,expected", [(0, 0), (-1, -1), ("2", 2), (2.0, 2), (True, 1)]
)
def test_required_grant_count_keeps_original_typed_coercion(supplied, expected):
    from dotmac_learning.attempt_contracts import GrantExtraAttemptCommand

    command = GrantExtraAttemptCommand(
        tenant_id=uuid4(),
        person_id=uuid4(),
        activity_id=uuid4(),
        reason="power",
        extra_attempts=supplied,
    )
    assert command.extra_attempts == expected and type(command.extra_attempts) is int
    with pytest.raises(ValidationError):
        GrantExtraAttemptCommand(
            tenant_id=uuid4(), person_id=uuid4(), activity_id=uuid4(), reason="power"
        )
