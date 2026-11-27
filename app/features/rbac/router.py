"""Tenant-scoped audit endpoint (the only RBAC route left in the reference app).

Role and grant administration moved to `dotmac-access-admin`; the writers are
gone from here so there is one owning writer per state. Guarded by
`require_permission("rbac.audit.read")`, a code this feature declares.
"""

from __future__ import annotations

from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.deps import get_db, require_permission, require_tenant
from dotmac_kernel.models import Party, Tenant
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.features.rbac import service as rbac_service
from app.features.rbac.schemas import AuditEventRead

DEFAULT_AUDIT_LIMIT = 50
MAX_AUDIT_LIMIT = 200

router = APIRouter(
    prefix="/rbac", tags=["rbac"], dependencies=[Depends(require_tenant)]
)


@router.get("/audit-events", response_model=list[AuditEventRead])
def list_audit_events(
    limit: int = Query(default=DEFAULT_AUDIT_LIMIT, ge=0, le=MAX_AUDIT_LIMIT),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    _: Party = Depends(require_permission("rbac.audit.read")),
) -> list[AuditEvent]:
    # Task 6: audit-events was the last unpaginated list in the app — this
    # route and `app.features.rbac.web`'s `/admin/audit` screen both call
    # the SAME `rbac_service.list_audit_events`, so there is one paginated
    # implementation, not two that could drift (see that function's
    # docstring).
    return rbac_service.list_audit_events(db, tenant, limit=limit, offset=offset)
