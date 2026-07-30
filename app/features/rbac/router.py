"""Tenant-scoped RBAC and audit endpoints."""

from __future__ import annotations

from dotmac_kernel.audit import AuditEvent, write_audit_event
from dotmac_kernel.deps import get_db, require_role, require_tenant
from dotmac_kernel.models import Party, Role, Tenant
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.features.rbac import service as rbac_service
from app.features.rbac.schemas import (
    AuditEventRead,
    RoleCreate,
    RoleGrantRequest,
    RoleRead,
)

DEFAULT_ROLES_LIMIT = 50
MAX_ROLES_LIMIT = 200
DEFAULT_AUDIT_LIMIT = 50
MAX_AUDIT_LIMIT = 200

router = APIRouter(
    prefix="/rbac", tags=["rbac"], dependencies=[Depends(require_tenant)]
)


@router.post(
    "/roles",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_role("admin")),
) -> Role:
    role = rbac_service.create_role(db, tenant, payload)
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        action="role.create",
        entity_type="role",
        entity_id=str(role.id),
        details={"slug": role.slug},
    )
    return role


@router.get("/roles", response_model=list[RoleRead])
def list_roles(
    limit: int = Query(default=DEFAULT_ROLES_LIMIT, ge=0, le=MAX_ROLES_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> list[Role]:
    return rbac_service.list_roles(db, tenant, limit=limit, offset=offset)


@router.post(
    "/role-grants", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
def grant_role(
    payload: RoleGrantRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_role("admin")),
) -> None:
    party_role = rbac_service.assign_role(db, tenant, payload)
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_party_id=actor.id,
        action="role.grant",
        entity_type="party_role",
        entity_id=str(party_role.party_id),
        details={"role_id": str(party_role.role_id)},
    )


@router.get("/audit-events", response_model=list[AuditEventRead])
def list_audit_events(
    limit: int = Query(default=DEFAULT_AUDIT_LIMIT, ge=0, le=MAX_AUDIT_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_role("admin")),
) -> list[AuditEvent]:
    # Task 6: audit-events was the last unpaginated list in the app — this
    # route and `app.features.rbac.web`'s `/admin/audit` screen both call
    # the SAME `rbac_service.list_audit_events`, so there is one paginated
    # implementation, not two that could drift (see that function's
    # docstring).
    return rbac_service.list_audit_events(db, tenant, limit=limit, offset=offset)
