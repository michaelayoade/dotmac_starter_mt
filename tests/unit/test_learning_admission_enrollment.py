"""Admission ensure command canary; authored before implementation, hosted only."""

from uuid import uuid4

import pytest
from dotmac_learning.participation_contracts import EnsureAdmissionEnrollmentCommand
from pydantic import ValidationError


def test_command_is_frozen_typed_and_rejects_client_authority_fields():
    tenant, cohort, person = uuid4(), uuid4(), uuid4()
    command = EnsureAdmissionEnrollmentCommand(
        tenant_id=tenant, cohort_id=cohort, person_id=person, track_id=None
    )
    assert (
        command.tenant_id,
        command.cohort_id,
        command.person_id,
        command.track_id,
    ) == (
        tenant,
        cohort,
        person,
        None,
    )
    with pytest.raises(ValidationError):
        command.track_id = uuid4()
    with pytest.raises(ValidationError):
        EnsureAdmissionEnrollmentCommand(
            tenant_id=tenant,
            cohort_id=cohort,
            person_id=person,
            track_id=None,
            actor_is_admin=True,
        )
    with pytest.raises(ValidationError):
        EnsureAdmissionEnrollmentCommand(
            tenant_id="invalid", cohort_id=cohort, person_id=person, track_id=None
        )


def test_initial_state_requires_both_fields_and_is_frozen():
    from dataclasses import FrozenInstanceError

    from dotmac_learning import AdmissionEnrollmentInitialState

    with pytest.raises(TypeError):
        AdmissionEnrollmentInitialState()
    with pytest.raises(TypeError):
        AdmissionEnrollmentInitialState(role_in_cohort="student")
    with pytest.raises(TypeError):
        AdmissionEnrollmentInitialState(status="active")
    state = AdmissionEnrollmentInitialState(
        role_in_cohort="instructor", status="dropped"
    )
    with pytest.raises(FrozenInstanceError):
        state.status = "active"


@pytest.mark.parametrize("field", ["role_in_cohort", "status"])
@pytest.mark.parametrize("value", [None, True, False, 0, 1, 1.5, b"student", [], {}])
def test_initial_state_rejects_non_builtin_strings(field, value):
    from dotmac_learning import AdmissionEnrollmentInitialState

    values = {"role_in_cohort": "student", "status": "active", field: value}
    with pytest.raises(TypeError, match=field):
        AdmissionEnrollmentInitialState(**values)


@pytest.mark.parametrize("field", ["role_in_cohort", "status"])
def test_initial_state_preserves_storage_bounds_and_verbatim_values(field):
    from dotmac_learning import AdmissionEnrollmentInitialState

    class StringSubclass(str):
        pass

    values = {"role_in_cohort": "student", "status": "active"}
    for value in ("", " \t ", "unlisted-business", "界" * 20):
        state = AdmissionEnrollmentInitialState(**{**values, field: value})
        assert getattr(state, field) == value
    with pytest.raises(ValueError, match=field):
        AdmissionEnrollmentInitialState(**{**values, field: "界" * 21})
    with pytest.raises(TypeError, match=field):
        AdmissionEnrollmentInitialState(**{**values, field: StringSubclass("active")})


def test_owner_requires_keyword_policy_and_rejects_malformed_snapshot_before_db_use():
    from dotmac_learning import (
        AdmissionEnrollmentInitialState,
        ensure_admission_enrollment,
    )

    command = EnsureAdmissionEnrollmentCommand(
        tenant_id=uuid4(), cohort_id=uuid4(), person_id=uuid4(), track_id=None
    )
    with pytest.raises(TypeError):
        ensure_admission_enrollment(None, command)
    state = AdmissionEnrollmentInitialState(role_in_cohort="student", status="active")
    with pytest.raises(TypeError):
        ensure_admission_enrollment(None, command, state)
    for malformed in (None, {}, "student", True):
        with pytest.raises(TypeError, match="initial_state"):
            ensure_admission_enrollment(None, command, initial_state=malformed)


def test_owner_rejects_subclass_without_dispatching_validation_or_handing_off_db(
    monkeypatch,
):
    from dotmac_learning import AdmissionEnrollmentInitialState, roster

    handoffs = []

    def forbidden_handoff(db):
        handoffs.append(db)
        pytest.fail("invalid initial state reached the transaction owner")

    monkeypatch.setattr(roster, "_transaction", forbidden_handoff)

    class SkippedValidation(AdmissionEnrollmentInitialState):
        def __post_init__(self):
            pass

    command = EnsureAdmissionEnrollmentCommand(
        tenant_id=uuid4(), cohort_id=uuid4(), person_id=uuid4(), track_id=None
    )
    for role, status in (("student", "active"), (None, "active")):
        state = SkippedValidation(role_in_cohort=role, status=status)
        with pytest.raises(TypeError, match="initial_state"):
            roster.ensure_admission_enrollment(object(), command, initial_state=state)
    assert handoffs == []


@pytest.mark.parametrize("field", ["role_in_cohort", "status"])
@pytest.mark.parametrize(
    "value,error",
    [(None, TypeError), (1, TypeError), (True, TypeError), ("界" * 21, ValueError)],
)
def test_owner_revalidates_tampered_raw_fields_before_any_db_handoff(
    monkeypatch, field, value, error
):
    from dotmac_learning import AdmissionEnrollmentInitialState, roster

    handoffs = []

    def forbidden_handoff(db):
        handoffs.append(db)
        pytest.fail("tampered initial state reached the transaction owner")

    monkeypatch.setattr(roster, "_transaction", forbidden_handoff)
    state = AdmissionEnrollmentInitialState(role_in_cohort="student", status="active")
    object.__setattr__(state, field, value)
    command = EnsureAdmissionEnrollmentCommand(
        tenant_id=uuid4(), cohort_id=uuid4(), person_id=uuid4(), track_id=None
    )
    with pytest.raises(error, match=field):
        roster.ensure_admission_enrollment(object(), command, initial_state=state)
    assert handoffs == []
