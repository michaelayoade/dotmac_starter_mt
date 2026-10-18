"""Cohort commands preserve source strings and reject client authority."""

from uuid import uuid4

import pytest
from dotmac_learning.cohort_contracts import (
    CreateCohortCommand,
    RemoveFacilitatorCommand,
)
from pydantic import ValidationError


def test_creation_keeps_raw_strings_and_has_no_client_status_or_delivery_policy():
    command = CreateCohortCommand(tenant_id=uuid4(), name="  ", discipline=" ANY ")
    assert (command.name, command.discipline) == ("  ", " ANY ")
    for field, value in (("status", "dropped"), ("delivery_mode", "live")):
        with pytest.raises(ValidationError):
            CreateCohortCommand(**command.model_dump(), **{field: value})


def test_removal_requires_scoped_ids_and_rejects_client_authorization():
    values = {"tenant_id": uuid4(), "cohort_id": uuid4(), "person_id": uuid4()}
    assert RemoveFacilitatorCommand(**values).model_dump() == values
    with pytest.raises(ValidationError):
        RemoveFacilitatorCommand(**values, actor_is_admin=True)
    for field in values:
        with pytest.raises(ValidationError):
            RemoveFacilitatorCommand(**{k: v for k, v in values.items() if k != field})
