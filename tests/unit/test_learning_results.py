"""Pure contracts; database behavior is qualified in hosted PostgreSQL tests."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_learning.result_contracts import AdmissionObservation, GradedResult


def test_admission_observation_is_frozen_and_has_no_transaction_capability():
    observation = AdmissionObservation(
        tenant_id=uuid4(),
        person_id=uuid4(),
        activity_id=uuid4(),
        course_id=uuid4(),
        max_attempts=2,
        used=1,
        activity_type="mcq_test",
        grading="auto",
        pass_threshold=0.7,
        assessment_mode="graded",
        bank_id=None,
        question_count=None,
    )
    with pytest.raises(FrozenInstanceError):
        observation.used = 0
    for capability in (
        "commit",
        "rollback",
        "connection",
        "get_transaction",
        "execute",
    ):
        with pytest.raises(AttributeError):
            getattr(observation, capability)
    assert observation.max_attempts == 2


def test_external_grade_preserves_fraction_and_feedback_without_regrading():
    grade = GradedResult(
        score=1,
        max_score=3,
        fraction=0.333333333333,
        passed=True,
        per_item=[{"ext_id": "q", "correct": True}],
    )
    assert grade.fraction == 0.333333333333
    assert grade.passed is True
    assert grade.per_item == [{"ext_id": "q", "correct": True}]


@pytest.mark.parametrize(
    "field,value", [("source", "override"), ("override_reason", "forged")]
)
def test_external_grading_facts_cannot_claim_override_provenance(field, value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GradedResult.model_validate(
            {"score": 4, "max_score": 5, "fraction": 0.8, "passed": True, field: value}
        )
