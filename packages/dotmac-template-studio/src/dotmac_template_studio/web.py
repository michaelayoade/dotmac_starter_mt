"""Template Studio's facet-relative staff surface (ADR-0006 M1).

Held to the same rules as an assembly feature's `web.py` — thin wrapper (no
query in this file), `render()` for every HTML response, and both declared CSRF
transports for native/htmx mutations, with "re-render at 200, don't redirect"
on a validation or conflict failure. The governance tests that enforce those rules
walk this package too; extending their globs was part of shipping it, because a
module that escaped them would be held to a weaker standard than the assembly it
installs into.

**Templates are PACKAGE DATA.** They live in this package's own `templates/`
directory, not in the assembly's, and reach the shared Jinja environment through
the manifest's `TemplatePackage(namespace="template_studio", ...)`. Render names
therefore begin `template_studio/`; another module cannot shadow them by choosing
the same relative file name.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.deps import get_db, require_capability, require_tenant
from dotmac_kernel.exceptions import BadRequestError, ConflictError, NotFoundError
from dotmac_kernel.models import Party, Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_permission
from dotmac_kernel.web_surfaces import surface_path
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from dotmac_template_studio import service
from dotmac_template_studio.contexts import registered_contexts
from dotmac_template_studio.schemas import TemplateRead, VersionRead

router = APIRouter(
    prefix="/templates",
    tags=["web"],
    # Same capability as the JSON API — one feature, one entitlement
    # decision, both surfaces.
    dependencies=[Depends(require_capability("template_studio.use"))],
)

_PKG_DIR = Path(__file__).resolve().parent


def template_dir() -> Path:
    """This package's namespaced Jinja root, resolved by package path."""
    return _PKG_DIR / "templates"


def _template_view(value: object) -> TemplateRead:
    """Detach a template render model from the request's ORM session."""

    return TemplateRead.model_validate(value)


def _version_views(values: Sequence[object]) -> tuple[VersionRead, ...]:
    """Templates consume typed values, never live ORM instances."""

    return tuple(VersionRead.model_validate(value) for value in values)


@router.get("", name="list")
def templates_index(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.read")),
) -> HTMLResponse:
    template_views = tuple(
        _template_view(value) for value in service.list_templates(db, tenant.id)
    )
    context: dict[str, object] = {"templates": template_views}
    if request.headers.get("HX-Request"):
        return render(
            request,
            "template_studio/admin/template_studio/_templates_table.html",
            context,
        )
    context.update({"active_nav": "templates", "page_title": "Templates"})
    return render(request, "template_studio/admin/template_studio/index.html", context)


@router.get("/create", name="create_form")
def template_create_form(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.manage")),
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
        "template_studio/admin/template_studio/create.html",
        {
            "active_nav": "templates",
            "page_title": "New Template",
            # The product's registered vocabulary, not a constant of this
            # module — the author picks a real send path and sees the exact
            # variables it can supply.
            "contexts": registered_contexts(),
            "error": error,
            "form": form or {},
        },
        status_code=status_code,
    )


@router.post("", response_model=None, name="create")
async def template_create_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.manage")),
) -> HTMLResponse | RedirectResponse:
    form_data = await request.form()
    raw = {
        "slug": str(form_data.get("slug", "")).strip(),
        "channel": str(form_data.get("channel", "")).strip(),
        "context": str(form_data.get("context", "")).strip(),
        "name": str(form_data.get("name", "")).strip(),
    }
    try:
        template = service.create_template(
            db,
            tenant.id,
            slug=raw["slug"],
            channel=raw["channel"],
            context=raw["context"],
            name=raw["name"],
        )
    except (BadRequestError, ConflictError) as exc:
        return _render_create_form(request, error=str(exc), form=raw)
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=party.id,
        actor_type="user",
        actor_id=str(party.id),
        action="template_studio.template.create",
        entity_type="template",
        entity_id=str(template.id),
        details={
            "slug": template.slug,
            "channel": template.channel,
            "context": template.context,
        },
    )
    response = HTMLResponse("")
    response.headers["HX-Redirect"] = surface_path(
        request, "detail", template_id=str(template.id)
    )
    return response


def _context_variables(name: str) -> list[str]:
    """The placeholders an author may use, for the editor's hint list.

    Degrades to an empty list rather than raising: a template whose context was
    un-registered by a later deployment must still be VIEWABLE, so an operator
    can see what is there and delete it. Authoring a new version against it
    still fails loudly in the service, which is where that decision belongs.
    """
    from dotmac_template_studio.contexts import UnknownRenderContextError, get_context

    try:
        return get_context(name).sorted_variables()
    except UnknownRenderContextError:
        return []


@router.get("/{template_id}", name="detail")
def template_detail(
    request: Request,
    template_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.read")),
) -> HTMLResponse:
    template = _template_view(service.get_template(db, tenant.id, template_id))
    versions = _version_views(service.list_versions(db, tenant.id, template_id))
    return render(
        request,
        "template_studio/admin/template_studio/detail.html",
        {
            "active_nav": "templates",
            "page_title": template.name,
            "template": template,
            "versions": versions,
            "variables": _context_variables(template.context),
        },
    )


@router.post("/{template_id}/versions", response_model=None, name="create_version")
async def version_create_submit(
    request: Request,
    template_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.manage")),
) -> HTMLResponse:
    form_data = await request.form()
    body = str(form_data.get("body", "")).strip()
    subject = str(form_data.get("subject", "")).strip() or None
    template_record = service.get_template(db, tenant.id, template_id)
    error = None
    if not body:
        error = "A version needs a body."
    else:
        try:
            version = service.create_version(
                db,
                tenant.id,
                template_id,
                body=body,
                subject=subject,
                author_party_id=party.id,
            )
        except BadRequestError as exc:
            # Save-time placeholder validation. Re-render at 200 with the
            # message rather than redirecting or 500ing — an author needs to see
            # WHICH variable is wrong and what the context does supply.
            error = str(exc)
        else:
            write_audit_event(
                db,
                tenant_id=tenant.id,
                actor_party_id=party.id,
                actor_type="user",
                actor_id=str(party.id),
                action="template_studio.version.create",
                entity_type="template_version",
                entity_id=str(version.id),
                details={"template_id": str(template_id), "version": version.version},
            )
    return render(
        request,
        "template_studio/admin/template_studio/_versions_panel.html",
        {
            "template": _template_view(template_record),
            "versions": _version_views(
                service.list_versions(db, tenant.id, template_id)
            ),
            "variables": _context_variables(template_record.context),
            "error": error,
        },
        status_code=200,
    )


@router.post(
    "/{template_id}/versions/{version}/publish",
    response_model=None,
    name="publish_version",
)
def version_publish(
    request: Request,
    template_id: UUID,
    version: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.publish")),
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
            actor_party_id=party.id,
            actor_type="user",
            actor_id=str(party.id),
            action="template_studio.version.publish",
            entity_type="template_version",
            entity_id=str(revision.id),
            details={"template_id": str(template_id), "version": version},
        )
    template_record = service.get_template(db, tenant.id, template_id)
    return render(
        request,
        "template_studio/admin/template_studio/_versions_panel.html",
        {
            "template": _template_view(template_record),
            "versions": _version_views(
                service.list_versions(db, tenant.id, template_id)
            ),
            "variables": _context_variables(template_record.context),
            "error": error,
        },
        status_code=200,
    )
