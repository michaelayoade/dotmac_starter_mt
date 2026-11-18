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

import threading
import time
from collections import deque
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

from dotmac_template_studio import delivery_seam, layout, service
from dotmac_template_studio.contexts import registered_contexts
from dotmac_template_studio.preview import IFRAME_SANDBOX, build_srcdoc
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
            # plus the reserved layout context (channel `email_layout`).
            "contexts": (*registered_contexts(), layout.layout_context()),
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


# ── Rendering helpers ───────────────────────────────────────────────────────

_PANEL = "template_studio/admin/template_studio/_versions_panel.html"
_DETAIL = "template_studio/admin/template_studio/detail.html"
_WORKSPACE = "template_studio/admin/template_studio/version.html"
_CONFIRM = "template_studio/admin/template_studio/confirm.html"


def _detail_context(
    db: Session,
    tenant: Tenant,
    template_id: UUID,
    *,
    error: str | None = None,
    notice: str | None = None,
    form: dict[str, str] | None = None,
) -> dict[str, object]:
    template_record = service.get_template(db, tenant.id, template_id)
    template = _template_view(template_record)
    return {
        "active_nav": "templates",
        "page_title": template.name,
        "template": template,
        "versions": _version_views(service.list_versions(db, tenant.id, template_id)),
        "variables": _context_variables(template.context),
        "layouts": _layout_options(db, tenant, template_record),
        "error": error,
        "notice": notice,
        "form": form or {},
    }


def _respond(
    request: Request,
    db: Session,
    tenant: Tenant,
    template_id: UUID,
    *,
    error: str | None = None,
    notice: str | None = None,
    form: dict[str, str] | None = None,
) -> HTMLResponse:
    """Re-render at 200 after a mutation: the panel for htmx, the page for a
    native form post."""
    context = _detail_context(
        db, tenant, template_id, error=error, notice=notice, form=form
    )
    name = _PANEL if request.headers.get("HX-Request") else _DETAIL
    return render(request, name, context, status_code=200)


@router.get("/{template_id}", name="detail")
def template_detail(
    request: Request,
    template_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.read")),
) -> HTMLResponse:
    return render(request, _DETAIL, _detail_context(db, tenant, template_id))


def _audit(
    db: Session,
    tenant: Tenant,
    party: Party,
    action: str,
    entity_type: str,
    entity_id: str,
    details: dict[str, object],
) -> None:
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=party.id,
        actor_type="user",
        actor_id=str(party.id),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
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
    html_body = str(form_data.get("html_body", "")).strip() or None
    layout_slug = str(form_data.get("layout_slug", "")).strip() or None
    error = None
    notice = None
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
                html_body=html_body,
                layout_slug=layout_slug,
            )
        except BadRequestError as exc:
            # Save-time validation. Re-render at 200 with the message rather
            # than redirecting or 500ing — an author needs to see WHICH problem.
            error = str(exc)
        else:
            _audit(
                db,
                tenant,
                party,
                "template_studio.version.create",
                "template_version",
                str(version.id),
                {"template_id": str(template_id), "version": version.version},
            )
            notice = f"Saved version {version.version} as a draft."
    return _respond(
        request,
        db,
        tenant,
        template_id,
        error=error,
        notice=notice,
        form=(
            {
                "subject": subject or "",
                "body": body,
                "html_body": html_body or "",
                "layout_slug": layout_slug or "",
            }
            if error
            else None
        ),
    )


# ── Version workspace: edit, preview, diff, test send ───────────────────────

# In-process limiter for "send test to me": at most N per window per actor. It is
# per worker process, so it bounds a runaway click, not a determined actor;
# every attempt is audited either way.
_TEST_SEND_LIMIT = 5
_TEST_SEND_WINDOW_SECONDS = 600.0
_test_send_log: dict[tuple[UUID, UUID], deque[float]] = {}
_test_send_lock = threading.Lock()


def _test_send_allowed(tenant_id: UUID, party_id: UUID) -> bool:
    now = time.monotonic()
    with _test_send_lock:
        recent = _test_send_log.setdefault((tenant_id, party_id), deque())
        while recent and now - recent[0] > _TEST_SEND_WINDOW_SECONDS:
            recent.popleft()
        if len(recent) >= _TEST_SEND_LIMIT:
            return False
        recent.append(now)
        return True


def _layout_options(db: Session, tenant: Tenant, template_record: object) -> tuple:
    """Layout slugs a draft of this template may opt in to (email only)."""
    if getattr(template_record, "channel", None) != layout.LAYOUT_TARGET_CHANNEL:
        return ()
    return tuple(
        (value.slug, value.name) for value in service.list_layouts(db, tenant.id)
    )


def _workspace_response(
    request: Request,
    db: Session,
    tenant: Tenant,
    template_id: UUID,
    version: int,
    *,
    error: str | None = None,
    notice: str | None = None,
    form: dict[str, str] | None = None,
) -> HTMLResponse:
    template_record = service.get_template(db, tenant.id, template_id)
    revision = VersionRead.model_validate(
        service.get_version(db, tenant.id, template_id, version)
    )
    preview_error: str | None = None
    preview_text: str | None = None
    preview_subject: str | None = None
    preview_srcdoc: str | None = None
    try:
        rendered = service.render_preview(db, tenant.id, template_id, version)
    except BadRequestError as exc:
        preview_error = str(exc)
    else:
        preview_subject = rendered.subject
        preview_text = rendered.text
        # Passed as an ordinary string: the template attribute-escapes it. The
        # rendered html is never emitted into the page itself.
        preview_srcdoc = build_srcdoc(rendered.html) if rendered.html else None
    published_version, diffs = service.diff_against_published(
        db, tenant.id, template_id, version
    )
    return render(
        request,
        _WORKSPACE,
        {
            "active_nav": "templates",
            "page_title": f"{template_record.name} v{version}",
            "template": _template_view(template_record),
            "version": revision,
            "is_draft": revision.published_at is None,
            "is_published": template_record.published_version == version,
            "variables": _context_variables(template_record.context),
            "error": error,
            "notice": notice,
            "form": form
            or {
                "subject": revision.subject or "",
                "body": revision.body,
                "html_body": revision.html_body or "",
                "layout_slug": revision.layout_slug or "",
            },
            "layouts": _layout_options(db, tenant, template_record),
            "preview_error": preview_error,
            "preview_subject": preview_subject,
            "preview_text": preview_text,
            "preview_srcdoc": preview_srcdoc,
            "iframe_sandbox": IFRAME_SANDBOX,
            "published_version": published_version,
            "diffs": diffs,
            "can_test_send": delivery_seam.can_test_send(template_record.channel),
        },
    )


@router.get("/{template_id}/versions/{version}", name="version_workspace")
def version_workspace(
    request: Request,
    template_id: UUID,
    version: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.manage")),
) -> HTMLResponse:
    return _workspace_response(request, db, tenant, template_id, version)


@router.post(
    "/{template_id}/versions/{version}/edit",
    response_model=None,
    name="update_version",
)
async def version_update_submit(
    request: Request,
    template_id: UUID,
    version: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.manage")),
) -> HTMLResponse:
    form_data = await request.form()
    body = str(form_data.get("body", "")).strip()
    subject = str(form_data.get("subject", "")).strip()
    html_body = str(form_data.get("html_body", "")).strip() or None
    layout_slug = str(form_data.get("layout_slug", "")).strip() or None
    form = {
        "subject": subject,
        "body": body,
        "html_body": html_body or "",
        "layout_slug": layout_slug or "",
    }
    error = None
    notice = None
    if not body:
        error = "A version needs a body."
    else:
        try:
            # Same validation + sanitizer path as the API PATCH. An empty html
            # field removes the html part; an empty subject clears the subject.
            revision = service.update_version(
                db,
                tenant.id,
                template_id,
                version,
                body=body,
                subject=subject,
                html_body=html_body,
                layout_slug=layout_slug,
            )
        except (BadRequestError, ConflictError) as exc:
            error = str(exc)
        else:
            _audit(
                db,
                tenant,
                party,
                "template_studio.version.update",
                "template_version",
                str(revision.id),
                {"template_id": str(template_id), "version": version},
            )
            notice = "Draft saved."
    return _workspace_response(
        request,
        db,
        tenant,
        template_id,
        version,
        error=error,
        notice=notice,
        form=form if error else None,
    )


@router.post(
    "/{template_id}/versions/{version}/test-send",
    response_model=None,
    name="test_send_version",
)
def version_test_send(
    request: Request,
    template_id: UUID,
    version: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.manage")),
) -> HTMLResponse:
    """Send the rendered sample to the ACTING USER's own address, nowhere else.

    There is no recipient field: the address is `party.email` from the
    authenticated party. No form input is read.
    """
    template_record = service.get_template(db, tenant.id, template_id)
    sender = delivery_seam.get_test_sender()
    details: dict[str, object] = {"template_id": str(template_id), "version": version}
    error: str | None = None
    notice: str | None = None
    outcome = "sent"
    if sender is None or not delivery_seam.can_test_send(template_record.channel):
        outcome, error = "unavailable", "Test send is not configured for this channel."
    elif not party.email:
        outcome, error = "no_address", "Your account has no email address."
    elif not _test_send_allowed(tenant.id, party.id):
        outcome, error = "rate_limited", "Too many test sends; try again later."
    else:
        # The product's sender runs inside a savepoint: if it fails at the
        # database level the savepoint is rolled back, the session stays usable
        # and the audit row below is still written.
        message = None
        savepoint = db.begin_nested()
        try:
            rendered = service.render_preview(db, tenant.id, template_id, version)
            message = delivery_seam.OwnAddressTestMessage(
                tenant_id=tenant.id,
                recipient_party_id=party.id,
                recipient_email=party.email,
                channel=template_record.channel,
                template_slug=template_record.slug,
                template_version=version,
                subject=rendered.subject,
                text=rendered.text,
                html=rendered.html,
            )
            sender(db, message)
            savepoint.commit()
        except BadRequestError as exc:
            savepoint.rollback()
            outcome, error = "refused", str(exc)
        except Exception:  # the product's delivery owner failed; no detail leaks
            savepoint.rollback()
            outcome, error = "failed", "The test message could not be sent."
        else:
            notice = "A test message was sent to your own address."
    details["outcome"] = outcome
    _audit(
        db,
        tenant,
        party,
        "template_studio.version.test_send",
        "template_version",
        f"{template_id}:{version}",
        details,
    )
    return _workspace_response(
        request, db, tenant, template_id, version, error=error, notice=notice
    )


# ── Publish / restore with an explicit confirmation step ────────────────────


def _confirm_response(
    request: Request,
    db: Session,
    tenant: Tenant,
    template_id: UUID,
    version: int,
    *,
    error: str | None = None,
) -> HTMLResponse:
    template_record = service.get_template(db, tenant.id, template_id)
    revision = service.get_version(db, tenant.id, template_id, version)
    published_version, diffs = service.diff_against_published(
        db, tenant.id, template_id, version
    )
    was_published = revision.published_at is not None
    return render(
        request,
        _CONFIRM,
        {
            "active_nav": "templates",
            "page_title": f"Confirm v{version}",
            "template": _template_view(template_record),
            "version": version,
            "action": "restore" if was_published else "publish",
            "is_current": template_record.published_version == version,
            "published_version": published_version,
            "diffs": diffs,
            "error": error,
        },
    )


@router.get("/{template_id}/versions/{version}/confirm", name="confirm_version")
def version_confirm(
    request: Request,
    template_id: UUID,
    version: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.publish")),
) -> HTMLResponse:
    return _confirm_response(request, db, tenant, template_id, version)


def _confirmed(form_data: object) -> bool:
    getter = getattr(form_data, "get", None)
    return getter is not None and str(getter("confirm", "")) == "yes"


@router.post(
    "/{template_id}/versions/{version}/publish",
    response_model=None,
    name="publish_version",
)
async def version_publish(
    request: Request,
    template_id: UUID,
    version: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.publish")),
) -> HTMLResponse:
    if not _confirmed(await request.form()):
        return _confirm_response(
            request, db, tenant, template_id, version, error="Confirm to continue."
        )
    error = None
    notice = None
    try:
        existing = service.get_version(db, tenant.id, template_id, version)
        if existing.published_at is not None:
            raise ConflictError(
                f"version {version} was published before — restore it instead"
            )
        revision = service.publish_version(
            db, tenant.id, template_id, version, actor_party_id=party.id
        )
    except service.PublishRefusedError as exc:
        error = str(exc)
        _audit(
            db,
            tenant,
            party,
            "template_studio.version.publish_refused",
            "template_version",
            f"{template_id}:{version}",
            {
                "template_id": str(template_id),
                "version": version,
            },
        )
    except (NotFoundError, ConflictError) as exc:
        error = str(exc)
    else:
        _audit(
            db,
            tenant,
            party,
            "template_studio.version.publish",
            "template_version",
            str(revision.id),
            {"template_id": str(template_id), "version": version},
        )
        notice = f"Version {version} is now published."
    return _respond(request, db, tenant, template_id, error=error, notice=notice)


@router.post(
    "/{template_id}/versions/{version}/restore",
    response_model=None,
    name="restore_version",
)
async def version_restore(
    request: Request,
    template_id: UUID,
    version: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.publish")),
) -> HTMLResponse:
    if not _confirmed(await request.form()):
        return _confirm_response(
            request, db, tenant, template_id, version, error="Confirm to continue."
        )
    error = None
    notice = None
    try:
        revision, previous = service.restore_version(
            db, tenant.id, template_id, version
        )
    except (NotFoundError, ConflictError) as exc:
        error = str(exc)
    else:
        _audit(
            db,
            tenant,
            party,
            "template_studio.version.restore",
            "template_version",
            str(revision.id),
            {
                "template_id": str(template_id),
                "version": version,
                "previous_version": previous,
            },
        )
        notice = f"Restored version {version} as the published version."
    return _respond(request, db, tenant, template_id, error=error, notice=notice)


# ── Archive / reactivate ────────────────────────────────────────────────────


@router.post("/{template_id}/status", response_model=None, name="set_status")
async def template_set_status(
    request: Request,
    template_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("template_studio.templates.manage")),
) -> HTMLResponse:
    """Deactivate (archive) or reactivate. Content and versions are kept; an
    inactive template simply stops rendering."""
    form_data = await request.form()
    active = str(form_data.get("active", "")) == "1"
    template = service.update_template(db, tenant.id, template_id, is_active=active)
    _audit(
        db,
        tenant,
        party,
        "template_studio.template.update",
        "template",
        str(template.id),
        {"slug": template.slug, "is_active": active},
    )
    return _respond(
        request,
        db,
        tenant,
        template_id,
        notice="Template reactivated." if active else "Template deactivated.",
    )
