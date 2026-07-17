"""Custom field definitions — per-tenant field metadata for registrable entities.

Port of `ERP:app/models/finance/automation/custom_field.py`
(`CustomFieldDefinition` + the `CustomFieldType` enum, kept verbatim — 13
members) with starter adaptations:

- `tenant_id` replaces `organization_id`.
- `entity_type` is a plain `String(50)` (validated against `registry.py`'s
  `ENTITY_MODELS` at the service layer, Task 10) rather than the ERP's
  `CustomFieldEntityType` enum — registrable entities are this app's own
  extension point, not a fixed ERP-shaped list.
- `id` uses the repo's `uuid_pk()` convention instead of `field_id`.
- `created_by`/`updated_by` are dropped — no actor plumbing yet (phase 2c).
- `css_class`/`show_in_print` are dropped — YAGNI for the starter.

Field *values* live on the entity's own `custom_fields` JSONB column (see
`Party.custom_fields` in `app.core.models`), keyed by `field_code`. This
table only defines the field *shape* (type, validation, display).
"""

from __future__ import annotations

import enum
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, uuid_pk


class CustomFieldType(str, enum.Enum):
    """Data types for custom fields — ported verbatim from ERP (13 members)."""

    TEXT = "TEXT"
    TEXTAREA = "TEXTAREA"
    NUMBER = "NUMBER"
    DECIMAL = "DECIMAL"
    DATE = "DATE"
    DATETIME = "DATETIME"
    BOOLEAN = "BOOLEAN"
    SELECT = "SELECT"
    MULTISELECT = "MULTISELECT"
    EMAIL = "EMAIL"
    URL = "URL"
    PHONE = "PHONE"
    CURRENCY = "CURRENCY"


def _enum_values(enum_cls: type[enum.Enum]) -> list[str]:
    return [member.value for member in enum_cls]


class CustomFieldDefinition(Base, TimestampMixin):
    """Defines a custom field that can be attached to a registrable entity.

    Every tenant-scoped model follows the repo's standard template:
    `tenant_id UUID NOT NULL REFERENCES tenants(id)`, a composite unique on
    `(tenant_id, ...)` for anything unique-per-tenant, and RLS applied in the
    migration that creates the table (single `USING/WITH CHECK
    (tenant_id = app_current_tenant_id())` policy — same shape as `parties`,
    not the special split-policy shape `domain_settings` uses).
    """

    __tablename__ = "custom_field_definitions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "entity_type",
            "field_code",
            name="uq_custom_field_definitions_tenant_entity_code",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    tenant_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Field identification
    entity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Registrable entity key — validated against registry.ENTITY_MODELS",
    )
    field_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        comment="Internal code used as key in the entity's custom_fields JSON",
    )
    field_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        comment="Display name for the field",
    )
    description: Mapped[str | None] = mapped_column(Text)

    # Field type and configuration
    field_type: Mapped[CustomFieldType] = mapped_column(
        sa.Enum(
            CustomFieldType,
            name="ck_custom_field_definitions_field_type",
            native_enum=False,
            values_callable=_enum_values,
        ),
        nullable=False,
    )
    field_options: Mapped[dict[str, Any] | None] = mapped_column(
        sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
        comment="Options for SELECT/MULTISELECT: {options: [{value, label}]}",
    )

    # Validation
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_value: Mapped[str | None] = mapped_column(String(500))
    validation_regex: Mapped[str | None] = mapped_column(
        String(500), comment="Regex pattern for validation"
    )
    validation_message: Mapped[str | None] = mapped_column(
        String(200), comment="Error message when validation fails"
    )
    min_value: Mapped[str | None] = mapped_column(
        String(50), comment="Minimum value for NUMBER/DECIMAL/DATE"
    )
    max_value: Mapped[str | None] = mapped_column(
        String(50), comment="Maximum value for NUMBER/DECIMAL/DATE"
    )
    max_length: Mapped[int | None] = mapped_column(
        Integer, comment="Max length for TEXT/TEXTAREA"
    )

    # Display settings
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    section_name: Mapped[str | None] = mapped_column(
        String(100), comment="Group fields into sections"
    )
    placeholder: Mapped[str | None] = mapped_column(String(200))
    help_text: Mapped[str | None] = mapped_column(String(500))

    # Visibility
    show_in_list: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, comment="Show in list/table views"
    )
    show_in_form: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="Show in create/edit forms"
    )
    show_in_detail: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="Show in detail views"
    )

    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
