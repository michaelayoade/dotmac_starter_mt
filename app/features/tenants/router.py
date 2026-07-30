"""Platform-admin endpoints — provision, suspend, delete tenants.

Reachable ONLY on the platform root domain (host-exact — see
`app.core.middleware.tenant._is_platform_path`) and only by an
authenticated platform admin (`app.core.platform_auth.require_platform_admin`,
the router-level guard: separate platform identity, `aud="platform"` bearer
token, live `platform_sessions` row). Uses `get_platform_db`, which connects
as `platform_api` — an online role with explicit grants and no RLS bypass.

Still pending (control-plane security Task 2): owner-user provisioning in
the same transaction as tenant create + audit events on every state change.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_platform_db
from app.core.models import Tenant
from app.core.platform_auth import require_platform_admin
from app.features.tenants import service as tenants_service
from app.features.tenants.schemas import TenantCreate, TenantRead

router = APIRouter(
    prefix="/platform/tenants",
    tags=["platform"],
    dependencies=[Depends(require_platform_admin)],
)


@router.post("", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate, db: Session = Depends(get_platform_db)
) -> Tenant:
    return tenants_service.create_tenant(db, payload)


@router.get("", response_model=list[TenantRead])
def list_tenants(db: Session = Depends(get_platform_db)) -> list[Tenant]:
    return tenants_service.list_tenants(db)


@router.get("/{tenant_id}", response_model=TenantRead)
def get_tenant(tenant_id: UUID, db: Session = Depends(get_platform_db)) -> Tenant:
    return tenants_service.get_tenant(db, tenant_id)
