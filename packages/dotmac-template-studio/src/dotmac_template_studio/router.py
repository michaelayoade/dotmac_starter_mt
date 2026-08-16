"""Template Studio's JSON API — a thin adapter over `service.py`.

Every route validates, authorizes, delegates, and shapes a response; none issues
a query of its own (hard rule 1). Authorization names the DECISION as a
permission code this module's own manifest declares and owns — `require_role`
would hardcode who satisfies it, which is the declaration's business.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.audit import write_audit_event
from dotmac_kernel.deps import (
    get_db,
    require_capability,
    require_permission,
    require_tenant,
)
from dotmac_kernel.models import Party, Tenant
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from dotmac_template_studio import service
from dotmac_template_studio.models import Template, TemplateVersion
from dotmac_template_studio.schemas import (
    RenderRequest,
    RenderResult,
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
    VersionCreate,
    VersionRead,
    VersionUpdate,
)

router = APIRouter(
    prefix="/template-studio",
    tags=["template-studio"],
    # Two independent decisions, both required. The capability gates the
    # FEATURE for the tenant (router level, once); the per-route
    # `require_permission` gates the ACTION for the actor. An entitled tenant's
    # viewer still cannot publish, and a permitted admin in an un-entitled
    # tenant still gets nothing.
    dependencies=[
        Depends(require_tenant),
        Depends(require_capability("template_studio.use")),
    ],
)


@router.get("/templates", response_model=list[TemplateRead])
def list_templates(
    channel: str | None = Query(default=None),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission("template_studio.templates.read")),
) -> list[Template]:
    return service.list_templates(db, tenant.id, channel=channel)


@router.post(
    "/templates", response_model=TemplateRead, status_code=status.HTTP_201_CREATED
)
def create_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("template_studio.templates.manage")),
) -> Template:
    template = service.create_template(
        db,
        tenant.id,
        slug=payload.slug,
        channel=payload.channel,
        context=payload.context,
        name=payload.name,
        description=payload.description,
    )
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        actor_type="user",
        actor_id=str(actor.id),
        action="template_studio.template.create",
        entity_type="template",
        entity_id=str(template.id),
        details={
            "slug": template.slug,
            "channel": template.channel,
            "context": template.context,
        },
    )
    return template


@router.get("/templates/{template_id}", response_model=TemplateRead)
def get_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission("template_studio.templates.read")),
) -> Template:
    return service.get_template(db, tenant.id, template_id)


@router.patch("/templates/{template_id}", response_model=TemplateRead)
def update_template(
    template_id: UUID,
    payload: TemplateUpdate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("template_studio.templates.manage")),
) -> Template:
    template = service.update_template(
        db,
        tenant.id,
        template_id,
        name=payload.name,
        description=payload.description,
        is_active=payload.is_active,
    )
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        actor_type="user",
        actor_id=str(actor.id),
        action="template_studio.template.update",
        entity_type="template",
        entity_id=str(template.id),
        details={"slug": template.slug},
    )
    return template


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def delete_template(
    template_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("template_studio.templates.manage")),
) -> None:
    template = service.get_template(db, tenant.id, template_id)
    slug, channel = template.slug, template.channel
    service.delete_template(db, tenant.id, template_id)
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        actor_type="user",
        actor_id=str(actor.id),
        action="template_studio.template.delete",
        entity_type="template",
        entity_id=str(template_id),
        details={"slug": slug, "channel": channel},
    )


# ── Versions ────────────────────────────────────────────────────────────────


@router.get("/templates/{template_id}/versions", response_model=list[VersionRead])
def list_versions(
    template_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission("template_studio.templates.read")),
) -> list[TemplateVersion]:
    return service.list_versions(db, tenant.id, template_id)


@router.post(
    "/templates/{template_id}/versions",
    response_model=VersionRead,
    status_code=status.HTTP_201_CREATED,
)
def create_version(
    template_id: UUID,
    payload: VersionCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("template_studio.templates.manage")),
) -> TemplateVersion:
    version = service.create_version(
        db,
        tenant.id,
        template_id,
        body=payload.body,
        subject=payload.subject,
        author_party_id=actor.id,
    )
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        actor_type="user",
        actor_id=str(actor.id),
        action="template_studio.version.create",
        entity_type="template_version",
        entity_id=str(version.id),
        details={"template_id": str(template_id), "version": version.version},
    )
    return version


@router.patch("/templates/{template_id}/versions/{version}", response_model=VersionRead)
def update_version(
    template_id: UUID,
    version: int,
    payload: VersionUpdate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("template_studio.templates.manage")),
) -> TemplateVersion:
    revision = service.update_version(
        db,
        tenant.id,
        template_id,
        version,
        body=payload.body,
        subject=payload.subject,
    )
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        actor_type="user",
        actor_id=str(actor.id),
        action="template_studio.version.update",
        entity_type="template_version",
        entity_id=str(revision.id),
        details={"template_id": str(template_id), "version": version},
    )
    return revision


@router.post(
    "/templates/{template_id}/versions/{version}/publish", response_model=VersionRead
)
def publish_version(
    template_id: UUID,
    version: int,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("template_studio.templates.publish")),
) -> TemplateVersion:
    revision = service.publish_version(db, tenant.id, template_id, version)
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        actor_type="user",
        actor_id=str(actor.id),
        action="template_studio.version.publish",
        entity_type="template_version",
        entity_id=str(revision.id),
        details={"template_id": str(template_id), "version": version},
    )
    return revision


# ── Rendering ───────────────────────────────────────────────────────────────


@router.post("/render/{slug}/{channel}", response_model=RenderResult)
def render_template(
    slug: str,
    channel: str,
    payload: RenderRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission("template_studio.templates.render")),
) -> RenderResult:
    """Render the PUBLISHED revision of `(slug, channel)`.

    A caller addresses a template by its stable identity and never by version —
    which revision is live is the module's decision. No audit event: rendering
    reads state, and a per-render trail entry would flood the tenant's audit log
    with one row per outbound message.
    """
    subject, body = service.render_published(
        db, tenant.id, slug, channel, payload.values
    )
    return RenderResult(subject=subject, body=body)
