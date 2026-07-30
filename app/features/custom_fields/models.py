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
`Party.custom_fields` in `dotmac_kernel.models`), keyed by `field_code`. This
table only defines the field *shape* (type, validation, display).
"""

from __future__ import annotations

import enum
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from dotmac_kernel.models import Base, TimestampMixin, uuid_pk
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

    def validate_value(self, value: Any) -> tuple[bool, str | None]:
        """Validate a value against this field's rules.

        Ported from ERP's `CustomFieldDefinition.validate_value`
        (`dotmac_erp:app/models/finance/automation/custom_field.py`) with two
        gap-closures (verified: ERP declared these columns/type but never
        enforced them — see `docs/superpowers/upstream-findings.md`):

        - `min_value`/`max_value` are enforced for NUMBER/DECIMAL, compared
          as `Decimal` (ERP stored these but the method never read them).
        - MULTISELECT membership is validated (each element checked against
          `field_options["options"]`) — ERP had no MULTISELECT branch at
          all, so a MULTISELECT field's `field_options` were purely
          decorative there. PORT-DELTA: this is new behavior, not a port.

        Task 9 review fixes — real checks added for two more types ERP left
        as pure passthrough:
        - BOOLEAN: value must be a `bool` (`True`/`False`), not a truthy
          string like `"true"`.
        - DATE / DATETIME: value must be a `date`/`datetime` object, or a
          string parseable by `date.fromisoformat`/`datetime.fromisoformat`
          (ISO 8601).

        **URL, PHONE, and CURRENCY are deliberately left as passthrough —
        no format check runs for them here.** Real-world formats for these
        vary too much per project (international phone numbers, currency
        codes vs. symbols, internal vs. public URLs) for a starter to pick
        one. Set `validation_regex` (+ optionally `validation_message`) on
        the field definition to enforce your project's format.

        Returns `(is_valid, error_message)`, same shape as ERP's method —
        the service layer (`service.py::validate_values`) aggregates the
        error messages instead of returning the tuple to its own caller.
        """
        if self.is_required and (value is None or value == ""):
            return False, f"{self.field_name} is required"

        if value is None or value == "":
            return True, None

        if self.field_type == CustomFieldType.NUMBER:
            # Final-review Group 4(b): `Decimal(int(value))` truncated toward
            # zero BEFORE either the integrality or range check ran — 3.7
            # silently became 3, and a value like 7.9 against max_value=7
            # would truncate to exactly 7 and pass. `Decimal(str(value))`
            # preserves full precision so both checks below see the value
            # the caller actually sent.
            try:
                numeric = Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                return False, f"{self.field_name} must be a number"
            if numeric != numeric.to_integral_value():
                return False, f"{self.field_name} must be a whole number"
            range_error = self._range_error(numeric)
            if range_error:
                return False, range_error

        elif self.field_type == CustomFieldType.DECIMAL:
            try:
                numeric = Decimal(str(value))
            except (InvalidOperation, ValueError, TypeError):
                return False, f"{self.field_name} must be a decimal number"
            range_error = self._range_error(numeric)
            if range_error:
                return False, range_error

        elif self.field_type == CustomFieldType.BOOLEAN:
            if not isinstance(value, bool):
                return False, f"{self.field_name} must be true or false"

        elif self.field_type == CustomFieldType.DATE:
            if not isinstance(value, date):
                try:
                    date.fromisoformat(str(value))
                except ValueError:
                    return (
                        False,
                        f"{self.field_name} must be a valid date (YYYY-MM-DD)",
                    )

        elif self.field_type == CustomFieldType.DATETIME:
            if not isinstance(value, datetime):
                try:
                    datetime.fromisoformat(str(value))
                except ValueError:
                    return (
                        False,
                        f"{self.field_name} must be a valid datetime (ISO 8601)",
                    )

        elif self.field_type == CustomFieldType.EMAIL:
            email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
            if not re.match(email_pattern, str(value)):
                return False, f"{self.field_name} must be a valid email address"

        elif self.field_type == CustomFieldType.SELECT:
            if self.field_options:
                valid_values = [
                    opt.get("value") for opt in self.field_options.get("options", [])
                ]
                if str(value) not in valid_values:
                    return (
                        False,
                        f"{self.field_name} must be one of the allowed options",
                    )

        elif self.field_type == CustomFieldType.MULTISELECT:
            if self.field_options:
                valid_values = [
                    opt.get("value") for opt in self.field_options.get("options", [])
                ]
                if not isinstance(value, list | tuple):
                    return False, f"{self.field_name} must be a list of values"
                if any(str(item) not in valid_values for item in value):
                    return (
                        False,
                        f"{self.field_name} must contain only allowed options",
                    )

        # Regex validation
        if self.validation_regex:
            if not re.match(self.validation_regex, str(value)):
                return (
                    False,
                    self.validation_message or f"{self.field_name} format is invalid",
                )

        # Length validation
        if self.max_length and len(str(value)) > self.max_length:
            return (
                False,
                f"{self.field_name} must be at most {self.max_length} characters",
            )

        return True, None

    def _range_error(self, numeric: Decimal) -> str | None:
        """min_value/max_value are stored as strings; compare as Decimal."""
        if self.min_value is not None:
            try:
                if numeric < Decimal(self.min_value):
                    return f"{self.field_name} must be at least {self.min_value}"
            except InvalidOperation:
                pass
        if self.max_value is not None:
            try:
                if numeric > Decimal(self.max_value):
                    return f"{self.field_name} must be at most {self.max_value}"
            except InvalidOperation:
                pass
        return None
