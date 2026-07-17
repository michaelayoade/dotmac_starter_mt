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

from pydantic import BaseModel

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


__all__ = ["CustomFieldCreate"]
