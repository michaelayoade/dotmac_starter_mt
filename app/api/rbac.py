"""Tenant-scoped RBAC and audit endpoints."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role, require_tenant
from app.models.person import Person
from app.models.rbac import AuditEvent, Role
from app.models.tenant import Tenant
from app.services import rbac as rbac_service
from app.services.audit import write_audit_event

router = APIRouter(
    prefix="/rbac", tags=["rbac"], dependencies=[Depends(require_tenant)]
)


class RoleCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=120)


class RoleRead(BaseModel):
    id: UUID
    slug: str
    name: str
    model_config = {"from_attributes": True}


class RoleGrantRequest(BaseModel):
    person_id: UUID
    role_id: UUID


class AuditEventRead(BaseModel):
    id: UUID
    actor_person_id: UUID | None
    action: str
    entity_type: str
    entity_id: str | None
    details: dict[str, object]
    created_at: datetime
    model_config = {"from_attributes": True}


@router.post(
    "/roles",
    response_model=RoleRead,
    status_code=status.HTTP_201_CREATED,
)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Person = Depends(require_role("admin")),
) -> Role:
    role = rbac_service.create_role(db, tenant, payload)
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_person_id=actor.id,
        action="role.create",
        entity_type="role",
        entity_id=str(role.id),
        details={"slug": role.slug},
    )
    return role


@router.post(
    "/role-grants", status_code=status.HTTP_204_NO_CONTENT, response_model=None
)
def grant_role(
    payload: RoleGrantRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Person = Depends(require_role("admin")),
) -> None:
    person_role = rbac_service.assign_role(db, tenant, payload)
    write_audit_event(
        db,
        tenant_id=tenant.id,
        actor_person_id=actor.id,
        action="role.grant",
        entity_type="person_role",
        entity_id=str(person_role.person_id),
        details={"role_id": str(person_role.role_id)},
    )


@router.get("/audit-events", response_model=list[AuditEventRead])
def list_audit_events(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Person = Depends(require_role("admin")),
) -> list[AuditEvent]:
    return rbac_service.list_audit_events(db, tenant)
