"""Custom fields service — definitions CRUD, value validation, value storage.

Port of `dotmac_erp:app/services/finance/automation/custom_fields.py`
(`CustomFieldsService`), generalized for this app's tenant model and
extended with FOUR gap-closures ERP declared but never built (verified,
logged in `docs/superpowers/upstream-findings.md`):

1. **Per-entity limit enforcement** — ERP's `custom_fields/max_per_entity`
   setting existed in its spec registry but no code path ever read it.
   `create_field` here resolves it via `app.core.settings_resolver.
   resolve_value` and rejects creation once the tenant's active field count
   for that `entity_type` reaches it.
2. **min_value/max_value enforcement for NUMBER/DECIMAL** — ERP's model
   stored these columns; `validate_value` never checked them. Closed in
   `CustomFieldDefinition.validate_value` (`models.py`), compared as
   `Decimal`.
3. **Unknown field codes are errors, not silently ignored** — ERP's
   `validate_custom_fields` had a bare `continue` for a field code with no
   matching definition. `validate_values` here raises instead — a starter
   should fail loudly on a caller-side typo rather than silently drop data.
4. **The value-storage layer itself** — ERP's service only validated field
   *definitions* against a `field_values` dict callers built and stored
   themselves; there was no `set_values`/`get_values` pair reading/writing
   a real column. `set_values`/`get_values` here read/write the entity's
   own `custom_fields` JSONB column (see `Party.custom_fields`), resolved
   generically via `registry.resolve_entity` so any future registered
   entity gets this for free.

Other adaptations from the ERP port:
- `organization_id` -> `tenant_id` throughout; queries filter explicitly
  (mirroring ERP's explicit `organization_id ==` filters) rather than
  relying solely on Postgres RLS, since this module's unit tests run on
  RLS-less SQLite.
- `HTTPException` -> domain exceptions: duplicate field code ->
  `ConflictError`, invalid field code / limit reached / validation failure
  -> `BadRequestError`, missing definition/entity row -> `NotFoundError`.
- `field_code.isidentifier()` check kept verbatim.
- `update_field` keeps ERP's `entity_type`/`field_code` immutability forbid.
- `delete` (ERP's hard/soft split) narrows to just the soft path —
  `deactivate_field` — this app has no hard-delete requirement yet.
- MULTISELECT membership validation is new (ERP never validated it at all);
  see `CustomFieldDefinition.validate_value` in `models.py`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.settings_models import SettingDomain
from app.core.settings_resolver import resolve_value
from app.features.custom_fields.models import CustomFieldDefinition
from app.features.custom_fields.registry import resolve_entity
from app.features.custom_fields.schemas import CustomFieldCreate

_DEFAULT_MAX_PER_ENTITY = 20


def _active_field_count(db: Session, tenant_id: UUID, entity_type: str) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(CustomFieldDefinition)
            .where(
                CustomFieldDefinition.tenant_id == tenant_id,
                CustomFieldDefinition.entity_type == entity_type,
                CustomFieldDefinition.is_active == True,  # noqa: E712
            )
        )
        or 0
    )


def create_field(
    db: Session, tenant_id: UUID, payload: CustomFieldCreate
) -> CustomFieldDefinition:
    """Create a field definition, enforcing the per-(tenant, entity_type) limit.

    Limit check runs FIRST, ahead of the duplicate-code and identifier
    checks ERP already had — the gap this task closes (see module
    docstring, gap 1).
    """
    limit = resolve_value(
        db,
        SettingDomain.custom_fields,
        "max_per_entity",
        tenant_id=tenant_id,
        default=_DEFAULT_MAX_PER_ENTITY,
    )
    count = _active_field_count(db, tenant_id, payload.entity_type)
    if count >= limit:
        raise BadRequestError(
            f"Custom field limit reached ({limit}) for {payload.entity_type}"
        )

    existing = get_by_code(db, tenant_id, payload.entity_type, payload.field_code)
    if existing is not None:
        raise ConflictError(
            f"Field with code '{payload.field_code}' already exists "
            f"for {payload.entity_type}"
        )

    if not payload.field_code.isidentifier():
        raise BadRequestError(
            "Field code must be a valid identifier (letters, numbers, underscores)"
        )

    field = CustomFieldDefinition(tenant_id=tenant_id, **payload.model_dump())
    db.add(field)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ConflictError(
            f"Field with code '{payload.field_code}' already exists "
            f"for {payload.entity_type}"
        ) from exc
    return field


def get_field(db: Session, tenant_id: UUID, field_id: UUID) -> CustomFieldDefinition:
    field = db.scalars(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.tenant_id == tenant_id,
            CustomFieldDefinition.id == field_id,
        )
    ).first()
    if field is None:
        raise NotFoundError("Custom field not found")
    return field


def get_by_code(
    db: Session, tenant_id: UUID, entity_type: str, field_code: str
) -> CustomFieldDefinition | None:
    return db.scalars(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.tenant_id == tenant_id,
            CustomFieldDefinition.entity_type == entity_type,
            CustomFieldDefinition.field_code == field_code,
        )
    ).first()


def list_for_entity(
    db: Session,
    tenant_id: UUID,
    entity_type: str,
    *,
    is_active: bool = True,
) -> list[CustomFieldDefinition]:
    stmt = select(CustomFieldDefinition).where(
        CustomFieldDefinition.tenant_id == tenant_id,
        CustomFieldDefinition.entity_type == entity_type,
    )
    if is_active:
        stmt = stmt.where(CustomFieldDefinition.is_active == True)  # noqa: E712
    stmt = stmt.order_by(
        CustomFieldDefinition.section_name,
        CustomFieldDefinition.display_order,
        CustomFieldDefinition.field_name,
    )
    return list(db.scalars(stmt).all())


def update_field(
    db: Session, tenant_id: UUID, field_id: UUID, updates: dict[str, Any]
) -> CustomFieldDefinition:
    """Update a field definition. `entity_type`/`field_code` are immutable
    (ported forbid from ERP's `update_field`)."""
    field = get_field(db, tenant_id, field_id)

    updates = dict(updates)
    updates.pop("entity_type", None)
    updates.pop("field_code", None)

    for key, value in updates.items():
        if hasattr(field, key):
            setattr(field, key, value)

    db.flush()
    return field


def deactivate_field(
    db: Session, tenant_id: UUID, field_id: UUID
) -> CustomFieldDefinition:
    """Soft-delete — ERP's `delete()`. This app has no hard-delete requirement."""
    field = get_field(db, tenant_id, field_id)
    field.is_active = False
    db.flush()
    return field


def validate_values(
    db: Session, tenant_id: UUID, entity_type: str, values: dict[str, Any]
) -> None:
    """Validate `values` against this entity_type's active field definitions.

    Raises `BadRequestError` joining every violation with `\\n`, instead of
    ERP's `(is_valid, errors)` tuple return. Unlike ERP's
    `validate_custom_fields` (which silently `continue`d past an unknown
    field code), a code with no matching definition is an error here — gap
    2 in the module docstring.
    """
    definitions = list_for_entity(db, tenant_id, entity_type)
    definitions_by_code = {defn.field_code: defn for defn in definitions}
    errors: list[str] = []

    for defn in definitions:
        if defn.is_required:
            value = values.get(defn.field_code)
            if value is None or value == "":
                errors.append(f"{defn.field_name} is required")

    for field_code, value in values.items():
        field_def = definitions_by_code.get(field_code)
        if field_def is None:
            errors.append(f"Unknown custom field: {field_code!r}")
            continue
        is_valid, error = field_def.validate_value(value)
        if not is_valid and error:
            errors.append(error)

    if errors:
        raise BadRequestError("\n".join(errors))


def _get_entity_row(db: Session, entity_type: str, entity_id: UUID) -> Any:
    model = resolve_entity(entity_type)
    row = db.get(model, entity_id)
    if row is None:
        raise NotFoundError(f"{entity_type} {entity_id} not found")
    return row


def set_values(
    db: Session,
    tenant_id: UUID,
    entity_type: str,
    entity_id: UUID,
    values: dict[str, Any],
) -> dict[str, Any]:
    """Merge `values` into the entity row's `custom_fields` JSONB column.

    Partial-update semantics: keys present in `values` overwrite the stored
    value; a `None` value deletes that key rather than storing a JSON null.
    Entity row resolved via `registry.resolve_entity` — `db.get` returning
    `None` raises `NotFoundError` (cross-tenant rows are invisible to `db.get`
    under Postgres RLS; the unit-test suite only exercises the true-missing
    case since SQLite has no RLS).
    """
    row = _get_entity_row(db, entity_type, entity_id)
    validate_values(db, tenant_id, entity_type, values)

    merged = dict(row.custom_fields or {})
    for key, value in values.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value

    row.custom_fields = merged
    flag_modified(row, "custom_fields")
    db.flush()
    return merged


def get_values(
    db: Session, tenant_id: UUID, entity_type: str, entity_id: UUID
) -> dict[str, Any]:
    row = _get_entity_row(db, entity_type, entity_id)
    return dict(row.custom_fields or {})


__all__ = [
    "create_field",
    "deactivate_field",
    "get_by_code",
    "get_field",
    "get_values",
    "list_for_entity",
    "set_values",
    "update_field",
    "validate_values",
]
