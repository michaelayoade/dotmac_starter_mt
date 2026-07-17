"""Platform-admin endpoints — provision, suspend, delete tenants.

Reachable only on the platform root domain (no subdomain). Uses `get_platform_db` which
connects with `platform_api` — an online role with explicit grants and no RLS bypass.

This is a skeleton. Real impl needs:
- Platform admin authentication (separate auth from tenant users)
- Owner user provisioning in the same transaction as tenant create
- Audit log entry on every state change
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.deps import get_platform_db, require_platform
from app.core.models import Tenant
from app.features.tenants import service as tenants_service
from app.features.tenants.schemas import TenantCreate, TenantRead

router = APIRouter(
    prefix="/platform/tenants",
    tags=["platform"],
    dependencies=[Depends(require_platform)],
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
