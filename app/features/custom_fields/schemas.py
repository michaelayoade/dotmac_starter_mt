"""Pydantic schemas for the custom fields feature.

`CustomFieldCreate` lives here rather than in `service.py` because both
consume it: the service (Task 9) needs it to type `create_field`'s payload,
and the router (Task 10) reuses it unmodified as the create endpoint's
request body. Field set/order mirrors ERP's `CustomFieldInput` dataclass
(`dotmac_erp:app/services/finance/automation/custom_fields.py`) minus the
dropped columns documented in `models.py` (`css_class`, `show_in_print`,
`created_by`/`updated_by` — no actor plumbing yet).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, RootModel

from app.features.custom_fields.models import CustomFieldType


class CustomFieldCreate(BaseModel):
    entity_type: str
    field_code: str
    field_name: str
    field_type: CustomFieldType
    description: str | None = None
    field_options: dict[str, Any] | None = None
    is_required: bool = False
    default_value: str | None = None
    validation_regex: str | None = None
    validation_message: str | None = None
    min_value: str | None = None
    max_value: str | None = None
    max_length: int | None = None
    display_order: int = 0
    section_name: str | None = None
    placeholder: str | None = None
    help_text: str | None = None
    show_in_list: bool = False
    show_in_form: bool = True
    show_in_detail: bool = True


class CustomFieldUpdate(BaseModel):
    """Definition update payload. Deliberately has NO `entity_type`/
    `field_code` fields — those are immutable (see `service.update_field`'s
    docstring) and must not even be *acceptable* input on this route, not
    just silently dropped. The router builds its `updates` dict from
    `model_dump(exclude_unset=True)` so only fields the caller actually set
    reach `service.update_field` (the service's mass-assignment comment says
    the router owns input filtering — this schema is that filter).
    """

    field_name: str | None = None
    description: str | None = None
    field_type: CustomFieldType | None = None
    field_options: dict[str, Any] | None = None
    is_required: bool | None = None
    default_value: str | None = None
    validation_regex: str | None = None
    validation_message: str | None = None
    min_value: str | None = None
    max_value: str | None = None
    max_length: int | None = None
    display_order: int | None = None
    section_name: str | None = None
    placeholder: str | None = None
    help_text: str | None = None
    show_in_list: bool | None = None
    show_in_form: bool | None = None
    show_in_detail: bool | None = None


class CustomFieldRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    entity_type: str
    field_code: str
    field_name: str
    field_type: CustomFieldType
    description: str | None = None
    field_options: dict[str, Any] | None = None
    is_required: bool
    default_value: str | None = None
    validation_regex: str | None = None
    validation_message: str | None = None
    min_value: str | None = None
    max_value: str | None = None
    max_length: int | None = None
    display_order: int
    section_name: str | None = None
    placeholder: str | None = None
    help_text: str | None = None
    show_in_list: bool
    show_in_form: bool
    show_in_detail: bool
    is_active: bool


class CustomFieldValues(RootModel[dict[str, Any]]):
    """Request/response body for the values endpoints — a bare
    `{field_code: value}` dict, no envelope. `RootModel` lets FastAPI accept
    and return a top-level JSON object without wrapping it in a named key.
    """


__all__ = [
    "CustomFieldCreate",
    "CustomFieldRead",
    "CustomFieldUpdate",
    "CustomFieldValues",
]
