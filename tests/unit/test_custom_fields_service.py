"""Unit coverage for `app.features.custom_fields.service` (Task 9).

Port of `dotmac_erp:app/services/finance/automation/custom_fields.py`'s
service tests, generalized for tenant_id + the four gap-closures this task
adds over ERP (see `service.py`'s module docstring): per-entity limit
enforcement, min/max Decimal enforcement, unknown-code errors, and the
set_values/get_values value-storage layer. MULTISELECT membership
validation is new behavior (ERP never validated it at all).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core import settings_resolver as sr
from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.models import Party, Tenant
from app.core.settings_models import SettingDomain
from app.features.custom_fields import service as cf_service
from app.features.custom_fields.models import CustomFieldType
from app.features.custom_fields.schemas import CustomFieldCreate

# Import for the side effect: registers custom_fields/max_per_entity (and the
# other spec-declared settings) with the core resolver registry.
from app.features.settings import spec as _settings_spec  # noqa: F401


def _payload(**overrides) -> CustomFieldCreate:
    defaults: dict = {
        "entity_type": "party",
        "field_code": "loyalty_tier",
        "field_name": "Loyalty Tier",
        "field_type": CustomFieldType.TEXT,
    }
    defaults.update(overrides)
    return CustomFieldCreate(**defaults)


# ---------------------------------------------------------------------------
# create_field
# ---------------------------------------------------------------------------


def test_create_field_creates_definition(db: Session, tenant_row: Tenant) -> None:
    field = cf_service.create_field(db, tenant_row.id, _payload())

    assert field.tenant_id == tenant_row.id
    assert field.entity_type == "party"
    assert field.field_code == "loyalty_tier"
    assert field.field_type == CustomFieldType.TEXT
    assert field.is_active is True


def test_create_field_duplicate_code_raises_conflict(
    db: Session, tenant_row: Tenant
) -> None:
    cf_service.create_field(db, tenant_row.id, _payload())

    with pytest.raises(ConflictError):
        cf_service.create_field(db, tenant_row.id, _payload())


def test_create_field_non_identifier_code_raises_bad_request(
    db: Session, tenant_row: Tenant
) -> None:
    with pytest.raises(BadRequestError):
        cf_service.create_field(db, tenant_row.id, _payload(field_code="not-valid!"))


def test_create_field_limit_reached_raises_bad_request(
    db: Session, tenant_row: Tenant
) -> None:
    sr.upsert_by_key(
        db,
        SettingDomain.custom_fields,
        "max_per_entity",
        2,
        tenant_id=tenant_row.id,
    )

    cf_service.create_field(db, tenant_row.id, _payload(field_code="field_one"))
    cf_service.create_field(db, tenant_row.id, _payload(field_code="field_two"))

    with pytest.raises(BadRequestError, match="limit reached"):
        cf_service.create_field(db, tenant_row.id, _payload(field_code="field_three"))


# ---------------------------------------------------------------------------
# get_field / get_by_code / list_for_entity
# ---------------------------------------------------------------------------


def test_get_field_returns_definition(db: Session, tenant_row: Tenant) -> None:
    created = cf_service.create_field(db, tenant_row.id, _payload())

    fetched = cf_service.get_field(db, tenant_row.id, created.id)

    assert fetched.id == created.id


def test_get_field_missing_raises_not_found(db: Session, tenant_row: Tenant) -> None:
    with pytest.raises(NotFoundError):
        cf_service.get_field(db, tenant_row.id, uuid4())


def test_get_by_code_returns_none_when_missing(db: Session, tenant_row: Tenant) -> None:
    assert cf_service.get_by_code(db, tenant_row.id, "party", "nope") is None


def test_list_for_entity_orders_by_section_order_name(
    db: Session, tenant_row: Tenant
) -> None:
    cf_service.create_field(
        db,
        tenant_row.id,
        _payload(field_code="z_field", section_name="B", display_order=0),
    )
    cf_service.create_field(
        db,
        tenant_row.id,
        _payload(field_code="a_field", section_name="A", display_order=1),
    )
    cf_service.create_field(
        db,
        tenant_row.id,
        _payload(field_code="b_field", section_name="A", display_order=0),
    )

    fields = cf_service.list_for_entity(db, tenant_row.id, "party")

    assert [f.field_code for f in fields] == ["b_field", "a_field", "z_field"]


def test_list_for_entity_excludes_inactive_by_default(
    db: Session, tenant_row: Tenant
) -> None:
    field = cf_service.create_field(db, tenant_row.id, _payload())
    cf_service.deactivate_field(db, tenant_row.id, field.id)

    active = cf_service.list_for_entity(db, tenant_row.id, "party")
    assert active == []

    everyone = cf_service.list_for_entity(db, tenant_row.id, "party", is_active=False)
    assert [f.id for f in everyone] == [field.id]


# ---------------------------------------------------------------------------
# update_field / deactivate_field
# ---------------------------------------------------------------------------


def test_update_field_updates_allowed_fields(db: Session, tenant_row: Tenant) -> None:
    field = cf_service.create_field(db, tenant_row.id, _payload())

    updated = cf_service.update_field(
        db, tenant_row.id, field.id, {"field_name": "Renamed", "is_required": True}
    )

    assert updated.field_name == "Renamed"
    assert updated.is_required is True


def test_update_field_forbids_entity_type_and_field_code_change(
    db: Session, tenant_row: Tenant
) -> None:
    field = cf_service.create_field(db, tenant_row.id, _payload())

    updated = cf_service.update_field(
        db,
        tenant_row.id,
        field.id,
        {"entity_type": "other", "field_code": "renamed_code"},
    )

    assert updated.entity_type == "party"
    assert updated.field_code == "loyalty_tier"


def test_deactivate_field_soft_deletes(db: Session, tenant_row: Tenant) -> None:
    field = cf_service.create_field(db, tenant_row.id, _payload())

    deactivated = cf_service.deactivate_field(db, tenant_row.id, field.id)

    assert deactivated.is_active is False
    # Row still exists (soft delete, not a hard delete).
    assert cf_service.get_field(db, tenant_row.id, field.id).is_active is False


# ---------------------------------------------------------------------------
# validate_values
# ---------------------------------------------------------------------------


def test_validate_values_required_missing_raises(
    db: Session, tenant_row: Tenant
) -> None:
    cf_service.create_field(db, tenant_row.id, _payload(is_required=True))

    with pytest.raises(BadRequestError, match="is required"):
        cf_service.validate_values(db, tenant_row.id, "party", {})


def test_validate_values_number_non_numeric_raises(
    db: Session, tenant_row: Tenant
) -> None:
    cf_service.create_field(
        db,
        tenant_row.id,
        _payload(field_code="age", field_type=CustomFieldType.NUMBER),
    )

    with pytest.raises(BadRequestError, match="must be a number"):
        cf_service.validate_values(db, tenant_row.id, "party", {"age": "not-a-number"})


def test_validate_values_select_non_member_raises(
    db: Session, tenant_row: Tenant
) -> None:
    cf_service.create_field(
        db,
        tenant_row.id,
        _payload(
            field_code="tier",
            field_type=CustomFieldType.SELECT,
            field_options={"options": [{"value": "gold"}, {"value": "silver"}]},
        ),
    )

    with pytest.raises(BadRequestError, match="allowed options"):
        cf_service.validate_values(db, tenant_row.id, "party", {"tier": "bronze"})


def test_validate_values_regex_mismatch_raises(db: Session, tenant_row: Tenant) -> None:
    cf_service.create_field(
        db,
        tenant_row.id,
        _payload(
            field_code="sku",
            validation_regex=r"^SKU-\d+$",
            validation_message="Must look like SKU-123",
        ),
    )

    with pytest.raises(BadRequestError, match="Must look like SKU-123"):
        cf_service.validate_values(db, tenant_row.id, "party", {"sku": "nope"})


def test_validate_values_min_max_out_of_range_decimal(
    db: Session, tenant_row: Tenant
) -> None:
    cf_service.create_field(
        db,
        tenant_row.id,
        _payload(
            field_code="discount",
            field_type=CustomFieldType.DECIMAL,
            min_value="0",
            max_value="0.5",
        ),
    )

    with pytest.raises(BadRequestError, match="must be at most 0.5"):
        cf_service.validate_values(db, tenant_row.id, "party", {"discount": "0.75"})

    with pytest.raises(BadRequestError, match="must be at least 0"):
        cf_service.validate_values(db, tenant_row.id, "party", {"discount": "-1"})

    # In range — no error.
    cf_service.validate_values(db, tenant_row.id, "party", {"discount": "0.25"})


def test_validate_values_unknown_code_errors(db: Session, tenant_row: Tenant) -> None:
    with pytest.raises(BadRequestError, match="Unknown custom field"):
        cf_service.validate_values(db, tenant_row.id, "party", {"never_defined": "x"})


def test_validate_values_multiselect_membership(
    db: Session, tenant_row: Tenant
) -> None:
    """PORT-DELTA: ERP never validated MULTISELECT at all."""
    cf_service.create_field(
        db,
        tenant_row.id,
        _payload(
            field_code="tags",
            field_type=CustomFieldType.MULTISELECT,
            field_options={"options": [{"value": "a"}, {"value": "b"}]},
        ),
    )

    with pytest.raises(BadRequestError, match="allowed options"):
        cf_service.validate_values(db, tenant_row.id, "party", {"tags": ["a", "z"]})

    # All members valid — no error.
    cf_service.validate_values(db, tenant_row.id, "party", {"tags": ["a", "b"]})


# ---------------------------------------------------------------------------
# set_values / get_values
# ---------------------------------------------------------------------------


def test_set_values_merges_and_none_deletes(
    db: Session, tenant_row: Tenant, party_row: Party
) -> None:
    cf_service.create_field(db, tenant_row.id, _payload(field_code="nickname"))
    cf_service.create_field(db, tenant_row.id, _payload(field_code="notes"))

    first = cf_service.set_values(
        db,
        tenant_row.id,
        "party",
        party_row.id,
        {"nickname": "Ada", "notes": "vip"},
    )
    assert first == {"nickname": "Ada", "notes": "vip"}

    # Partial update: `nickname` overwritten, `notes` untouched (not in payload).
    second = cf_service.set_values(
        db, tenant_row.id, "party", party_row.id, {"nickname": "A.L."}
    )
    assert second == {"nickname": "A.L.", "notes": "vip"}

    # None deletes the key.
    third = cf_service.set_values(
        db, tenant_row.id, "party", party_row.id, {"notes": None}
    )
    assert third == {"nickname": "A.L."}

    assert party_row.custom_fields == {"nickname": "A.L."}


def test_set_values_missing_entity_raises_not_found(
    db: Session, tenant_row: Tenant
) -> None:
    with pytest.raises(NotFoundError):
        cf_service.set_values(db, tenant_row.id, "party", uuid4(), {})


def test_get_values_missing_entity_raises_not_found(
    db: Session, tenant_row: Tenant
) -> None:
    with pytest.raises(NotFoundError):
        cf_service.get_values(db, tenant_row.id, "party", uuid4())


def test_get_values_returns_stored_values(
    db: Session, tenant_row: Tenant, party_row: Party
) -> None:
    cf_service.create_field(db, tenant_row.id, _payload(field_code="nickname"))
    cf_service.set_values(db, tenant_row.id, "party", party_row.id, {"nickname": "Ada"})

    assert cf_service.get_values(db, tenant_row.id, "party", party_row.id) == {
        "nickname": "Ada"
    }


def test_get_values_empty_for_entity_with_no_values(
    db: Session, tenant_row: Tenant, party_row: Party
) -> None:
    assert cf_service.get_values(db, tenant_row.id, "party", party_row.id) == {}
