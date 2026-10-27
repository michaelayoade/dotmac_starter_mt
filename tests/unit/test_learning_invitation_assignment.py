"""Invitation intent is strict; results expose source observations, not rendering."""

from dataclasses import FrozenInstanceError, fields
from uuid import uuid4

import pytest
from dotmac_learning import roster
from dotmac_learning.participation_contracts import (
    ActivationResult,
    EnsureInvitationEnrollmentCommand,
    InvitationEnrollmentInitialState,
    InvitationEnrollmentResult,
    ParticipationBadRequest,
    ParticipationForbidden,
    RosterAuthority,
)
from dotmac_learning.result_contracts import TransactionChanged
from pydantic import ValidationError


def test_invitation_command_preserves_course_order_duplicates_and_open_product_roles():
    course, other = uuid4(), uuid4()
    command = EnsureInvitationEnrollmentCommand(
        tenant_id=uuid4(),
        cohort_id=uuid4(),
        person_id=uuid4(),
        global_role="product-observer",
        pacing_default_open=True,
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


class ConfiguredRolePolicy:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def member_role(self, global_role):
        self.calls.append(global_role)
        return self.mapping[global_role]


@pytest.fixture
def staged_invitation(monkeypatch):
    staged = []
    boundary = object()
    monkeypatch.setattr(roster, "_transaction", lambda session: boundary)
    monkeypatch.setattr(roster, "_unchanged", lambda session, root: None)
    monkeypatch.setattr(roster, "_enrollment", lambda *args: None)

    class StagingSession:
        def add(self, row):
            staged.append(row)

    db = StagingSession()
    command = EnsureInvitationEnrollmentCommand(
        tenant_id=uuid4(),
        cohort_id=uuid4(),
        person_id=uuid4(),
        global_role="configured-guide",
        pacing_default_open=True,
    )
    return db, command, staged


@pytest.mark.parametrize("configured_role", ["student", "instructor"])
def test_installed_policy_controls_custom_product_role(
    staged_invitation, configured_role
):
    db, command, staged = staged_invitation
    policy = ConfiguredRolePolicy({"configured-guide": configured_role})
    result = roster.ensure_invitation_enrollment(
        db,
        command,
        authority=RosterAuthority(command.tenant_id, False),
        role_policy=policy,
        initial_state=InvitationEnrollmentInitialState("active"),
    )
    assert policy.calls == ["configured-guide"]
    assert result.member_role == configured_role
    assert staged[0].role_in_cohort == configured_role


@pytest.mark.parametrize("global_role", ["admin", "instructor"])
def test_product_can_configure_familiar_global_labels_as_students(
    staged_invitation, global_role
):
    db, command, staged = staged_invitation
    command = command.model_copy(update={"global_role": global_role})
    policy = ConfiguredRolePolicy({global_role: "student"})
    result = roster.ensure_invitation_enrollment(
        db,
        command,
        authority=RosterAuthority(command.tenant_id, False),
        role_policy=policy,
        initial_state=InvitationEnrollmentInitialState("active"),
    )
    assert policy.calls == [global_role]
    assert result.member_role == staged[0].role_in_cohort == "student"


@pytest.mark.parametrize("invalid_role", ["admin", "", None, True, [], {}])
def test_invalid_policy_role_refuses_before_query_or_mutation(
    staged_invitation, monkeypatch, invalid_role
):
    db, command, staged = staged_invitation
    policy = ConfiguredRolePolicy({command.global_role: invalid_role})

    def forbidden_query(*args):
        pytest.fail("invalid role reached enrollment query")

    monkeypatch.setattr(roster, "_enrollment", forbidden_query)
    with pytest.raises(
        ParticipationBadRequest, match="invalid invitation enrollment role"
    ):
        roster.ensure_invitation_enrollment(
            db,
            command,
            authority=RosterAuthority(command.tenant_id, False),
            role_policy=policy,
            initial_state=InvitationEnrollmentInitialState("active"),
        )
    assert policy.calls == [command.global_role]
    assert staged == []


def test_policy_is_required_without_a_default(staged_invitation):
    db, command, staged = staged_invitation
    with pytest.raises(TypeError, match="role_policy"):
        roster.ensure_invitation_enrollment(
            db,
            command,
            authority=RosterAuthority(command.tenant_id, False),
            initial_state=InvitationEnrollmentInitialState("active"),
        )
    assert staged == []


def test_authority_refusal_precedes_installed_policy(staged_invitation):
    db, command, staged = staged_invitation
    policy = ConfiguredRolePolicy({command.global_role: "instructor"})
    with pytest.raises(ParticipationForbidden):
        roster.ensure_invitation_enrollment(
            db,
            command,
            authority=RosterAuthority(uuid4(), True),
            role_policy=policy,
            initial_state=InvitationEnrollmentInitialState("active"),
        )
    assert policy.calls == []
    assert staged == []


def test_transaction_refusal_precedes_installed_policy(staged_invitation, monkeypatch):
    db, command, staged = staged_invitation
    policy = ConfiguredRolePolicy({command.global_role: "instructor"})

    def refuse_transaction(session):
        raise TransactionChanged("caller transaction required")

    monkeypatch.setattr(roster, "_transaction", refuse_transaction)
    with pytest.raises(TransactionChanged):
        roster.ensure_invitation_enrollment(
            db,
            command,
            authority=RosterAuthority(command.tenant_id, False),
            role_policy=policy,
            initial_state=InvitationEnrollmentInitialState("active"),
        )
    assert policy.calls == []
    assert staged == []
