"""Invitation intent is strict; results expose source observations, not rendering."""

from dataclasses import FrozenInstanceError, fields
from uuid import uuid4

import pytest
from dotmac_learning.participation_contracts import (
    ActivationResult,
    EnsureInvitationEnrollmentCommand,
    InvitationEnrollmentResult,
)
from pydantic import ValidationError


def test_invitation_command_preserves_course_order_duplicates_and_open_product_roles():
    course, other = uuid4(), uuid4()
    command = EnsureInvitationEnrollmentCommand(
        tenant_id=uuid4(),
        cohort_id=uuid4(),
        person_id=uuid4(),
        global_role="product-observer",
        selected_course_ids=[course, other, course],
    )
    assert command.selected_course_ids == (course, other, course)
    assert command.global_role == "product-observer" and command.track_id is None
    with pytest.raises(ValidationError):
        command.global_role = "admin"
    with pytest.raises(ValidationError):
        EnsureInvitationEnrollmentCommand(**command.model_dump(), actor_is_admin=True)


def test_invitation_result_is_frozen_and_reuses_canonical_activation_observation():
    activation = ActivationResult("reactivated", "waitlisted")
    result = InvitationEnrollmentResult("student", True, activation)
    assert result.activation is activation
    assert [field.name for field in fields(result)] == [
        "member_role",
        "preserved_instructor_role",
        "activation",
    ]
    with pytest.raises(FrozenInstanceError):
        result.preserved_instructor_role = False
    assert InvitationEnrollmentResult("student", False, None).activation is None
