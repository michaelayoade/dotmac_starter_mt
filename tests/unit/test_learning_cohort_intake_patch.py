"""Selected-field contract semantics for the narrow intake owner patch."""

from uuid import uuid4

import pytest
from dotmac_learning import CohortIntakePatch
from pydantic import ValidationError


def test_omitted_fields_are_distinct_from_each_explicit_null():
    ids = {"tenant_id": uuid4(), "cohort_id": uuid4()}
    patch = CohortIntakePatch(**ids)
    assert patch.model_fields_set == set(ids)
    assert patch.model_dump(exclude_unset=True) == ids
    for field in (
        "entrance_bank_id",
        "entrance_time_limit_minutes",
        "auto_accept_threshold",
    ):
        patch = CohortIntakePatch(**ids, **{field: None})
        assert patch.model_fields_set == {*ids, field}
        assert patch.model_dump(exclude_unset=True) == {**ids, field: None}


def test_required_ids_and_extra_authority_or_unrelated_fields_refuse():
    ids = {"tenant_id": uuid4(), "cohort_id": uuid4()}
    for missing in ids:
        with pytest.raises(ValidationError):
            CohortIntakePatch(
                **{key: value for key, value in ids.items() if key != missing}
            )
    for field, value in (
        ("actor_is_admin", True),
        ("status", "active"),
        ("auto_accept_below_action", "none"),
        ("time_limit_seconds", 60),
    ):
        with pytest.raises(ValidationError):
            CohortIntakePatch(**ids, **{field: value})
    with pytest.raises(ValidationError):
        CohortIntakePatch(**ids, entrance_bank_id="invalid")


def test_raw_numeric_values_remain_product_policy():
    patch = CohortIntakePatch(
        tenant_id=uuid4(),
        cohort_id=uuid4(),
        entrance_time_limit_minutes=-3,
        auto_accept_threshold=1.5,
    )
    assert patch.entrance_time_limit_minutes == -3
    assert patch.auto_accept_threshold == 1.5
