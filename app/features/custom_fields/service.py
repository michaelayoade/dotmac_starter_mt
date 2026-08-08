"""Custom fields service — definitions CRUD, value validation, value storage.

Port of `dotmac_erp:app/services/finance/automation/custom_fields.py`
(`CustomFieldsService`), generalized for this app's tenant model and
extended with FOUR gap-closures ERP declared but never built (verified,
logged in `docs/superpowers/upstream-findings.md`):

1. **Per-entity limit enforcement** — ERP's `custom_fields/max_per_entity`
   setting existed in its spec registry but no code path ever read it.
   `create_field` here resolves it via `dotmac_kernel.settings_resolver.
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

Task 9 review fixes (same day, before Task 10's router lands):
- `create_field` calls `registry.resolve_entity(payload.entity_type)` first,
  so an unregistered `entity_type` fails loudly with a message naming the
  extension point (`registry.py`'s `ENTITY_MODELS`) instead of silently
  creating a definition that `set_values`/`get_values` can never resolve.
- `create_field` / `update_field` reject a non-numeric `min_value`/
  `max_value` string on a NUMBER/DECIMAL definition (definition
  self-consistency — `_validate_min_max_numeric`); previously
  `CustomFieldDefinition._range_error` caught the resulting
  `InvalidOperation` and silently skipped the range check.
- `validate_value` (`models.py`) gained real checks for BOOLEAN (must be a
  `bool`, not a truthy string) and DATE/DATETIME (must be an ISO 8601
  string parseable by `date.fromisoformat`/`datetime.fromisoformat`, or the
  matching Python object).
- **URL, PHONE, and CURRENCY values are intentionally NOT format-validated**
  by default — real-world formats vary too much per project (international
  phone formats, currency codes vs. symbols, internal vs. public URLs) for
  a starter to bake in one opinion. Set `validation_regex` (+ optionally
  `validation_message`) on the field definition to enforce your project's
  format; see `CustomFieldDefinition.validate_value` in `models.py`.

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

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from uuid import UUID

from dotmac_kernel.db import conflict_savepoint
from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from dotmac_kernel.settings_resolver import resolve
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.features.custom_fields.models import CustomFieldDefinition, CustomFieldType
from app.features.custom_fields.registry import ENTITY_MODELS, resolve_entity
from app.features.custom_fields.schemas import CustomFieldCreate
from app.features.custom_fields.spec import MAX_PER_ENTITY

_NUMERIC_FIELD_TYPES = (CustomFieldType.NUMBER, CustomFieldType.DECIMAL)
_MIN_MAX_CONSISTENCY_KEYS = {"min_value", "max_value", "field_type"}
_OPTION_FIELD_TYPES = (CustomFieldType.SELECT, CustomFieldType.MULTISELECT)
_SELECT_OPTIONS_CONSISTENCY_KEYS = {"field_type", "field_options"}


def _validate_min_max_numeric(
    field_type: CustomFieldType, min_value: str | None, max_value: str | None
) -> None:
    """Definition self-consistency: `min_value`/`max_value` are free-text
    columns (see `models.py`), so a NUMBER/DECIMAL definition can otherwise
    be created with a non-numeric bound that `validate_value`'s `_range_error`
    would silently swallow (`InvalidOperation` caught, range check skipped)."""
    if field_type not in _NUMERIC_FIELD_TYPES:
        return
    for label, value in (("min_value", min_value), ("max_value", max_value)):
        if value is None:
            continue
        try:
            Decimal(value)
        except InvalidOperation as exc:
            raise BadRequestError(
                f"{label} must be numeric for NUMBER/DECIMAL fields"
            ) from exc


def _validate_select_options(
    field_type: CustomFieldType, field_options: dict[str, Any] | None
) -> None:
    """Task 7 review finding 3: `CustomFieldDefinition.validate_value`'s
    SELECT/MULTISELECT branches only check membership `if self.field_options:`
    (see `models.py`) — an options-less SELECT/MULTISELECT definition
    silently skips membership validation forever after (the exact same
    guard-shape gap logged for dotmac_erp, see
    `docs/superpowers/upstream-findings.md` finding 2). Reject it at
    create/update time instead, same "definition self-consistency, checked
    up front" pattern as `_validate_min_max_numeric`/`_validate_regex_compiles`
    above: a SELECT/MULTISELECT definition must carry at least one non-empty
    option in `field_options["options"]`.
    """
    if field_type not in _OPTION_FIELD_TYPES:
        return
    options = (field_options or {}).get("options") or []
    if not options:
        raise BadRequestError("SELECT/MULTISELECT fields require at least one option")


def _validate_regex_compiles(validation_regex: str | None) -> None:
    """Definition self-consistency (final-review Group 4(a)): `validation_regex`
    is a free-text column (`models.py`) never checked at write time — an
    unparseable pattern (e.g. `"["`) previously only failed the first time
    some value happened to be validated against it, via an unhandled
    `re.error` out of `CustomFieldDefinition.validate_value`'s bare
    `re.match(self.validation_regex, ...)`. Compile-checked here instead, at
    create/update time, so a bad pattern 400s immediately."""
    if validation_regex is None:
        return
    try:
        re.compile(validation_regex)
    except re.error as exc:
        raise BadRequestError(
            f"validation_regex is not a valid pattern: {exc}"
        ) from exc


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

    `entity_type` is resolved against the registry FIRST — an unknown
    entity_type fails loudly with a message naming the extension point
    (`registry.py::resolve_entity`) rather than falling through to a limit
    check keyed on an entity_type that can never actually be used with
    `set_values`/`get_values`. Limit check runs next, ahead of the
    duplicate-code and identifier checks ERP already had — the gap this
    task closes (see module docstring, gap 1).
    """
    resolve_entity(payload.entity_type)
    _validate_min_max_numeric(payload.field_type, payload.min_value, payload.max_value)
    _validate_regex_compiles(payload.validation_regex)
    _validate_select_options(payload.field_type, payload.field_options)

    # Typed: `resolve` returns the spec's declared `int`, so `count >= limit`
    # below is a checked comparison rather than one against `Any`.
    limit = resolve(db, MAX_PER_ENTITY, tenant_id=tenant_id)
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
    # `db.add` happens INSIDE the savepoint, not before it: `Session.
    # begin_nested()` auto-flushes any already-pending changes as part of
    # establishing the SAVEPOINT (`_take_snapshot`) — adding `field` before
    # entering `conflict_savepoint` would let that pre-flush emit the
    # conflicting INSERT with no savepoint yet in place to protect the
    # outer transaction's `SET LOCAL` if it fails. See
    # `.superpowers/sdd/task-2-report.md`'s harness-interplay notes.
    try:
        with conflict_savepoint(db):
            db.add(field)
            db.flush()
    except IntegrityError as exc:
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
    visible_in: Literal["form", "detail", "list"] | None = None,
) -> list[CustomFieldDefinition]:
    """List this entity_type's field definitions.

    F6 fix: `visible_in` is the SINGLE query-level owner of visibility
    semantics — no template/consumer ever re-filters a definitions list by
    `show_in_form`/`show_in_detail`/`show_in_list` itself, it asks this
    function for the right slice instead:

    - `visible_in="form"` -> `show_in_form` (consumer: the values-panel EDIT
      form, `app.features.custom_fields.web.party_values_panel`, only
      renders an input for fields where this is true).
    - `visible_in="detail"` -> `show_in_detail` (consumer: the same panel's
      read-only "Details" listing, embedded in the party detail page).
    - `visible_in="list"` -> `show_in_list`. No list-VIEW consumer exists
      yet (a future entity-list admin screen would request
      `visible_in="list"` to pick its columns); today's only consumer of
      `show_in_list` is the definitions table's own "visible in" badge
      (`app.features.custom_fields.web.index` /
      `templates/admin/custom_fields/_table.html`), which reads the flag
      directly per-row to summarize it rather than filtering a list with
      it — that badge is this flag's real consumer surface today, added by
      the same task that added this parameter (F6), so `show_in_list` is no
      longer a dead control even though its list-COLUMN story is still
      future work.

    `None` (the default) returns every definition regardless of any
    show_in_* flag — every pre-existing caller (JSON API `list_for_entity`
    passthrough, `validate_values`' own internal lookup) keeps that
    behavior unchanged.
    """
    stmt = select(CustomFieldDefinition).where(
        CustomFieldDefinition.tenant_id == tenant_id,
        CustomFieldDefinition.entity_type == entity_type,
    )
    if is_active:
        stmt = stmt.where(CustomFieldDefinition.is_active == True)  # noqa: E712
    if visible_in == "form":
        stmt = stmt.where(CustomFieldDefinition.show_in_form == True)  # noqa: E712
    elif visible_in == "detail":
        stmt = stmt.where(CustomFieldDefinition.show_in_detail == True)  # noqa: E712
    elif visible_in == "list":
        stmt = stmt.where(CustomFieldDefinition.show_in_list == True)  # noqa: E712
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

    if _MIN_MAX_CONSISTENCY_KEYS & updates.keys():
        effective_type = updates.get("field_type", field.field_type)
        effective_min = updates.get("min_value", field.min_value)
        effective_max = updates.get("max_value", field.max_value)
        _validate_min_max_numeric(effective_type, effective_min, effective_max)

    if "validation_regex" in updates:
        _validate_regex_compiles(updates["validation_regex"])

    if _SELECT_OPTIONS_CONSISTENCY_KEYS & updates.keys():
        effective_type = updates.get("field_type", field.field_type)
        effective_options = updates.get("field_options", field.field_options)
        _validate_select_options(effective_type, effective_options)

    # Router must pass schema-validated dicts only — this loop trusts its
    # input (inherited ERP shape; Task 10's router owns input filtering).
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
    db: Session,
    tenant_id: UUID,
    entity_type: str,
    values: dict[str, Any],
    *,
    merged: dict[str, Any] | None = None,
) -> None:
    """Validate `values` against this entity_type's active field definitions.

    Raises `BadRequestError` joining every violation with `\\n`, instead of
    ERP's `(is_valid, errors)` tuple return. Unlike ERP's
    `validate_custom_fields` (which silently `continue`d past an unknown
    field code), a code with no matching definition is an error here — gap
    2 in the module docstring.

    Final-review Group 1 fix — required-field vs. partial-update bug:
    is-required validation (the first loop below) is checked against
    `merged` when the caller supplies it, NOT against `values` itself.
    `values` is only ever the caller's PARTIAL request; a key the caller
    didn't touch is absent from it even when the entity already has a
    stored value for it (`set_values` merges before calling this). Checking
    required-ness against the raw partial `values` therefore 400s any
    partial update that omits an already-satisfied required field — the
    headline bug this fix closes. `set_values` computes the full merged
    result (current stored values + this update, `None` entries deleted)
    and passes it as `merged`; direct callers that don't pass `merged` keep
    the old behavior (required-ness checked against `values` itself — e.g.
    a caller validating a dict that already represents the full desired
    state, or this module's own unit tests exercising `validate_values` in
    isolation).

    Type/format/option validation (the second loop) always runs against
    `values` ONLY, regardless of `merged` — an untouched stored value was
    already validated when it was written and is not re-validated here.
    """
    definitions = list_for_entity(db, tenant_id, entity_type)
    definitions_by_code = {defn.field_code: defn for defn in definitions}
    errors: list[str] = []

    required_context = values if merged is None else merged
    for defn in definitions:
        if defn.is_required:
            value = required_context.get(defn.field_code)
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

    The merge is computed BEFORE validation (final-review Group 1 fix) so
    `validate_values` can check is-required against the entity's full
    post-update state (`merged`) rather than this partial `values` dict —
    see that function's docstring. Type/option validation still runs against
    `values` only. A rejected update never reaches `row.custom_fields` —
    `merged` stays a local dict until validation passes.
    """
    row = _get_entity_row(db, entity_type, entity_id)

    merged = dict(row.custom_fields or {})
    for key, value in values.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value

    validate_values(db, tenant_id, entity_type, values, merged=merged)

    row.custom_fields = merged
    flag_modified(row, "custom_fields")
    db.flush()
    return merged


def get_values(
    db: Session, tenant_id: UUID, entity_type: str, entity_id: UUID
) -> dict[str, Any]:
    row = _get_entity_row(db, entity_type, entity_id)
    return dict(row.custom_fields or {})


def list_entity_types() -> list[str]:
    """Registered `entity_type` keys custom fields can attach to.

    Thin wrapper over `registry.ENTITY_MODELS` (sorted for stable UI
    ordering) — feeds the admin web UI's entity_type select
    (`app.features.custom_fields.web`) without that module reaching into
    the registry module directly for a one-line lookup.
    """
    return sorted(ENTITY_MODELS.keys())


__all__ = [
    "create_field",
    "deactivate_field",
    "get_by_code",
    "get_field",
    "get_values",
    "list_entity_types",
    "list_for_entity",
    "set_values",
    "update_field",
    "validate_values",
]
