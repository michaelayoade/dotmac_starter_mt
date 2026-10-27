"""Typed bank import intent preserves raw policy and returns immutable observations."""

from dataclasses import FrozenInstanceError, fields
from uuid import uuid4

import pytest
from dotmac_learning.bank_activity_contracts import (
    BankActivityImportResult,
    BankActivityTerms,
    ImportBankWithActivityCommand,
)
from dotmac_learning.curriculum_contracts import BankDocument
from pydantic import ValidationError


def test_command_uses_existing_bank_document_without_extra_policy_validation():
    doc = BankDocument(
        course="source-slug",
        kind="final",
        questions=(),
        policy={"pool": None, "mode": None, "pass_threshold": None},
    )
    tenant = uuid4()
    command = ImportBankWithActivityCommand(
        tenant_id=tenant, doc=doc, terms=BANK_ACTIVITY_TERMS, missing_question_weight=1
    )
    assert command.tenant_id == tenant and command.doc is doc
    assert command.doc.policy == doc.policy
    with pytest.raises(ValidationError):
        ImportBankWithActivityCommand(
            tenant_id=tenant,
            doc=doc,
            unknown=True,
            terms=BANK_ACTIVITY_TERMS,
            missing_question_weight=1,
        )


def test_result_is_frozen_and_does_not_require_activity_identity_or_defaults():
    result = BankActivityImportResult(
        bank_id=uuid4(),
        title="Final assessment",
        created=True,
        applied_policy=(("pool", None), ("max_attempts", 3), ("mode", "exam")),
    )
    assert {field.name for field in fields(result)} == {
        "bank_id",
        "title",
        "created",
        "applied_policy",
    }
    with pytest.raises(FrozenInstanceError):
        result.title = "changed"


BANK_ACTIVITY_TERMS = BankActivityTerms(
    pass_threshold_by_kind={"chapter": 0.0, "mid": 0.60, "final": 0.70},
    fallback_pass_threshold=0.0,
    new_activity_assessment_mode="graded",
    new_activity_grading="auto",
    new_activity_weight=1.0,
    new_activity_scoring_policy="best",
    new_activity_min_attempt_interval_seconds=0,
)


def test_bank_terms_reject_unknown_policy_and_out_of_range_cooldown():
    base = {
        "pass_threshold_by_kind": {},
        "fallback_pass_threshold": 0.0,
        "new_activity_assessment_mode": "graded",
        "new_activity_grading": "auto",
        "new_activity_weight": 1.0,
        "new_activity_scoring_policy": "best",
        "new_activity_min_attempt_interval_seconds": 0,
    }
    with pytest.raises(ValueError, match="scoring_policy"):
        BankActivityTerms(**{**base, "new_activity_scoring_policy": "median"})
    for bad in (-1, 2592001, True):
        with pytest.raises(ValueError, match="min_attempt_interval"):
            BankActivityTerms(
                **{**base, "new_activity_min_attempt_interval_seconds": bad}
            )
