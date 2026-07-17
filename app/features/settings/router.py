"""Tenant-scoped settings admin API.

Guard pattern matches `app.features.rbac.router` exactly: router-level
`Depends(require_tenant)` plus a per-route `Depends(require_role("admin"))` —
only a tenant admin may view or change settings.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_role, require_tenant
from app.core.models import Person, Tenant
from app.features.settings import service as settings_service
from app.features.settings.schemas import SettingOut, SettingUpdate

router = APIRouter(
    prefix="/settings", tags=["settings"], dependencies=[Depends(require_tenant)]
)


@router.get("/{domain}", response_model=list[SettingOut])
def list_settings(
    domain: str,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Person = Depends(require_role("admin")),
) -> list[SettingOut]:
    return settings_service.list_settings(db, tenant, domain)


@router.put("/{domain}/{key}", response_model=SettingOut)
def update_setting(
    domain: str,
    key: str,
    payload: SettingUpdate,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Person = Depends(require_role("admin")),
) -> SettingOut:
    return settings_service.update_setting(db, tenant, domain, key, payload.value)
