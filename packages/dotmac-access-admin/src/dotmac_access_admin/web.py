"""Access administration's facet-relative `staff` surface (module contract 2).

Held to the same rules as every packaged surface: thin wrappers (queries live in
`service`), `render()` for HTML, both CSRF transports in the templates, and
"re-render at 200" on a validation or conflict failure. Routes never name a
prefix; sibling links are built with `surface_path`/`surface_url`.

Templates are package data under the `access_admin` namespace.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from dotmac_kernel.models import Party, Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_permission
from dotmac_kernel.web_surfaces import surface_path
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from dotmac_access_admin import service

router = APIRouter(prefix="/access", tags=["web"])

PAGE_SIZE = 25
_NS = "access_admin/admin/access_admin/"
_PKG_DIR = Path(__file__).resolve().parent


def template_dir() -> Path:
    return _PKG_DIR / "templates"


def _redirect(request: Request, local: str, **params: object) -> RedirectResponse:
    target = surface_path(request, local, **params)
    response = RedirectResponse(url=target, status_code=303)
    return response


def _detail_response(
    request: Request,
    db: Session,
    tenant: Tenant,
    party_id: UUID,
    *,
    error: str | None = None,
    notice: str | None = None,
) -> HTMLResponse:
    user = service.get_user(db, tenant, party_id)
    roles = service.list_roles(db, tenant)
    return render(
        request,
        _NS + "detail.html",
        {
            "active_nav": "access",
            "page_title": user.display_name,
            "user": user,
            "roles": [r for r in roles if r.id is not None],
            "error": error,
            "notice": notice,
        },
    )


@router.get("", name="list")
def users_index(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("access_admin.users.read")),
) -> HTMLResponse:
    users = service.list_users(
        db, tenant, q=q, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE
    )
    total = service.count_users(db, tenant, q=q)
    return render(
        request,
        _NS + "index.html",
        {
            "active_nav": "access",
            "page_title": "Users and access",
            "users": users,
            "q": q or "",
            "page": page,
            "total": total,
            "total_pages": max(1, -(-total // PAGE_SIZE)),
        },
    )


@router.get("/create", name="create_form")
def user_create_form(
    request: Request,
    party: Party = Depends(require_web_permission("access_admin.users.manage")),
) -> HTMLResponse:
    return render(
        request,
        _NS + "create.html",
        {"active_nav": "access", "page_title": "New user", "error": None, "form": {}},
    )


@router.post("", response_model=None, name="create")
async def user_create_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_web_permission("access_admin.users.manage")),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    raw = {
        "email": str(form_data.get("email", "")).strip(),
        "first_name": str(form_data.get("first_name", "")).strip(),
        "last_name": str(form_data.get("last_name", "")).strip(),
    }
    if not all(raw.values()) or "@" not in raw["email"]:
        error: str | None = "Email, first name and last name are required"
    else:
        error = None
    if error is None:
        try:
            created = service.create_user(db, tenant, actor, **raw)
        except (BadRequestError, ConflictError) as exc:
            error = str(exc)
        else:
            return _redirect(request, "detail", party_id=str(created.user.id))
    return render(
        request,
        _NS + "create.html",
        {"active_nav": "access", "page_title": "New user", "error": error, "form": raw},
    )


@router.get("/roles", name="roles")
def roles_index(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("access_admin.roles.read")),
) -> HTMLResponse:
    return render(
        request,
        _NS + "roles.html",
        {
            "active_nav": "access",
            "page_title": "Roles",
            "roles": service.list_roles(db, tenant),
            "error": None,
            "form": {},
        },
    )


@router.post("/roles", response_model=None, name="role_create")
async def role_create_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_web_permission("access_admin.roles.manage")),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    raw = {
        "slug": str(form_data.get("slug", "")).strip(),
        "name": str(form_data.get("name", "")).strip(),
    }
    error: str | None = None
    try:
        from dotmac_access_admin.schemas import RoleCreate

        payload = RoleCreate(**raw)
        service.create_role(db, tenant, actor, slug=payload.slug, name=payload.name)
    except (BadRequestError, ConflictError) as exc:
        error = str(exc)
    except ValueError:
        error = "Slug (lowercase letters, digits, hyphens) and name are required"
    if error is None:
        return _redirect(request, "roles")
    return render(
        request,
        _NS + "roles.html",
        {
            "active_nav": "access",
            "page_title": "Roles",
            "roles": service.list_roles(db, tenant),
            "error": error,
            "form": raw,
        },
    )


@router.get("/{party_id}", name="detail")
def user_detail(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("access_admin.users.read")),
) -> HTMLResponse:
    return _detail_response(request, db, tenant, party_id)


@router.post("/{party_id}/grants", response_model=None, name="grant")
async def grant_submit(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_web_permission("access_admin.grants.manage")),
) -> HTMLResponse:
    form_data = await request.form()
    try:
        role_id = UUID(str(form_data.get("role_id", "")))
        outcome = service.grant_role(
            db, tenant, actor, party_id=party_id, role_id=role_id
        )
    except (ValueError, BadRequestError, ConflictError, NotFoundError) as exc:
        return _detail_response(
            request, db, tenant, party_id, error=str(exc) or "Invalid role"
        )
    notice = f"Granted {outcome.role_slug}."
    if outcome.warnings:
        notice += " Warning: " + " ".join(w.message for w in outcome.warnings)
    return _detail_response(request, db, tenant, party_id, notice=notice)


@router.post(
    "/{party_id}/grants/{role_id}/revoke", response_model=None, name="revoke_grant"
)
def revoke_grant_submit(
    request: Request,
    party_id: UUID,
    role_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_web_permission("access_admin.grants.manage")),
) -> HTMLResponse:
    try:
        service.revoke_grant(db, tenant, actor, party_id=party_id, role_id=role_id)
    except (ConflictError, NotFoundError) as exc:
        return _detail_response(request, db, tenant, party_id, error=str(exc))
    return _detail_response(request, db, tenant, party_id, notice="Role revoked.")


@router.post("/{party_id}/deactivate", response_model=None, name="deactivate")
def deactivate_submit(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_web_permission("access_admin.users.manage")),
) -> HTMLResponse:
    try:
        service.deactivate_user(db, tenant, actor, party_id=party_id)
    except (ConflictError, NotFoundError) as exc:
        return _detail_response(request, db, tenant, party_id, error=str(exc))
    return _detail_response(request, db, tenant, party_id, notice="User deactivated.")


@router.post("/{party_id}/reactivate", response_model=None, name="reactivate")
def reactivate_submit(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_web_permission("access_admin.users.manage")),
) -> HTMLResponse:
    try:
        service.reactivate_user(db, tenant, actor, party_id=party_id)
    except (ConflictError, NotFoundError) as exc:
        return _detail_response(request, db, tenant, party_id, error=str(exc))
    return _detail_response(
        request, db, tenant, party_id, notice="User reactivated (no grants restored)."
    )


@router.post("/{party_id}/leaver-revoke", response_model=None, name="leaver_revoke")
async def leaver_revoke_submit(
    request: Request,
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_web_permission("access_admin.users.revoke")),
) -> HTMLResponse:
    form_data = await request.form()
    if str(form_data.get("confirm", "")) != "yes":
        return _detail_response(
            request, db, tenant, party_id, error="Confirm the revoke to continue."
        )
    try:
        outcome = service.leaver_revoke(db, tenant, actor, party_id=party_id)
    except (ConflictError, NotFoundError) as exc:
        return _detail_response(request, db, tenant, party_id, error=str(exc))
    return _detail_response(
        request,
        db,
        tenant,
        party_id,
        notice=(
            f"Revoked {outcome.grants_revoked} grant(s) and "
            f"{outcome.sessions_revoked} session(s); user deactivated."
        ),
    )
