"""Pure grading canaries ported from Academy 0968881."""

from dotmac_learning.grading import grade_submission


def test_numeric_exact_match():
    qs = [
        {"ext_id": "n1", "type": "numeric", "correct": 42, "options": {}, "weight": 1}
    ]
    r = grade_submission({"n1": ["42"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is True
    assert r.score == 1.0


def test_numeric_within_tolerance():
    qs = [
        {
            "ext_id": "n1",
            "type": "numeric",
            "correct": 10.0,
            "options": {"tolerance": 0.5},
            "weight": 1,
        }
    ]
    r = grade_submission({"n1": ["10.3"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is True


def test_numeric_outside_tolerance():
    qs = [
        {
            "ext_id": "n1",
            "type": "numeric",
            "correct": 10.0,
            "options": {"tolerance": 0.5},
            "weight": 1,
        }
    ]
    r = grade_submission({"n1": ["11.0"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is False


def test_numeric_zero_tolerance_is_default():
    qs = [{"ext_id": "n1", "type": "numeric", "correct": 5, "options": {}, "weight": 1}]
    r = grade_submission({"n1": ["5.1"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is False


def test_numeric_blank_is_wrong():
    qs = [
        {"ext_id": "n1", "type": "numeric", "correct": 42, "options": {}, "weight": 1}
    ]
    r = grade_submission({"n1": []}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is False


def test_numeric_nonnumeric_is_wrong():
    qs = [
        {"ext_id": "n1", "type": "numeric", "correct": 42, "options": {}, "weight": 1}
    ]
    r = grade_submission({"n1": ["hello"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is False


def test_numeric_correct_as_list():
    """correct stored as [42] (list form) should also work."""
    qs = [
        {"ext_id": "n1", "type": "numeric", "correct": [42], "options": {}, "weight": 1}
    ]
    r = grade_submission({"n1": ["42"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is True


def test_short_text_exact_match():
    qs = [
        {
            "ext_id": "s1",
            "type": "short_text",
            "correct": ["Paris"],
            "options": {},
            "weight": 1,
        }
    ]
    r = grade_submission({"s1": ["Paris"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is True


def test_short_text_case_insensitive():
    qs = [
        {
            "ext_id": "s1",
            "type": "short_text",
            "correct": ["Paris"],
            "options": {},
            "weight": 1,
        }
    ]
    r = grade_submission({"s1": ["paris"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is True


def test_short_text_trim():
    qs = [
        {
            "ext_id": "s1",
            "type": "short_text",
            "correct": ["Paris"],
            "options": {},
            "weight": 1,
        }
    ]
    r = grade_submission({"s1": ["  Paris  "]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is True


def test_short_text_nonmatch():
    qs = [
        {
            "ext_id": "s1",
            "type": "short_text",
            "correct": ["Paris"],
            "options": {},
            "weight": 1,
        }
    ]
    r = grade_submission({"s1": ["London"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is False


def test_short_text_multiple_accepted():
    qs = [
        {
            "ext_id": "s1",
            "type": "short_text",
            "correct": ["TCP", "Transmission Control Protocol"],
            "options": {},
            "weight": 1,
        }
    ]
    r = grade_submission(
        {"s1": ["transmission control protocol"]}, qs, 0.5, missing_question_weight=1
    )
    assert r.per_item[0]["correct"] is True


def test_short_text_blank_is_wrong():
    qs = [
        {
            "ext_id": "s1",
            "type": "short_text",
            "correct": ["Paris"],
            "options": {},
            "weight": 1,
        }
    ]
    r = grade_submission({"s1": []}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is False


def test_short_text_regex_match():
    qs = [
        {
            "ext_id": "s1",
            "type": "short_text",
            "correct": [r"\d{3}-\d{4}"],
            "options": {"regex": True},
            "weight": 1,
        }
    ]
    r = grade_submission({"s1": ["555-1234"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is True


def test_short_text_regex_no_match():
    qs = [
        {
            "ext_id": "s1",
            "type": "short_text",
            "correct": [r"\d{3}-\d{4}"],
            "options": {"regex": True},
            "weight": 1,
        }
    ]
    r = grade_submission({"s1": ["abc"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is False


def test_short_text_bad_regex_counts_as_wrong():
    qs = [
        {
            "ext_id": "s1",
            "type": "short_text",
            "correct": ["[invalid"],
            "options": {"regex": True},
            "weight": 1,
        }
    ]
    r = grade_submission({"s1": ["anything"]}, qs, 0.5, missing_question_weight=1)
    assert r.per_item[0]["correct"] is False


def test_mixed_bank_all_correct():
    qs = [
        {
            "ext_id": "q1",
            "type": "single",
            "correct": ["A"],
            "options": ["A", "B"],
            "weight": 1,
        },
        {"ext_id": "q2", "type": "numeric", "correct": 10, "options": {}, "weight": 1},
        {
            "ext_id": "q3",
            "type": "short_text",
            "correct": ["TCP"],
            "options": {},
            "weight": 1,
        },
    ]
    r = grade_submission(
        {"q1": ["A"], "q2": ["10"], "q3": ["tcp"]}, qs, 0.9, missing_question_weight=1
    )
    assert r.score == 3.0
    assert r.fraction == 1.0
    assert r.passed is True


def test_mixed_bank_partial():
    qs = [
        {
            "ext_id": "q1",
            "type": "single",
            "correct": ["A"],
            "options": ["A", "B"],
            "weight": 1,
        },
        {"ext_id": "q2", "type": "numeric", "correct": 10, "options": {}, "weight": 1},
        {
            "ext_id": "q3",
            "type": "short_text",
            "correct": ["TCP"],
            "options": {},
            "weight": 1,
        },
    ]
    # q1 correct, q2 wrong (off by 1, no tolerance), q3 wrong
    r = grade_submission(
        {"q1": ["A"], "q2": ["11"], "q3": ["UDP"]}, qs, 0.9, missing_question_weight=1
    )
    assert r.score == 1.0
    assert r.max_score == 3.0
    assert abs(r.fraction - 1 / 3) < 1e-9
    assert r.passed is False


# Additional source JSON/contract boundary canaries. No input validation is added.
def test_grade_binds_existing_result_contract_and_all_five_fields():
    from dotmac_learning.result_contracts import GradedResult

    result = grade_submission(
        {"q": ["A", "A"]},
        [{"ext_id": "q", "correct": ["A"], "weight": "2"}],
        1.0,
        missing_question_weight=1,
    )
    assert isinstance(result, GradedResult)
    assert result.model_dump() == {
        "score": 2.0,
        "max_score": 2.0,
        "fraction": 1.0,
        "passed": True,
        "per_item": [
            {
                "id": "q",
                "correct": True,
                "chosen": ["A", "A"],
                "expected": ["A"],
                "weight": "2",
            }
        ],
    }


def test_numeric_reversed_range_inclusive_and_only_first_two_bounds():
    questions = [{"ext_id": "n", "type": "numeric", "correct": [5, 1, 100]}]
    assert grade_submission(
        {"n": ["1"]}, questions, 1, missing_question_weight=1
    ).passed
    assert grade_submission(
        {"n": ["5"]}, questions, 1, missing_question_weight=1
    ).passed
    assert not grade_submission(
        {"n": ["100"]}, questions, 1, missing_question_weight=1
    ).passed


def test_invalid_tolerance_falls_back_to_zero_and_ignores_range():
    questions = [
        {
            "ext_id": "n",
            "type": "numeric",
            "correct": [10, 20],
            "options": {"tolerance": "invalid"},
        }
    ]
    assert grade_submission(
        {"n": ["10"]}, questions, 1, missing_question_weight=1
    ).passed
    assert not grade_submission(
        {"n": ["15"]}, questions, 1, missing_question_weight=1
    ).passed


def test_short_text_collapses_internal_whitespace_and_scalar_correct():
    result = grade_submission(
        {"s": [" TCP   protocol "]},
        [{"ext_id": "s", "type": "short_text", "correct": "tcp protocol"}],
        1,
        missing_question_weight=1,
    )
    assert result.passed
    assert result.per_item[0]["expected"] == ["tcp protocol"]


def test_scalar_answer_string_preserves_source_list_conversion():
    result = grade_submission(
        {"s": "AB"},
        [{"ext_id": "s", "correct": ["A", "B"]}],
        1,
        missing_question_weight=1,
    )
    assert result.passed
    assert result.per_item[0]["chosen"] == ["A", "B"]


def test_null_correct_is_scalar_and_null_options_use_source_fallback():
    result = grade_submission(
        {"s": [None]},
        [{"ext_id": "s", "correct": None, "options": None}],
        1,
        missing_question_weight=1,
    )
    assert result.passed
    assert result.per_item[0]["expected"] == [None]


def test_null_answer_and_unhashable_set_values_keep_source_errors():
    import pytest

    with pytest.raises(TypeError):
        grade_submission({"s": None}, [{"ext_id": "s"}], 1, missing_question_weight=1)
    with pytest.raises(TypeError):
        grade_submission(
            {"s": [["A"]]},
            [{"ext_id": "s", "correct": ["A"]}],
            1,
            missing_question_weight=1,
        )


def test_zero_maximum_and_empty_bank_never_pass():
    for result in (
        grade_submission({}, [], 0, missing_question_weight=1),
        grade_submission(
            {"s": ["A"]},
            [{"ext_id": "s", "correct": ["A"], "weight": 0}],
            0,
            missing_question_weight=1,
        ),
    ):
        assert result.score == result.max_score == result.fraction == 0
        assert not result.passed


def test_fraction_uses_weight_and_inclusive_threshold():
    questions = [
        {"ext_id": "a", "correct": ["A"], "weight": 3},
        {"ext_id": "b", "correct": ["B"], "weight": 1},
    ]
    result = grade_submission({"a": ["A"]}, questions, 0.75, missing_question_weight=1)
    assert (result.score, result.max_score, result.fraction, result.passed) == (
        3,
        4,
        0.75,
        True,
    )
