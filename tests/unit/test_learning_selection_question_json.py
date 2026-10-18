"""Hosted unit canaries for stored-JSON selection inputs and existing pure logic."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import dotmac_learning as learning
import pytest
from pydantic import ValidationError


@pytest.mark.parametrize(
    "options",
    [
        None,
        [],
        ["alpha", "bravo"],
        {"tolerance": 0.5},
        {"regex": True},
        {"nested": [None, True, 7, 1.25, "value", {"key": []}]},
        7,
        1.25,
        True,
        "text",
    ],
)
def test_selection_command_accepts_json_shapes_and_deep_copy_preserves_types(options):
    question = learning.SelectionQuestion(ext_id="q", options=options)
    command = learning.OpenAttemptCommand(
        tenant_id=uuid4(),
        person_id=uuid4(),
        activity_id=uuid4(),
        all_ext_ids=["q"],
        count=1,
        questions=(question,),
    )
    copied = command.model_copy(deep=True)
    original = deepcopy(options)
    assert type(question.options) is type(options)
    assert question.options == original
    assert type(copied.questions[0].options) is type(options)
    assert copied.questions[0].options == original
    if isinstance(copied.questions[0].options, dict):
        copied.questions[0].options["changed"] = [False]
    elif isinstance(copied.questions[0].options, list):
        copied.questions[0].options.append("changed")
    assert question.options == command.questions[0].options == original


def test_selection_json_default_and_non_json_rejection():
    assert learning.SelectionQuestion(ext_id="q").options is None
    with pytest.raises(ValidationError):
        learning.SelectionQuestion(ext_id="q", options=object())


def test_pure_selection_ignores_options_and_keeps_existing_list_presentation():
    sitter = SimpleNamespace(id="cand-alpha")
    configs = ({"tolerance": 0.5}, {"regex": True}, ["alpha", "bravo"])
    mixed = [
        learning.SelectionQuestion(ext_id=f"q{i}", category="skill", options=o)
        for i, o in enumerate(configs)
    ]
    ids_only = [
        learning.SelectionQuestion(ext_id=q.ext_id, category=q.category) for q in mixed
    ]
    policy = learning.SelectionPolicy(total=2)
    for variant in ("", "attempt0:", "attempt1:"):
        selected = learning.select_questions(sitter, mixed, policy, variant=variant)
        expected = learning.select_questions(sitter, ids_only, policy, variant=variant)
        assert [q.ext_id for q in selected] == [q.ext_id for q in expected]
        assert all(any(q is original for original in mixed) for q in selected)
    listed = learning.SelectionQuestion(
        ext_id="numeracy-00", options=["alpha", "bravo", "charlie", "delta"]
    )
    assert learning.present_options(sitter, listed) == [
        "delta",
        "alpha",
        "bravo",
        "charlie",
    ]
    assert listed.options == ["alpha", "bravo", "charlie", "delta"]


def test_dictionary_configs_keep_source_chance_and_iteration_semantics():
    numeric = learning.SelectionQuestion(ext_id="numeric", options={"tolerance": 0.5})
    regex = learning.SelectionQuestion(ext_id="text", options={"regex": True})
    listed = learning.SelectionQuestion(ext_id="single", options=["a", "b", "c", "d"])
    assert learning.chance_baseline(
        [numeric, regex, listed], fallback_baseline=(1 / 3) / 1.33
    ) == pytest.approx(0.25)
    assert learning.chance_baseline(
        [numeric, regex], fallback_baseline=(1 / 3) / 1.33
    ) == pytest.approx((1 / 3) / 1.33)
    sitter = SimpleNamespace(id="candidate")
    assert learning.present_options(sitter, numeric) == ["tolerance"]
    assert learning.present_options(sitter, regex) == ["regex"]
    assert (
        learning.present_options(
            sitter, learning.SelectionQuestion(ext_id="empty", options=None)
        )
        == []
    )
    with pytest.raises(TypeError):
        learning.present_options(
            sitter, learning.SelectionQuestion(ext_id="bad", options=7)
        )


def test_numeric_tolerance_and_short_text_regex_reach_source_grader_unchanged():
    configs = ({"tolerance": 0.5}, {"regex": True})
    questions = [
        learning.SelectionQuestion(ext_id="numeric", options=configs[0]),
        learning.SelectionQuestion(ext_id="text", options=configs[1]),
    ]
    graded = learning.grade_submission(
        {"numeric": ["42.25"], "text": ["CODE-123"]},
        [
            {
                "ext_id": questions[0].ext_id,
                "type": "numeric",
                "correct": [42],
                "options": questions[0].options,
            },
            {
                "ext_id": questions[1].ext_id,
                "type": "short_text",
                "correct": [r"code-\d+"],
                "options": questions[1].options,
            },
        ],
        pass_threshold=1.0,
        missing_question_weight=1,
    )
    assert graded.fraction == 1.0 and graded.passed
    assert [q.options for q in questions] == list(configs)
