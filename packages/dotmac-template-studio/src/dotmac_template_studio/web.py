"""Template Studio's admin surface: `/admin/templates` (ADR-0006 M1).

Held to the same rules as an assembly feature's `web.py` — thin wrapper (no
query in this file), `render()` for every HTML response, `hx-post` mutations so
the CSRF header-bridge applies, and "re-render at 200, don't redirect" on a
validation or conflict failure. The governance tests that enforce those rules
walk this package too; extending their globs was part of shipping it, because a
module that escaped them would be held to a weaker standard than the assembly it
installs into.

**Templates are PACKAGE DATA.** They live in this package's own `templates/`
directory, not in the assembly's, and reach the shared Jinja environment through
`ProductAssemblySpec.packaged_template_dirs` (kernel 0.1.0a13). They are namespaced
`admin/template_studio/...` so an assembly can shadow any one of them by shipping
a file at the same path — first match wins, assembly over package.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from dotmac_kernel.models import Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_auth
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from dotmac_template_studio import service
from dotmac_template_studio.models import TEMPLATE_KINDS

router = APIRouter(prefix="/admin", tags=["web"])

_PKG_DIR = Path(__file__).resolve().parent


def template_dir() -> Path:
    """This package's Jinja directory — the path an assembly puts in
    `ProductAssemblySpec.packaged_template_dirs`. Resolved by package path, not
    CWD: a pip-installed module lives outside any assembly's working directory.
    """
    return _PKG_DIR / "templates"


@router.get("/templates")
def templates_index(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    templates = service.list_templates(db, tenant.id)
    context: dict[str, object] = {"templates": templates}
    if request.headers.get("HX-Request"):
        return render(request, "admin/template_studio/_templates_table.html", context)
    context.update({"active_nav": "templates", "page_title": "Templates"})
    return render(request, "admin/template_studio/index.html", context)


@router.get("/templates/create")
def template_create_form(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    return _render_create_form(request)


def _render_create_form(
    request: Request,
    *,
    error: str | None = None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    return render(
        request,
        "admin/template_studio/create.html",
        {
            "active_nav": "templates",
            "page_title": "New Template",
            "kinds": TEMPLATE_KINDS,
            "error": error,
            "form": form or {},
        },
        status_code=status_code,
    )


@router.post("/templates", response_model=None)
async def template_create_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    raw = {
        "kind": str(form_data.get("kind", "")).strip(),
        "slug": str(form_data.get("slug", "")).strip(),
        "name": str(form_data.get("name", "")).strip(),
        "channel": str(form_data.get("channel", "")).strip(),
    }
    try:
        template = service.create_template(
            db,
            tenant.id,
            kind=raw["kind"],
            slug=raw["slug"],
            name=raw["name"],
            channel=raw["channel"] or None,
        )
    except (BadRequestError, ConflictError) as exc:
        return _render_create_form(request, error=str(exc), form=raw)
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=auth["party"].id,
        action="template_studio.template.create",
        entity_type="template",
        entity_id=str(template.id),
        details={"kind": template.kind, "slug": template.slug},
    )
    response = HTMLResponse("")
    response.headers["HX-Redirect"] = f"/admin/templates/{template.id}"
    return response


@router.get("/templates/{template_id}")
def template_detail(
    request: Request,
    template_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    template = service.get_template(db, tenant.id, template_id)
    versions = service.list_versions(db, tenant.id, template_id)
    return render(
        request,
        "admin/template_studio/detail.html",
        {
            "active_nav": "templates",
            "page_title": template.name,
            "template": template,
            "versions": versions,
        },
    )


@router.post("/templates/{template_id}/versions", response_model=None)
async def version_create_submit(
    request: Request,
    template_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    form_data = await request.form()
    body = str(form_data.get("body", "")).strip()
    subject = str(form_data.get("subject", "")).strip() or None
    template = service.get_template(db, tenant.id, template_id)
    error = None
    if not body:
        error = "A version needs a body."
    else:
        version = service.create_version(
            db,
            tenant.id,
            template_id,
            body=body,
            subject=subject,
            author_party_id=auth["party"].id,
        )
        write_audit_event(
            db,
            tenant_id=tenant.id,
            actor_party_id=auth["party"].id,
            action="template_studio.version.create",
            entity_type="template_version",
            entity_id=str(version.id),
            details={"template_id": str(template_id), "version": version.version},
        )
    return render(
        request,
        "admin/template_studio/_versions_panel.html",
        {
            "template": template,
            "versions": service.list_versions(db, tenant.id, template_id),
            "error": error,
        },
        status_code=200,
    )


@router.post("/templates/{template_id}/versions/{version}/publish", response_model=None)
def version_publish(
    request: Request,
    template_id: UUID,
    version: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    error = None
    try:
        revision = service.publish_version(db, tenant.id, template_id, version)
    except NotFoundError as exc:
        error = str(exc)
    else:
        write_audit_event(
            db,
            tenant_id=tenant.id,
            actor_party_id=auth["party"].id,
            action="template_studio.version.publish",
            entity_type="template_version",
            entity_id=str(revision.id),
            details={"template_id": str(template_id), "version": version},
        )
    return render(
        request,
        "admin/template_studio/_versions_panel.html",
        {
            "template": service.get_template(db, tenant.id, template_id),
            "versions": service.list_versions(db, tenant.id, template_id),
            "error": error,
        },
        status_code=200,
    )
