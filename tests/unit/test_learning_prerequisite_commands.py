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
