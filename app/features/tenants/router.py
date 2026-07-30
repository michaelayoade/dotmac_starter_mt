"""Platform-admin endpoints — provision, suspend, delete tenants.

Reachable ONLY on the platform root domain (host-exact — see
`dotmac_kernel.middleware.tenant._is_platform_path`) and only by an
authenticated platform admin (`dotmac_kernel.platform_auth.require_platform_admin`,
the router-level guard: separate platform identity, `aud="platform"` bearer
token, live `platform_sessions` row). Uses `get_platform_db`, which connects
as `platform_api` — an online role with explicit grants and no RLS bypass.

Provisioning (POST) is atomic (control-plane security Task 2): tenant +
login-able owner + admin grant + two audit events in one transaction — see
`app.features.tenants.service.provision_tenant`.
"""

from __future__ import annotations

from uuid import UUID

from dotmac_kernel.deps import get_platform_db
from dotmac_kernel.models import Tenant
from dotmac_kernel.models_platform import PlatformAdmin
from dotmac_kernel.platform_auth import require_platform_admin
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.features.tenants import service as tenants_service
from app.features.tenants.schemas import TenantProvision, TenantRead

router = APIRouter(
    prefix="/platform/tenants",
    tags=["platform"],
    dependencies=[Depends(require_platform_admin)],
)


@router.post("", response_model=TenantRead, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantProvision,
    db: Session = Depends(get_platform_db),
    # Same dependency as the router-level guard — FastAPI caches it per
    # request, so this only names the already-authenticated actor for audit.
    admin: PlatformAdmin = Depends(require_platform_admin),
) -> Tenant:
    return tenants_service.provision_tenant(db, payload, actor_email=admin.email)


@router.get("", response_model=list[TenantRead])
def list_tenants(
    limit: int = tenants_service.DEFAULT_PAGE_SIZE,
    offset: int = 0,
    db: Session = Depends(get_platform_db),
) -> list[Tenant]:
    return tenants_service.list_tenants(db, limit=limit, offset=offset)


@router.get("/{tenant_id}", response_model=TenantRead)
def get_tenant(tenant_id: UUID, db: Session = Depends(get_platform_db)) -> Tenant:
    return tenants_service.get_tenant(db, tenant_id)
