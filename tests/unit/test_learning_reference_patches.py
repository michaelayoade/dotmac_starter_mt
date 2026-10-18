"""Provided-field contracts for product-owned selective reference changes."""

from uuid import uuid4

import pytest
from dotmac_learning.references import ActivityPatch, CoursePatch
from pydantic import ValidationError


@pytest.mark.parametrize("command", [ActivityPatch, CoursePatch])
def test_patch_requires_exact_identity_and_forbids_unknown_and_timestamp_fields(
    command,
):
    for identity in ({}, {"id": uuid4()}, {"tenant_id": uuid4()}):
        with pytest.raises(ValidationError):
            command(**identity)
    for field in ("created_at", "updated_at", "actor_is_admin", "unknown"):
        with pytest.raises(ValidationError):
            command(id=uuid4(), tenant_id=uuid4(), **{field: None})


def test_activity_patch_tracks_provided_policy_keys_and_explicit_clear():
    identity = {"id": uuid4(), "tenant_id": uuid4()}
    patch = ActivityPatch(**identity, title="Imported", max_attempts=None)
    assert patch.model_fields_set == {*identity, "title", "max_attempts"}
    assert patch.model_dump(exclude_unset=True) == {
        **identity,
        "title": "Imported",
        "max_attempts": None,
    }
    empty = ActivityPatch(**identity)
    assert empty.model_dump(exclude_unset=True) == identity
    for field in ("chapter_number", "bank_id", "max_attempts", "question_count"):
        clear = ActivityPatch(**identity, **{field: None})
        assert clear.model_dump(exclude_unset=True)[field] is None


@pytest.mark.parametrize(
    "field",
    [
        "course_id",
        "title",
        "type",
        "pass_threshold",
        "assessment_mode",
        "grading",
        "weight",
    ],
)
def test_activity_patch_rejects_explicit_null_for_nonnullable_fields(field):
    with pytest.raises(ValidationError):
        ActivityPatch(id=uuid4(), tenant_id=uuid4(), **{field: None})


@pytest.mark.parametrize(
    "field",
    ["slug", "title", "discipline", "source_ref", "version", "status", "listed"],
)
def test_course_patch_rejects_explicit_null_for_nonnullable_fields(field):
    with pytest.raises(ValidationError):
        CoursePatch(id=uuid4(), tenant_id=uuid4(), **{field: None})


def test_course_patch_keeps_status_policy_with_product_and_omits_other_fields():
    identity = {"id": uuid4(), "tenant_id": uuid4()}
    patch = CoursePatch(**identity, status="product-defined")
    assert patch.model_fields_set == {*identity, "status"}
    assert patch.model_dump(exclude_unset=True) == {
        **identity,
        "status": "product-defined",
    }
