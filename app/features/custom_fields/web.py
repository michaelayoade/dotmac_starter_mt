"""Custom fields' web (HTMX) surface: `/admin/custom-fields` (Task 7).

Mirrors `app.features.parties.web`/`app.features.rbac.web`'s established
shape — `require_web_auth` (+`require_tenant`) on every route, thin wrappers
(no direct DB query in this file; all of that lives in
`app.features.custom_fields.service`), `render()` for every HTML response,
hx-post + `HX-Redirect` on successful mutations, "re-render, don't redirect"
at 200 on validation/conflict failures. `GET /admin/custom-fields` follows
the same fragment-vs-full-page convention (`HX-Request` header gets
`_table.html`, a plain GET gets `index.html`, which `{% include %}`s that
same fragment once).

**Cross-feature UI composition (the pattern this task introduces — full
writeup lands in docs/ARCHITECTURE.md in Task 8):** the party detail page
(`app.features.parties.web.detail` / `templates/admin/parties/detail.html`)
needs to show and edit a party's custom field values, but `parties` may
never import `custom_fields` (features are independent of each other —
`pyproject.toml`'s import-linter contract). The two routes below —
`GET`/`POST /admin/custom-fields/party/{party_id}/values-panel` — are owned
entirely by THIS feature: they read/write via
`app.features.custom_fields.service` (`list_for_entity`/`get_values`/
`set_values`, all already generic over `registry.ENTITY_MODELS`) and render
`templates/admin/custom_fields/_values_panel.html` (also owned here — the
feature that renders a partial owns that partial, per this task's file
list). `templates/admin/parties/detail.html` references ONLY the URL
(`hx-get="/admin/custom-fields/party/{{ party.id }}/values-panel"
hx-trigger="load"`) — zero Python import, composition happens entirely in
the browser via htmx's lazy-load-on-render.

The panel's own form posts to the SECOND route below (a WEB route, not the
JSON API's `PUT /custom-fields/{entity_type}/{entity_id}/values`) so the web
surface stays HTML-only end to end: an htmx form always sends
`Accept: text/html`, and the JSON API route would return `application/json`
regardless of that header (FastAPI's `response_model` decides the response
media type, not content negotiation) — htmx would swap that JSON blob in as
literal text, which is not what we want. This route calls the SAME service
function (`set_values` — single source of truth, two thin surfaces: one
JSON, one HTML) and re-renders the fragment, with validation errors inline
at 200 just like every other form in this app.

Only the `party` entity type is wired today (matching the task brief's
literal route shape) — a future registered entity gets its own
`/admin/custom-fields/<entity_type>/{id}/values-panel` pair the same way,
reusing every helper below except the hardcoded `_PARTY_ENTITY_TYPE`
constant.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.exceptions import BadRequestError, ConflictError
from dotmac_kernel.models import Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_auth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.features.custom_fields import service as custom_fields_service
from app.features.custom_fields.models import CustomFieldDefinition, CustomFieldType
from app.features.custom_fields.schemas import CustomFieldCreate, CustomFieldUpdate

router = APIRouter(prefix="/admin/custom-fields", tags=["web"])

_PARTY_ENTITY_TYPE = "party"


def _field_errors(exc: ValidationError) -> dict[str, str]:
    errors: dict[str, str] = {}
    for error in exc.errors():
        loc = error.get("loc", ())
        field = str(loc[0]) if loc else "_form"
        errors[field] = str(error.get("msg", "Invalid value"))
    return errors


# ---------------------------------------------------------------------------
# GET /admin/custom-fields — definitions list per entity_type
# ---------------------------------------------------------------------------


@router.get("")
def index(
    request: Request,
    entity_type: str | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    entity_types = custom_fields_service.list_entity_types()
    selected = entity_type if entity_type in entity_types else None
    if selected is None and entity_types:
        selected = entity_types[0]
    definitions = (
        custom_fields_service.list_for_entity(db, tenant.id, selected, is_active=False)
        if selected
        else []
    )
    context = {
        "entity_types": entity_types,
        "selected_entity_type": selected or "",
        "definitions": definitions,
    }
    if request.headers.get("HX-Request"):
        return render(request, "admin/custom_fields/_table.html", context)
    context.update({"active_nav": "custom_fields", "page_title": "Custom Fields"})
    return render(request, "admin/custom_fields/index.html", context)


# ---------------------------------------------------------------------------
# GET/POST /admin/custom-fields/create, /{field_id}/edit — definition form
# ---------------------------------------------------------------------------


def _options_to_text(field_options: dict[str, Any] | None) -> str:
    if not field_options:
        return ""
    lines: list[str] = []
    for opt in field_options.get("options", []):
        value = str(opt.get("value", ""))
        label = str(opt.get("label", value))
        lines.append(value if label == value else f"{value}|{label}")
    return "\n".join(lines)


def _parse_options(raw_text: str) -> dict[str, Any] | None:
    """Parse the options textarea (`value|label` per line, `label` optional
    and defaulting to `value`) into the `field_options` JSON shape
    `CustomFieldDefinition` expects: `{"options": [{"value", "label"}, ...]}`.
    Blank lines are skipped; returns `None` for no non-blank lines so a
    non-SELECT/MULTISELECT submission never manufactures an empty options
    dict.
    """
    options: list[dict[str, str]] = []
    for line in raw_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        value, _, label = stripped.partition("|")
        value = value.strip()
        label = label.strip() or value
        options.append({"value": value, "label": label})
    return {"options": options} if options else None


def _field_to_form(field: CustomFieldDefinition) -> dict[str, Any]:
    return {
        "entity_type": field.entity_type,
        "field_code": field.field_code,
        "field_name": field.field_name,
        "field_type": field.field_type.value,
        "description": field.description or "",
        "options_text": _options_to_text(field.field_options),
        "is_required": field.is_required,
        "default_value": field.default_value or "",
        "validation_regex": field.validation_regex or "",
        "validation_message": field.validation_message or "",
        "min_value": field.min_value or "",
        "max_value": field.max_value or "",
        "max_length": str(field.max_length) if field.max_length is not None else "",
        "display_order": str(field.display_order),
        "section_name": field.section_name or "",
        "placeholder": field.placeholder or "",
        "help_text": field.help_text or "",
        "show_in_list": field.show_in_list,
        "show_in_form": field.show_in_form,
        "show_in_detail": field.show_in_detail,
    }


def _form_view(form_data: FormData) -> dict[str, Any]:
    """Every submitted field, as strings/bools — used BOTH to re-render a
    failed submit (so the admin doesn't lose their input) and as the source
    `_raw_from_form` transforms into the create/update payload. Kept
    separate from that payload dict so a failed Pydantic coercion (e.g. a
    non-numeric `max_length`) still has the exact string the admin typed to
    redisplay, not a coerced/`None`-d value.
    """
    return {
        "entity_type": str(form_data.get("entity_type", "")).strip(),
        "field_code": str(form_data.get("field_code", "")).strip(),
        "field_name": str(form_data.get("field_name", "")).strip(),
        "field_type": str(form_data.get("field_type", "TEXT")).strip(),
        "description": str(form_data.get("description", "")),
        "options_text": str(form_data.get("options_text", "")),
        "is_required": "is_required" in form_data,
        "default_value": str(form_data.get("default_value", "")),
        "validation_regex": str(form_data.get("validation_regex", "")),
        "validation_message": str(form_data.get("validation_message", "")),
        "min_value": str(form_data.get("min_value", "")),
        "max_value": str(form_data.get("max_value", "")),
        "max_length": str(form_data.get("max_length", "")),
        "display_order": str(form_data.get("display_order", "0")),
        "section_name": str(form_data.get("section_name", "")),
        "placeholder": str(form_data.get("placeholder", "")),
        "help_text": str(form_data.get("help_text", "")),
        "show_in_list": "show_in_list" in form_data,
        "show_in_form": "show_in_form" in form_data,
        "show_in_detail": "show_in_detail" in form_data,
    }


def _raw_from_form(view: dict[str, Any]) -> dict[str, Any]:
    field_type = view["field_type"]
    return {
        "entity_type": view["entity_type"],
        "field_code": view["field_code"],
        "field_name": view["field_name"],
        "field_type": field_type,
        "description": view["description"].strip() or None,
        "field_options": (
            _parse_options(view["options_text"])
            if field_type in {"SELECT", "MULTISELECT"}
            else None
        ),
        "is_required": view["is_required"],
        "default_value": view["default_value"].strip() or None,
        "validation_regex": view["validation_regex"].strip() or None,
        "validation_message": view["validation_message"].strip() or None,
        "min_value": view["min_value"].strip() or None,
        "max_value": view["max_value"].strip() or None,
        "max_length": view["max_length"].strip() or None,
        "display_order": view["display_order"].strip() or "0",
        "section_name": view["section_name"].strip() or None,
        "placeholder": view["placeholder"].strip() or None,
        "help_text": view["help_text"].strip() or None,
        "show_in_list": view["show_in_list"],
        "show_in_form": view["show_in_form"],
        "show_in_detail": view["show_in_detail"],
    }


def _render_form(
    request: Request,
    *,
    mode: str,
    field: CustomFieldDefinition | None = None,
    entity_type: str | None = None,
    errors: dict[str, str] | None = None,
    form: dict[str, Any] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    entity_types = custom_fields_service.list_entity_types()
    if form is None:
        if field is not None:
            form = _field_to_form(field)
        else:
            default_entity_type = entity_type or (
                entity_types[0] if entity_types else ""
            )
            form = {
                "entity_type": default_entity_type,
                "field_type": "TEXT",
                "display_order": "0",
                "show_in_form": True,
                "show_in_detail": True,
            }
    page_title = f"Edit {field.field_name}" if field is not None else "New Custom Field"
    return render(
        request,
        "admin/custom_fields/form.html",
        {
            "active_nav": "custom_fields",
            "page_title": page_title,
            "mode": mode,
            "field": field,
            "entity_types": entity_types,
            "field_types": [t.value for t in CustomFieldType],
            "errors": errors or {},
            "form": form,
        },
        status_code=status_code,
    )


@router.get("/create")
def create_form(
    request: Request,
    entity_type: str | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    return _render_form(request, mode="create", entity_type=entity_type)


@router.post("", response_model=None)
async def create_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    view = _form_view(form_data)
    raw = _raw_from_form(view)
    try:
        payload = CustomFieldCreate(**raw)
    except ValidationError as exc:
        return _render_form(
            request, mode="create", errors=_field_errors(exc), form=view
        )

    try:
        field = custom_fields_service.create_field(db, tenant.id, payload)
    except (BadRequestError, ConflictError) as exc:
        return _render_form(
            request, mode="create", errors={"_form": str(exc)}, form=view
        )

    redirect_url = f"/admin/custom-fields?entity_type={field.entity_type}"
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.headers["HX-Redirect"] = redirect_url
    return response


@router.get("/{field_id}/edit")
def edit_form(
    request: Request,
    field_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    field = custom_fields_service.get_field(db, tenant.id, field_id)
    return _render_form(request, mode="edit", field=field)


@router.post("/{field_id}/edit", response_model=None)
async def edit_submit(
    request: Request,
    field_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    field = custom_fields_service.get_field(db, tenant.id, field_id)
    form_data = await request.form()
    view = _form_view(form_data)
    raw = _raw_from_form(view)
    # entity_type/field_code are immutable (service.update_field's docstring)
    # — never even acceptable input on this route, not just silently dropped.
    raw.pop("entity_type", None)
    raw.pop("field_code", None)
    try:
        payload = CustomFieldUpdate(**raw)
    except ValidationError as exc:
        return _render_form(
            request, mode="edit", field=field, errors=_field_errors(exc), form=view
        )

    try:
        custom_fields_service.update_field(
            db, tenant.id, field_id, payload.model_dump()
        )
    except BadRequestError as exc:
        return _render_form(
            request, mode="edit", field=field, errors={"_form": str(exc)}, form=view
        )

    redirect_url = f"/admin/custom-fields?entity_type={field.entity_type}"
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.headers["HX-Redirect"] = redirect_url
    return response


@router.post("/{field_id}/deactivate", response_model=None)
def deactivate(
    request: Request,
    field_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> RedirectResponse:
    field = custom_fields_service.deactivate_field(db, tenant.id, field_id)
    redirect_url = f"/admin/custom-fields?entity_type={field.entity_type}"
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.headers["HX-Redirect"] = redirect_url
    return response


# ---------------------------------------------------------------------------
# GET/POST /admin/custom-fields/party/{party_id}/values-panel — the
# cross-feature UI composition fragment (see module docstring).
# ---------------------------------------------------------------------------


def _values_from_form(
    form_data: FormData, definitions: list[CustomFieldDefinition]
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for defn in definitions:
        code = defn.field_code
        if defn.field_type == CustomFieldType.BOOLEAN:
            values[code] = code in form_data
        elif defn.field_type == CustomFieldType.MULTISELECT:
            selected = form_data.getlist(code)
            values[code] = list(selected) if selected else None
        else:
            raw_value = str(form_data.get(code, "")).strip()
            values[code] = raw_value or None
    return values


def _detail_only_definitions(
    db: Session, tenant: Tenant
) -> list[CustomFieldDefinition]:
    """The single owner of the read-only "Details" section's visibility rule
    (Task 5 review finding 1): `show_in_detail` renders read-only; if a field
    is ALSO `show_in_form`, the editable input in the form below is the
    single rendering of that field's value — showing it again as a read-only
    row would be visible duplication (the default for BOTH flags is `True`,
    see `schemas.py`, so this was the common case, not an edge case).

    `list_for_entity`'s own `visible_in="detail"` deliberately stays a pure,
    single-flag `show_in_detail` filter — a generic, independently-tested
    query-level primitive (see its docstring and
    `test_list_for_entity_visible_in_detail_filters_by_show_in_detail`) that
    other future consumers may still want unfiltered by `show_in_form`. This
    function layers the second condition (`not show_in_form`) on top,
    in-Python, scoped to the ONE consumer (this panel's "Details" section)
    that needs the narrower "detail-only" slice, rather than overloading
    `list_for_entity`'s established semantics. Called from all three
    "Details" call sites below so the combinator has exactly one
    implementation.
    """
    detail_candidates = custom_fields_service.list_for_entity(
        db, tenant.id, _PARTY_ENTITY_TYPE, visible_in="detail"
    )
    return [defn for defn in detail_candidates if not defn.show_in_form]


def _render_values_panel(
    request: Request,
    db: Session,
    tenant: Tenant,
    *,
    party_id: UUID,
    definitions_form: list[CustomFieldDefinition],
    definitions_detail: list[CustomFieldDefinition],
    values: dict[str, Any],
    errors: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "admin/custom_fields/_values_panel.html",
        {
            "party_id": party_id,
            "definitions_form": definitions_form,
            "definitions_detail": definitions_detail,
            "values": values,
            "errors": errors or {},
        },
        status_code=status_code,
    )


@router.get("/party/{party_id}/values-panel")
def party_values_panel(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    # F6 fix, narrowed by Task 5 review finding 1: two DIFFERENT slices of
    # the same entity's definitions — the edit form wants `show_in_form`
    # fields (`list_for_entity`'s `visible_in="form"`), the read-only
    # "Details" listing above it wants only the fields that are detail-only
    # (`show_in_detail` AND NOT `show_in_form` — see `_detail_only_definitions`
    # docstring for why a field visible in both must render just once, as the
    # editable input). `values` itself stays UNFILTERED (the full stored
    # dict) — both sections index into it by field_code, and the detail
    # section in particular needs to be able to display a field's stored
    # value even when that field is hidden from the form (`show_in_form=False`).
    definitions_form = custom_fields_service.list_for_entity(
        db, tenant.id, _PARTY_ENTITY_TYPE, visible_in="form"
    )
    definitions_detail = _detail_only_definitions(db, tenant)
    values = custom_fields_service.get_values(
        db, tenant.id, _PARTY_ENTITY_TYPE, party_id
    )
    return _render_values_panel(
        request,
        db,
        tenant,
        party_id=party_id,
        definitions_form=definitions_form,
        definitions_detail=definitions_detail,
        values=values,
    )


@router.post("/party/{party_id}/values-panel", response_model=None)
async def party_values_panel_submit(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    # Only `show_in_form` fields are ever extracted from the submitted form
    # data below — a field hidden from the form (`show_in_form=False`) has
    # no input on the page at all, so it's never a key in `submitted_values`
    # either. `set_values`' merge semantics (see that function's docstring)
    # then leave it untouched: this IS the F6 "hidden field survives a panel
    # save" pin, achieved by construction rather than a special case.
    definitions_form = custom_fields_service.list_for_entity(
        db, tenant.id, _PARTY_ENTITY_TYPE, visible_in="form"
    )
    form_data = await request.form()
    submitted_values = _values_from_form(form_data, definitions_form)

    try:
        updated = custom_fields_service.set_values(
            db, tenant.id, _PARTY_ENTITY_TYPE, party_id, submitted_values
        )
    except BadRequestError as exc:
        # Re-render with what the admin submitted (not the pre-existing
        # stored values) so a validation failure doesn't silently discard
        # their edits — same "re-render, don't redirect" convention as every
        # other form in this app. `_form` carries every violation, newline-
        # joined (see `service.validate_values`'s docstring); the template
        # renders it as one block rather than trying to map lines back to
        # individual fields. The failed submit never reached `set_values`'
        # persisted merge, so the read-only "Details" section is rendered
        # from the entity's ACTUAL stored values (not the rejected attempt)
        # overlaid with what was just submitted, so show_in_form fields
        # reflect the admin's attempted edit while show_in_detail-only
        # fields (absent from `submitted_values` entirely) still show their
        # real stored value instead of going blank. Detail-only slice, same
        # as the GET route above (Task 5 review finding 1) — a field also
        # `show_in_form` renders once, via the re-rendered form's input.
        definitions_detail = _detail_only_definitions(db, tenant)
        current_values = custom_fields_service.get_values(
            db, tenant.id, _PARTY_ENTITY_TYPE, party_id
        )
        return _render_values_panel(
            request,
            db,
            tenant,
            party_id=party_id,
            definitions_form=definitions_form,
            definitions_detail=definitions_detail,
            values={**current_values, **submitted_values},
            errors={"_form": str(exc)},
        )

    definitions_detail = _detail_only_definitions(db, tenant)
    return _render_values_panel(
        request,
        db,
        tenant,
        party_id=party_id,
        definitions_form=definitions_form,
        definitions_detail=definitions_detail,
        values=updated,
    )


__all__ = ["router"]
