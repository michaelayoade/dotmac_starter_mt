"""RBAC's web (HTMX) surface: `/admin/roles`, `/admin/role-grants`,
`/admin/audit` (Task 6).

Mirrors `app.features.parties.web`'s established shape — `require_web_auth`
(+`require_tenant`) on every route, thin wrappers (no direct DB query in
this file; all of that lives in `app.features.rbac.service`), `render()` for
every HTML response, hx-post + `HX-Redirect` on successful mutations, and
"re-render, don't redirect" at 200 on validation/conflict failures.

`/admin/roles` (list + create) and `/admin/audit` (paginated list) follow
the EXACT fragment-vs-full-page convention `parties.web.index` established:
an `HX-Request` header gets the `_*_table.html` fragment, a plain browser
GET gets the full page (which `{% include %}`s that same fragment once).

`/admin/role-grants` has no separate fragment template (see the Files list
in the task brief — only `grants.html` is listed, no `_grants.html`): the
party search box and the grant-submit form both use htmx's `hx-select`
attribute instead, so the server can ALWAYS render the full `grants.html`
page and the client picks the `#grants-content` div back out of that full
response to swap in place — same net effect (no jarring full-page reload,
no whole-document markup nested inside the triggering element) without a
second template file to keep in sync.

Feature-independence note: `list_grantable_parties` queries the CORE
`Party` model directly from `app.features.rbac.service` (documented there)
— this module never imports `app.features.parties`.
"""

from __future__ import annotations

import json
from uuid import UUID

from dotmac_kernel.audit import AuditEvent, write_audit_event
from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.exceptions import ConflictError, NotFoundError
from dotmac_kernel.models import Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_auth
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.features.rbac import service as rbac_service
from app.features.rbac.schemas import RoleCreate, RoleGrantRequest

router = APIRouter(prefix="/admin", tags=["web"])

ROLES_PAGE_SIZE = 20
AUDIT_PAGE_SIZE = 20
GRANTABLE_PARTIES_LIMIT = 50
ROLE_OPTIONS_LIMIT = 200
RECENT_GRANTS_LIMIT = 20


def _field_errors(exc: ValidationError) -> dict[str, str]:
    errors: dict[str, str] = {}
    for error in exc.errors():
        loc = error.get("loc", ())
        field = str(loc[0]) if loc else "_form"
        errors[field] = str(error.get("msg", "Invalid value"))
    return errors


# ---------------------------------------------------------------------------
# GET/POST /admin/roles
# ---------------------------------------------------------------------------


@router.get("/roles")
def roles_index(
    request: Request,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    offset = (page - 1) * ROLES_PAGE_SIZE
    roles = rbac_service.list_roles(db, tenant, limit=ROLES_PAGE_SIZE, offset=offset)
    total = rbac_service.count_roles(db, tenant)
    total_pages = max(1, -(-total // ROLES_PAGE_SIZE))
    context = {
        "roles": roles,
        "page": page,
        "total": total,
        "total_pages": total_pages,
    }
    if request.headers.get("HX-Request"):
        return render(request, "admin/rbac/_roles_table.html", context)
    context.update({"active_nav": "roles", "page_title": "Roles"})
    return render(request, "admin/rbac/roles.html", context)


def _render_role_create_form(
    request: Request,
    *,
    errors: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "admin/rbac/role_create.html",
        {
            "active_nav": "roles",
            "page_title": "New Role",
            "errors": errors or {},
            "form": form or {},
        },
        status_code=status_code,
    )


@router.get("/roles/create")
def role_create_form(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    return _render_role_create_form(request)


@router.post("/roles", response_model=None)
async def role_create_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    raw = {
        "slug": str(form_data.get("slug", "")).strip(),
        "name": str(form_data.get("name", "")).strip(),
    }
    try:
        payload = RoleCreate(**raw)
    except ValidationError as exc:
        return _render_role_create_form(request, errors=_field_errors(exc), form=raw)

    try:
        role = rbac_service.create_role(db, tenant, payload)
    except ConflictError as exc:
        return _render_role_create_form(request, errors={"_form": str(exc)}, form=raw)

    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=auth["party"].id,
        actor_type="user",
        actor_id=str(auth["party"].id),
        action="role.create",
        entity_type="role",
        entity_id=str(role.id),
        details={"slug": role.slug},
    )

    response = RedirectResponse(url="/admin/roles", status_code=302)
    response.headers["HX-Redirect"] = "/admin/roles"
    return response


# ---------------------------------------------------------------------------
# GET/POST /admin/role-grants
# ---------------------------------------------------------------------------


def _render_grants_page(
    request: Request,
    db: Session,
    tenant: Tenant,
    *,
    q: str | None,
    errors: dict[str, str] | None = None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    parties = rbac_service.list_grantable_parties(
        db, tenant, q=q, limit=GRANTABLE_PARTIES_LIMIT
    )
    roles = rbac_service.list_roles(db, tenant, limit=ROLE_OPTIONS_LIMIT, offset=0)
    recent_grants = rbac_service.list_recent_grants(
        db, tenant, limit=RECENT_GRANTS_LIMIT
    )
    return render(
        request,
        "admin/rbac/grants.html",
        {
            "active_nav": "roles",
            "page_title": "Grant Role",
            "parties": parties,
            "roles": roles,
            "recent_grants": recent_grants,
            "q": q or "",
            "errors": errors or {},
            "form": form or {},
        },
        status_code=status_code,
    )


@router.get("/role-grants")
def role_grants_index(
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    return _render_grants_page(request, db, tenant, q=q)


@router.post("/role-grants", response_model=None)
async def role_grants_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    raw = {
        "party_id": str(form_data.get("party_id", "")).strip(),
        "role_id": str(form_data.get("role_id", "")).strip(),
    }
    try:
        payload = RoleGrantRequest.model_validate(raw)
    except ValidationError as exc:
        return _render_grants_page(
            request, db, tenant, q=None, errors=_field_errors(exc), form=raw
        )

    try:
        party_role = rbac_service.assign_role(db, tenant, payload)
    except NotFoundError as exc:
        return _render_grants_page(
            request, db, tenant, q=None, errors={"_form": str(exc)}, form=raw
        )
    except ConflictError as exc:
        return _render_grants_page(
            request, db, tenant, q=None, errors={"_form": str(exc)}, form=raw
        )

    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=auth["party"].id,
        actor_type="user",
        actor_id=str(auth["party"].id),
        action="role.grant",
        entity_type="party_role",
        entity_id=str(party_role.party_id),
        details={"role_id": str(party_role.role_id)},
    )

    response = RedirectResponse(url="/admin/role-grants", status_code=302)
    response.headers["HX-Redirect"] = "/admin/role-grants"
    return response


# ---------------------------------------------------------------------------
# GET /admin/audit
# ---------------------------------------------------------------------------


def _audit_rows(
    events: list[AuditEvent], actor_names: dict[UUID, str]
) -> list[dict[str, object]]:
    """Build the per-row view model `_audit_table.html` renders: `details`
    (a `dict`) is turned into pretty-printed JSON text HERE (in Python) —
    the template renders it inside a `<pre>` as a plain string, which
    Jinja's default autoescaping escapes for us, so no `| safe` is ever
    needed for untrusted event details.
    """
    rows: list[dict[str, object]] = []
    for event in events:
        if event.actor_party_id is None:
            actor_display_name = "System"
        else:
            actor_display_name = actor_names.get(event.actor_party_id, "Unknown")
        rows.append(
            {
                "id": event.id,
                "action": event.action,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "actor_display_name": actor_display_name,
                "created_at": event.created_at,
                "details_json": json.dumps(event.details, indent=2, default=str),
            }
        )
    return rows


@router.get("/audit")
def audit_index(
    request: Request,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    offset = (page - 1) * AUDIT_PAGE_SIZE
    events = rbac_service.list_audit_events(
        db, tenant, limit=AUDIT_PAGE_SIZE, offset=offset
    )
    total = rbac_service.count_audit_events(db, tenant)
    total_pages = max(1, -(-total // AUDIT_PAGE_SIZE))
    actor_names = rbac_service.resolve_actor_display_names(
        db, tenant, (event.actor_party_id for event in events)
    )
    context = {
        "events": _audit_rows(events, actor_names),
        "page": page,
        "total": total,
        "total_pages": total_pages,
    }
    if request.headers.get("HX-Request"):
        return render(request, "admin/rbac/_audit_table.html", context)
    context.update({"active_nav": "audit", "page_title": "Audit Log"})
    return render(request, "admin/rbac/audit.html", context)


__all__ = ["router"]
