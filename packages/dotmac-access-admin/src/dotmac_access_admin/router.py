"""JSON API. Every route names a declared `access_admin.*` permission."""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.deps import get_db, require_permission, require_tenant
from dotmac_kernel.models import Party, Tenant
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from dotmac_access_admin import service
from dotmac_access_admin.schemas import (
    CompositionRead,
    CompositionRequest,
    GrantRequest,
    GrantResult,
    RevokeResult,
    RoleCreate,
    RoleRead,
    RoleRename,
    SodWarningRead,
    UserCreate,
    UserCreated,
    UserRead,
)

router = APIRouter(
    prefix="/access-admin",
    tags=["access-admin"],
    dependencies=[Depends(require_tenant)],
)


def _user(row: service.UserRow) -> UserRead:
    return UserRead(
        id=row.id,
        email=row.email,
        display_name=row.display_name,
        first_name=row.first_name,
        last_name=row.last_name,
        is_active=row.is_active,
        roles=list(row.roles),
    )


def _role(row: service.RoleRow) -> RoleRead:
    return RoleRead(
        slug=row.slug,
        name=row.name,
        id=row.id,
        kind=row.kind,
        read_only=row.read_only,
        permission_codes=list(row.permission_codes),
        holders=row.holders,
    )


@router.get("/users", response_model=list[UserRead])
def list_users(
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=0, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission("access_admin.users.read")),
) -> list[UserRead]:
    return [
        _user(r)
        for r in service.list_users(db, tenant, q=q, limit=limit, offset=offset)
    ]


@router.post("/users", response_model=UserCreated, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("access_admin.users.manage")),
) -> UserCreated:
    created = service.create_user(
        db,
        tenant,
        actor,
        email=payload.email,
        first_name=payload.first_name,
        last_name=payload.last_name,
    )
    return UserCreated(
        user=_user(created.user), recovery_intent_ref=created.recovery_intent_ref
    )


@router.post("/users/{party_id}/deactivate", response_model=UserRead)
def deactivate_user(
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("access_admin.users.manage")),
) -> UserRead:
    return _user(service.deactivate_user(db, tenant, actor, party_id=party_id))


@router.post("/users/{party_id}/reactivate", response_model=UserRead)
def reactivate_user(
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("access_admin.users.manage")),
) -> UserRead:
    return _user(service.reactivate_user(db, tenant, actor, party_id=party_id))


@router.post("/users/{party_id}/leaver-revoke", response_model=RevokeResult)
def leaver_revoke(
    party_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("access_admin.users.revoke")),
) -> RevokeResult:
    outcome = service.leaver_revoke(db, tenant, actor, party_id=party_id)
    return RevokeResult(
        party_id=outcome.party_id,
        grants_revoked=outcome.grants_revoked,
        sessions_revoked=outcome.sessions_revoked,
        deactivated=outcome.deactivated,
    )


@router.get("/roles", response_model=list[RoleRead])
def list_roles(
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission("access_admin.roles.read")),
) -> list[RoleRead]:
    return [_role(r) for r in service.list_roles(db, tenant)]


@router.post("/roles", response_model=RoleRead, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("access_admin.roles.manage")),
) -> RoleRead:
    role = service.create_role(db, tenant, actor, slug=payload.slug, name=payload.name)
    return RoleRead(
        slug=role.slug,
        name=role.name,
        id=role.id,
        kind="tenant",
        read_only=False,
        permission_codes=[],
        holders=0,
    )


@router.patch("/roles/{role_id}", response_model=RoleRead)
def rename_role(
    role_id: UUID,
    payload: RoleRename,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("access_admin.roles.manage")),
) -> RoleRead:
    role = service.rename_role(db, tenant, actor, role_id=role_id, name=payload.name)
    return RoleRead(
        slug=role.slug,
        name=role.name,
        id=role.id,
        kind="tenant",
        read_only=False,
        permission_codes=[],
        holders=0,
    )


@router.post("/roles/composition-check", response_model=CompositionRead)
def composition_check(
    payload: CompositionRequest,
    _: Party = Depends(require_permission("access_admin.roles.read")),
) -> CompositionRead:
    """Validate a permission set against the declared catalogue (422/400 on any
    undeclared code). Nothing is persisted; see `roles.py`."""
    return CompositionRead(
        permission_codes=list(service.composition_check(payload.permission_codes))
    )


@router.post("/grants", response_model=GrantResult, status_code=status.HTTP_201_CREATED)
def grant_role(
    payload: GrantRequest,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("access_admin.grants.manage")),
) -> GrantResult:
    outcome = service.grant_role(
        db, tenant, actor, party_id=payload.party_id, role_id=payload.role_id
    )
    return GrantResult(
        party_id=outcome.party_id,
        role_id=outcome.role_id,
        role_slug=outcome.role_slug,
        warnings=[
            SodWarningRead(roles=list(w.roles), message=w.message)
            for w in outcome.warnings
        ],
    )


@router.delete(
    "/grants/{party_id}/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def revoke_grant(
    party_id: UUID,
    role_id: UUID,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    actor: Party = Depends(require_permission("access_admin.grants.manage")),
) -> None:
    service.revoke_grant(db, tenant, actor, party_id=party_id, role_id=role_id)
