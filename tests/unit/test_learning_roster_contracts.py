"""Strict commands and immutable caller-authority/result contracts."""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest
from dotmac_learning.participation_contracts import (
    ActivateEnrollmentCommand,
    BulkEnrollCommand,
    RosterAuthority,
)
from pydantic import ValidationError


def test_roster_authority_is_required_scoped_and_immutable():
    tenant = uuid4()
    authority = RosterAuthority(tenant, False)
    assert authority.tenant_id == tenant
    with pytest.raises(FrozenInstanceError):
        authority.actor_is_admin = True
    with pytest.raises(TypeError):
        RosterAuthority(tenant)


def test_roster_commands_reject_client_authorization_and_unknown_fields():
    with pytest.raises(ValidationError):
        ActivateEnrollmentCommand(
            tenant_id=uuid4(), enrollment_id=uuid4(), actor_is_admin=True
        )
    command = BulkEnrollCommand(
        tenant_id=uuid4(), cohort_id=uuid4(), emails=[None, " A@X ", ""]
    )
    assert command.emails == (None, " A@X ", "")
