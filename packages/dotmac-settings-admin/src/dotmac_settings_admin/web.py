"""Settings-admin's facet-relative staff surface (module contract 2).

Held to the same rules as any assembly `web.py`: thin wrapper (no query here,
all of it lives in `service`/`transfer`), `render()` for every HTML response,
both CSRF transports on every mutation (htmx header + hidden `csrf_token`),
"re-render at 200, don't redirect" on a validation failure.

**One generic renderer.** Every declared setting domain is editable here with no
per-module code. A module that needs a bespoke screen ships its own surface with
navigation in the `settings` region; it does not fork this one.

**Secrets.** A secret-typed value is never rendered, echoed, exported or logged:
the pages say only `set` or `unset`, by name, and the edit form is write-only.

Templates are PACKAGE DATA under this package's `templates/`, composed through
the manifest's `TemplatePackage(namespace="settings_admin")`; render names begin
`settings_admin/`.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from dotmac_kernel.branding import RETIRED_BRAND_KEYS, load_branding
from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
)
from dotmac_kernel.flags import active_flags
from dotmac_kernel.models import Party, Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_permission
from dotmac_kernel.web_surfaces import surface_path
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from dotmac_settings_admin import service, transfer
from dotmac_settings_admin.router import APPROVE, MANAGE, READ, actor_from, may_approve

router = APIRouter(prefix="/settings", tags=["web"])

_PKG_DIR = Path(__file__).resolve().parent
_T = "settings_admin/admin/settings_admin/"
_ERRORS = (BadRequestError, ConflictError, ForbiddenError)


def template_dir() -> Path:
    """This package's namespaced Jinja root, resolved by package path."""
    return _PKG_DIR / "templates"


def _redirect(request: Request, route: str, **params: object) -> RedirectResponse:
    url = surface_path(request, route, **params)
    response = RedirectResponse(url=url, status_code=303)
    response.headers["HX-Redirect"] = url
    return response


def _page(
    request: Request, name: str, context: dict[str, object], status: int = 200
) -> HTMLResponse:
    context = {"active_nav": "settings", **context}
    return render(request, _T + name, context, status_code=status)


def _form_value(setting: service.SettingView, form_data: FormData) -> object:
    if setting.value_type == "boolean":
        return "value" in form_data
    raw = str(form_data.get("value", "")).strip()
    if setting.value_type == "json":
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            raise BadRequestError("Must be valid JSON") from None
    return raw


# ── Index and domain pages ──────────────────────────────────────────────────


def _index(
    request: Request,
    db: Session,
    tenant: Tenant,
    party: Party,
    *,
    error: str | None = None,
) -> HTMLResponse:
    by_owner: dict[str, list[service.DomainSummary]] = {}
    for summary in service.list_domains(db):
        by_owner.setdefault(summary.owner or "unowned", []).append(summary)
    return _page(
        request,
        "index.html",
        {
            "page_title": "Settings",
            "by_owner": by_owner,
            "proposals": service.list_proposals(db, tenant.id),
            "maker_checker": service.maker_checker_enabled(db, tenant.id),
            "can_approve": may_approve(db, tenant, party),
            "organisation": service.organisation_profile(db, tenant.id),
            "error": error,
        },
    )


@router.get("", name="index")
def index(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(READ)),
) -> HTMLResponse:
    return _index(request, db, tenant, party)


@router.get("/domains/{domain}", name="domain")
def domain_page(
    request: Request,
    domain: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(READ)),
) -> HTMLResponse:
    return _page(
        request,
        "domain.html",
        {
            "page_title": f"Settings: {domain}",
            "domain": domain,
            "items": service.list_settings(db, tenant.id, domain),
        },
    )


# ── Edit, clear, history, restore ───────────────────────────────────────────


def _edit(
    request: Request,
    setting: service.SettingView,
    *,
    error: str | None = None,
    form: Mapping[str, object] | None = None,
    status: int = 200,
) -> HTMLResponse:
    if form is None:
        if setting.is_secret:
            form = {"value": ""}  # write-only: never echoed, never masked-echoed
        elif setting.value_type == "json":
            form = {"value": json.dumps(setting.value, indent=2)}
        else:
            form = {"value": setting.value}
    return _page(
        request,
        "edit.html",
        {
            "page_title": f"Edit {setting.label or setting.key}",
            "setting": setting,
            "error": error,
            "form": form,
        },
        status,
    )


@router.get("/{domain}/{key}/edit", name="edit_form")
def edit_form(
    request: Request,
    domain: str,
    key: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(MANAGE)),
) -> HTMLResponse:
    return _edit(request, service.get_setting(db, tenant.id, domain, key))


@router.post("/{domain}/{key}/edit", response_model=None, name="edit_submit")
async def edit_submit(
    request: Request,
    domain: str,
    key: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(MANAGE)),
) -> HTMLResponse | RedirectResponse:
    setting = service.get_setting(db, tenant.id, domain, key)
    data = await request.form()
    reason = str(data.get("reason", ""))
    try:
        if setting.is_secret and not str(data.get("value", "")).strip():
            raise BadRequestError("Enter a new value; the current one is never shown.")
        service.write_setting(
            db,
            tenant.id,
            actor=actor_from(request, party),
            domain=domain,
            key=key,
            value=_form_value(setting, data),
            reason=reason,
            may_approve=may_approve(db, tenant, party),
        )
    except _ERRORS as exc:
        form = {
            "value": "" if setting.is_secret else str(data.get("value", "")),
            "reason": reason,
        }
        return _edit(request, setting, error=str(exc), form=form)
    return _redirect(request, "domain", domain=domain)


@router.post("/{domain}/{key}/clear", response_model=None, name="clear")
async def clear(
    request: Request,
    domain: str,
    key: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(MANAGE)),
) -> HTMLResponse | RedirectResponse:
    setting = service.get_setting(db, tenant.id, domain, key)
    data = await request.form()
    try:
        service.clear_setting(
            db,
            tenant.id,
            actor=actor_from(request, party),
            domain=domain,
            key=key,
            reason=str(data.get("reason", "")),
            may_approve=may_approve(db, tenant, party),
        )
    except _ERRORS as exc:
        return _edit(request, setting, error=str(exc))
    return _redirect(request, "domain", domain=domain)


@router.get("/{domain}/{key}/history", name="history")
def history(
    request: Request,
    domain: str,
    key: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(READ)),
) -> HTMLResponse:
    return _history(request, db, tenant, domain, key)


def _history(
    request: Request,
    db: Session,
    tenant: Tenant,
    domain: str,
    key: str,
    *,
    error: str | None = None,
    status: int = 200,
) -> HTMLResponse:
    setting = service.get_setting(db, tenant.id, domain, key)
    return _page(
        request,
        "history.html",
        {
            "page_title": f"History: {setting.label or setting.key}",
            "setting": setting,
            "entries": service.list_history(db, tenant.id, domain, key),
            "error": error,
        },
        status,
    )


@router.post(
    "/{domain}/{key}/history/{history_id}/restore", response_model=None, name="restore"
)
async def restore(
    request: Request,
    domain: str,
    key: str,
    history_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(MANAGE)),
) -> HTMLResponse | RedirectResponse:
    data = await request.form()
    try:
        service.restore_setting(
            db,
            tenant.id,
            actor=actor_from(request, party),
            history_id=history_id,
            reason=str(data.get("reason", "")),
            may_approve=may_approve(db, tenant, party),
        )
    except _ERRORS as exc:
        return _history(request, db, tenant, domain, key, error=str(exc))
    return _redirect(request, "history", domain=domain, key=key)


# ── Branding, flags ─────────────────────────────────────────────────────────

_BRAND_FIELDS = ("name", "tagline", "logo_url", "primary_color", "accent_color")


def _branding(
    request: Request,
    db: Session,
    tenant: Tenant,
    *,
    error: str | None = None,
    form: dict[str, str] | None = None,
    status: int = 200,
) -> HTMLResponse:
    current = load_branding(db, tenant.id)
    return _page(
        request,
        "branding.html",
        {
            "page_title": "Branding",
            "brand": current,
            "error": error,
            "form": form or {f: str(current.get(f, "") or "") for f in _BRAND_FIELDS},
        },
        status,
    )


@router.get("/branding", name="branding_form")
def branding_form(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(MANAGE)),
) -> HTMLResponse:
    return _branding(request, db, tenant)


@router.post("/branding", response_model=None, name="branding_submit")
async def branding_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(MANAGE)),
) -> HTMLResponse | RedirectResponse:
    data = await request.form()
    raw = {f: str(data.get(f, "")).strip() for f in _BRAND_FIELDS}
    # Retired input stays visible to the one write owner, which refuses it; this
    # adapter is not a second enforcement point.
    for retired in RETIRED_BRAND_KEYS:
        if retired in data:
            raw[retired] = str(data.get(retired, ""))
    try:
        service.write_setting(
            db,
            tenant.id,
            actor=actor_from(request, party),
            domain="branding",
            key="ui_branding",
            value=raw,
            reason=str(data.get("reason", "")),
            may_approve=may_approve(db, tenant, party),
        )
    except _ERRORS as exc:
        return _branding(request, db, tenant, error=str(exc), form=raw)
    return _redirect(request, "branding_form")


@router.get("/flags", name="flags")
def flags(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(READ)),
) -> HTMLResponse:
    # Declared flags and their defaults only: evaluation against tenant overrides
    # goes through a frozen kernel facility this stateless module must not call.
    rows = [{"spec": spec} for spec in active_flags().specs()]
    return _page(request, "flags.html", {"page_title": "Feature flags", "rows": rows})


# ── Export / import ─────────────────────────────────────────────────────────


@router.get("/export", name="export")
def export(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(READ)),
) -> JSONResponse:
    return JSONResponse(
        transfer.export_settings(db, tenant.id),
        headers={"Content-Disposition": 'attachment; filename="settings-export.json"'},
    )


@router.get("/import", name="import_form")
def import_form(
    request: Request,
    party: Party = Depends(require_web_permission(MANAGE)),
) -> HTMLResponse:
    return _page(
        request,
        "import.html",
        {"page_title": "Import settings", "plan": None, "error": None, "form": {}},
    )


@router.post("/import", response_model=None, name="import_submit")
async def import_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(MANAGE)),
) -> HTMLResponse | RedirectResponse:
    """Preview when no digest is posted; apply when the digest is posted back."""
    data = await request.form()
    document = str(data.get("document", ""))
    digest = str(data.get("digest", ""))
    reason = str(data.get("reason", ""))
    plan = None
    try:
        if not digest:
            plan = transfer.plan_import(db, tenant.id, document)
        else:
            transfer.apply_import(
                db,
                tenant.id,
                actor=actor_from(request, party),
                raw=document,
                reason=reason,
                expected_digest=digest,
                may_approve=may_approve(db, tenant, party),
            )
            return _redirect(request, "index")
    except _ERRORS as exc:
        return _page(
            request,
            "import.html",
            {
                "page_title": "Import settings",
                "plan": None,
                "error": str(exc),
                "form": {"document": document, "reason": reason},
            },
        )
    return _page(
        request,
        "import.html",
        {
            "page_title": "Import settings",
            "plan": plan,
            "error": None,
            "form": {"document": document, "reason": reason},
        },
    )


# ── Maker-checker proposals ─────────────────────────────────────────────────


@router.post("/proposals/{proposal_id}/{decision}", response_model=None, name="decide")
async def decide(
    request: Request,
    proposal_id: str,
    decision: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission(APPROVE)),
) -> HTMLResponse | RedirectResponse:
    data = await request.form()
    actor = actor_from(request, party)
    reason = str(data.get("reason", ""))
    try:
        if decision == "approve":
            service.approve_proposal(
                db, tenant.id, actor=actor, proposal_id=proposal_id, reason=reason
            )
        elif decision == "reject":
            service.reject_proposal(
                db, tenant.id, actor=actor, proposal_id=proposal_id, reason=reason
            )
        else:
            raise BadRequestError("Unknown decision.")
    except _ERRORS as exc:
        return _index(request, db, tenant, party, error=str(exc))
    return _redirect(request, "index")
