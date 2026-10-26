"""Typed prerequisite intent preserves source strings and nullable requires."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_learning.prerequisite_contracts import (
    PrerequisiteDeclaration,
    PrerequisiteReconciliationError,
    PrerequisiteReconciliationResult,
    ReconcilePrerequisitesCommand,
)
from pydantic import ValidationError


def test_source_requires_normalization_and_raw_strings():
    for supplied in ({}, {"requires": None}, {"requires": []}):
        assert PrerequisiteDeclaration(course=" unknown ", **supplied).requires == ()
    declaration = PrerequisiteDeclaration(course=" A ", requires=[" B ", " B "])
    assert declaration.course == " A "
    assert declaration.requires == (" B ", " B ")
    command = ReconcilePrerequisitesCommand(
        tenant_id=uuid4(), discipline=None, declarations=(declaration,)
    )
    assert command.discipline is None
    assert command.declarations == (declaration,)


def test_required_scope_and_rejected_authority_transaction_fields():
    values = {"tenant_id": uuid4(), "discipline": "raw", "declarations": ()}
    for field in ("tenant_id", "discipline"):
        with pytest.raises(ValidationError):
            ReconcilePrerequisitesCommand(
                **{k: v for k, v in values.items() if k != field}
            )
    for field in ("dry_run", "commit", "actor_is_admin"):
        with pytest.raises(ValidationError):
            ReconcilePrerequisitesCommand(**values, **{field: True})
    with pytest.raises(ValidationError):
        PrerequisiteDeclaration(course="x", requires=[None])


def test_typed_error_is_value_error_and_result_detached_immutable():
    assert isinstance(PrerequisiteReconciliationError("source detail"), ValueError)
    result = PrerequisiteReconciliationResult(added=2, removed=1, declared=3)
    with pytest.raises(FrozenInstanceError):
        result.added = 4


def test_condition_declaration_closed_catalog_and_typed_params():
    from uuid import uuid4

    from dotmac_learning.prerequisite_contracts import (
        PREREQUISITE_CONDITION_KINDS,
        PrerequisiteConditionDeclaration,
    )

    assert PREREQUISITE_CONDITION_KINDS == (
        "course_completed",
        "certificate_held",
        "course_grade_at_least",
        "activity_passed",
        "attempt_number_at_least",
    )
    activity_id = uuid4()
    assert (
        PrerequisiteConditionDeclaration(
            kind="attempt_number_at_least",
            activity_id=activity_id,
            min_attempts=2,
            group=" g1 ",
        ).group
        == "g1"
    )
    bad = [
        {"kind": "waiver"},
        {"kind": "course_completed"},
        {"kind": "course_completed", "requires": "a", "activity_id": activity_id},
        {"kind": "course_grade_at_least", "requires": "a"},
        {"kind": "course_grade_at_least", "requires": "a", "min_fraction": 0},
        {"kind": "course_grade_at_least", "requires": "a", "min_fraction": 1.5},
        {
            "kind": "course_grade_at_least",
            "requires": "a",
            "min_fraction": float("nan"),
        },
        {"kind": "activity_passed"},
        {"kind": "attempt_number_at_least", "activity_id": activity_id},
        {
            "kind": "attempt_number_at_least",
            "activity_id": activity_id,
            "min_attempts": 0,
        },
        {"kind": "certificate_held", "requires": "a", "min_fraction": 0.5},
        {"kind": "course_completed", "requires": "a", "group": "  "},
        {"kind": "course_completed", "requires": "a", "surprise": True},
    ]
    for values in bad:
        with pytest.raises(ValidationError):
            PrerequisiteConditionDeclaration(**values)


def test_declaration_conditions_and_composition_are_additive():
    from dotmac_learning.prerequisite_contracts import PrerequisiteDeclaration

    legacy = PrerequisiteDeclaration(course="a", requires=["b"])
    assert legacy.conditions == () and legacy.composition is None
    authored = PrerequisiteDeclaration(
        course="a",
        composition="any",
        conditions=[{"kind": "certificate_held", "requires": "b"}],
    )
    assert authored.composition == "any"
    assert authored.conditions[0].kind == "certificate_held"
    with pytest.raises(ValidationError):
        PrerequisiteDeclaration(course="a", composition="some")
