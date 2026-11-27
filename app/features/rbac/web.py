"""RBAC's remaining web surface: the audit log at `/admin/audit`.

The role and grant screens moved to `dotmac-access-admin` (one owning writer
per state). What stays here is only what the audit page needs, until the audit
viewer lane takes it: `require_web_auth` (+`require_tenant`), a thin wrapper
over `app.features.rbac.service`, `render()` for HTML, and the `HX-Request`
fragment-vs-full-page convention.
"""

from __future__ import annotations

import json
from uuid import UUID

from dotmac_kernel.audit import AuditEvent
from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.models import Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_auth
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.features.rbac import service as rbac_service

router = APIRouter(prefix="/admin", tags=["web"])

AUDIT_PAGE_SIZE = 20


# ---------------------------------------------------------------------------
# GET /admin/audit
# ---------------------------------------------------------------------------


def _audit_rows(
    events: list[AuditEvent], actor_names: dict[UUID, str]
) -> list[dict[str, object]]:
    """Build the per-row view model `_audit_table.html` renders: `details`
    (a `dict`) is turned into pretty-printed JSON text HERE (in Python) —
    the template renders it inside a `<pre>` as a plain string, which
    Jinja's default autoescaping escapes for us, so no `| safe` is ever
    needed for untrusted event details.
    """
    rows: list[dict[str, object]] = []
    for event in events:
        if event.actor_party_id is None:
            actor_display_name = "System"
        else:
            actor_display_name = actor_names.get(event.actor_party_id, "Unknown")
        rows.append(
            {
                "id": event.id,
                "action": event.action,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "actor_display_name": actor_display_name,
                "created_at": event.created_at,
                "details_json": json.dumps(event.details, indent=2, default=str),
            }
        )
    return rows


@router.get("/audit")
def audit_index(
    request: Request,
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    offset = (page - 1) * AUDIT_PAGE_SIZE
    events = rbac_service.list_audit_events(
        db, tenant, limit=AUDIT_PAGE_SIZE, offset=offset
    )
    total = rbac_service.count_audit_events(db, tenant)
    total_pages = max(1, -(-total // AUDIT_PAGE_SIZE))
    actor_names = rbac_service.resolve_actor_display_names(
        db, tenant, (event.actor_party_id for event in events)
    )
    context = {
        "events": _audit_rows(events, actor_names),
        "page": page,
        "total": total,
        "total_pages": total_pages,
    }
    if request.headers.get("HX-Request"):
        return render(request, "admin/rbac/_audit_table.html", context)
    context.update({"active_nav": "audit", "page_title": "Audit Log"})
    return render(request, "admin/rbac/audit.html", context)


__all__ = ["router"]
