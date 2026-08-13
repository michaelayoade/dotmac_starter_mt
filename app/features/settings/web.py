"""Settings' web (HTMX) surface: `/admin/settings` (Task 7).

Mirrors `app.features.parties.web`/`app.features.rbac.web`'s established
shape — `require_web_auth` (+`require_tenant`) on every route, thin wrappers
(no direct DB query in this file; all of that lives in
`app.features.settings.service` / `dotmac_kernel.settings_resolver`), `render()`
for every HTML response, hx-post + `HX-Redirect` on successful mutations,
"re-render, don't redirect" at 200 on validation failures.

Unlike `parties`/`rbac`, there is no separate `_table.html` fragment here —
the task brief's file list for this feature is just `index.html`/
`edit.html`/`branding.html`; the settings registry is small enough (three
specs across three domains as of this task) that a single full-page render
per request is plenty, no htmx fragment swap needed.

**`GET /admin/settings`** groups every registered spec (`dotmac_kernel.
settings_resolver.all_specs()`) by domain and resolves each one's effective
value + source (tenant/platform/default) via the EXISTING
`app.features.settings.service.list_settings` path — masking of secret
values already happens there (`MASKED_SECRET_VALUE`), so this route never
touches a real secret value.

**`GET`/`POST /admin/settings/{domain}/{key}/edit`** is the generic editor
for any registered setting: a validation failure from `validate_spec_value`
(raised as `BadRequestError` by `service.update_setting`) re-renders this
same template at 200 with the error inline, matching every other form in
this app. A secret setting's `value` input is ALWAYS rendered blank
(`_render_edit_form` never echoes a secret's real value back into the form,
masked or not) with "leave blank to keep the current value" help text; if
the admin submits blank, the write is skipped entirely (no-op success
redirect) rather than validating an empty string against the spec (which
would fail `validate_spec_value`'s "value must not be null"-adjacent checks
for most types anyway).

**`GET`/`POST /admin/settings/branding`** is the friendly editor for the one
JSON-typed spec (`branding/ui_branding`) — a raw JSON textarea would work
(and the generic edit route above still functions for it), but this page
exposes the actual sub-fields (`name`/`tagline`/`logo_url`/`primary_color`/
`accent_color`) as normal form inputs and composes them back
into the `ui_branding` dict on submit, still through the SAME
`service.update_setting` write path (SoT: one service, one write path, two
edit UIs). It renders the CURRENT effective branding via
`dotmac_kernel.branding.load_branding(db, tenant.id)` — this is the first route
caller of that function (previously zero callers, per
`docs/ARCHITECTURE.md`'s no-orphan-settings note) — and passes the result as
this render's own `brand` context key, shadowing the process-global static
`brand` template global for this response only (see
`dotmac_kernel.templating`'s module docstring for that split).

This page accepts NO tenant CSS. `custom_css` and its regex sanitizer were
removed on 2026-08-13 (ADR-0006 D8), and with them the only `| safe` in this
app's templates. A write naming a retired key is refused by the shared
`settings_service.update_setting` write owner rather than dropped. The generic
JSON editor and settings API pass their raw values to that owner; this friendly
adapter preserves the presence of any injected retired form key so the owner
can refuse it. The route is not a second enforcement point.
"""

from __future__ import annotations

import json

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.branding import RETIRED_BRAND_KEYS, load_branding
from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.exceptions import BadRequestError, NotFoundError
from dotmac_kernel.models import Tenant
from dotmac_kernel.settings_admin import all_specs, get_spec
from dotmac_kernel.settings_models import SettingDomain, SettingValueType
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_auth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.features.settings import service as settings_service
from app.features.settings.schemas import SettingOut

router = APIRouter(prefix="/admin/settings", tags=["web"])


# ---------------------------------------------------------------------------
# GET /admin/settings — grouped-by-domain list
# ---------------------------------------------------------------------------


@router.get("")
def index(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    domains = sorted({spec.domain for spec in all_specs()}, key=lambda d: d.value)
    grouped = {
        domain.value: settings_service.list_settings(db, tenant, domain.value)
        for domain in domains
    }
    return render(
        request,
        "admin/settings/index.html",
        {
            "active_nav": "settings",
            "page_title": "Settings",
            "grouped": grouped,
        },
    )


# ---------------------------------------------------------------------------
# GET/POST /admin/settings/{domain}/{key}/edit — generic per-key editor
# ---------------------------------------------------------------------------


def _find_setting(db: Session, tenant: Tenant, domain: str, key: str) -> SettingOut:
    """`service.list_settings` raises `NotFoundError` for an unregistered
    `domain` string; a registered domain with no matching `key` raises it
    here instead — both paths degrade to the same branded 404, never a raw
    lookup error.
    """
    for item in settings_service.list_settings(db, tenant, domain):
        if item.key == key:
            return item
    raise NotFoundError(f"Unknown setting: {domain}/{key}")


def _render_edit_form(
    request: Request,
    *,
    setting: SettingOut,
    errors: dict[str, str] | None = None,
    form: dict[str, object] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    if form is None:
        if setting.is_secret:
            # Never echo a secret's real (or masked) value into the form —
            # blank input + "leave blank to keep" is the only contract.
            value: object = ""
        elif setting.value_type == SettingValueType.json.value:
            value = json.dumps(setting.value, indent=2)
        else:
            value = setting.value
        form = {"value": value}
    return render(
        request,
        "admin/settings/edit.html",
        {
            "active_nav": "settings",
            "page_title": f"Edit {setting.label or setting.key}",
            "setting": setting,
            "errors": errors or {},
            "form": form,
        },
        status_code=status_code,
    )


@router.get("/{domain}/{key}/edit")
def edit_key_form(
    request: Request,
    domain: str,
    key: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    setting = _find_setting(db, tenant, domain, key)
    return _render_edit_form(request, setting=setting)


@router.post("/{domain}/{key}/edit", response_model=None)
async def edit_key_submit(
    request: Request,
    domain: str,
    key: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    setting = _find_setting(db, tenant, domain, key)
    form_data = await request.form()

    if setting.is_secret:
        raw_value = str(form_data.get("value", "")).strip()
        if not raw_value:
            # "Leave blank to keep the current value" — a no-op write, not a
            # validation failure against an empty string.
            redirect_url = "/admin/settings"
            response = RedirectResponse(url=redirect_url, status_code=302)
            response.headers["HX-Redirect"] = redirect_url
            return response
        value: object = raw_value
    elif setting.value_type == SettingValueType.boolean.value:
        value = "value" in form_data
    elif setting.value_type == SettingValueType.json.value:
        raw_value = str(form_data.get("value", "")).strip()
        try:
            value = json.loads(raw_value) if raw_value else {}
        except json.JSONDecodeError:
            return _render_edit_form(
                request,
                setting=setting,
                errors={"value": "Must be valid JSON"},
                form={"value": raw_value},
            )
    else:
        value = str(form_data.get("value", "")).strip()

    try:
        settings_service.update_setting(db, tenant, domain, key, value)
    except BadRequestError as exc:
        return _render_edit_form(
            request,
            setting=setting,
            errors={"value": str(exc)},
            form={"value": value},
        )

    spec = get_spec(SettingDomain(domain), key)
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=auth["party"].id,
        action="settings.update",
        entity_type="setting",
        entity_id=key,
        details={"domain": domain, "key": key, "is_secret": spec.is_secret},
    )

    redirect_url = "/admin/settings"
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.headers["HX-Redirect"] = redirect_url
    return response


# ---------------------------------------------------------------------------
# GET/POST /admin/settings/branding — friendly ui_branding editor
# ---------------------------------------------------------------------------


def _branding_form(current: dict[str, object]) -> dict[str, str]:
    return {
        "name": str(current.get("name", "") or ""),
        "tagline": str(current.get("tagline", "") or ""),
        "logo_url": str(current.get("logo_url", "") or ""),
        "primary_color": str(current.get("primary_color", "") or ""),
        "accent_color": str(current.get("accent_color", "") or ""),
    }


def _render_branding_form(
    request: Request,
    db: Session,
    tenant: Tenant,
    *,
    errors: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    current = load_branding(db, tenant.id)
    return render(
        request,
        "admin/settings/branding.html",
        {
            "active_nav": "settings",
            "page_title": "Branding",
            "brand": current,
            "errors": errors or {},
            "form": form or _branding_form(current),
        },
        status_code=status_code,
    )


@router.get("/branding")
def branding_form(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    return _render_branding_form(request, db, tenant)


@router.post("/branding", response_model=None)
async def branding_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    raw = {
        "name": str(form_data.get("name", "")).strip(),
        "tagline": str(form_data.get("tagline", "")).strip(),
        "logo_url": str(form_data.get("logo_url", "")).strip(),
        "primary_color": str(form_data.get("primary_color", "")).strip(),
        "accent_color": str(form_data.get("accent_color", "")).strip(),
    }
    # Keep retired input visible to the one write owner. Silently dropping an
    # injected field would tell an API/form caller its CSS was accepted even
    # though it can never render. This adapter does not decide the policy: it
    # forwards the retired key and `update_setting` owns the refusal.
    for retired_key in RETIRED_BRAND_KEYS:
        if retired_key in form_data:
            raw[retired_key] = str(form_data.get(retired_key, ""))

    try:
        settings_service.update_setting(db, tenant, "branding", "ui_branding", raw)
    except BadRequestError as exc:
        return _render_branding_form(
            request, db, tenant, errors={"_form": str(exc)}, form=raw
        )

    ui_branding_spec = get_spec(SettingDomain.branding, "ui_branding")
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=auth["party"].id,
        action="settings.update",
        entity_type="setting",
        entity_id="ui_branding",
        details={
            "domain": "branding",
            "key": "ui_branding",
            "is_secret": ui_branding_spec.is_secret,
        },
    )

    redirect_url = "/admin/settings/branding"
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.headers["HX-Redirect"] = redirect_url
    return response


__all__ = ["router"]
