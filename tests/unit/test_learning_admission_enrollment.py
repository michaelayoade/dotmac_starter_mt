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
