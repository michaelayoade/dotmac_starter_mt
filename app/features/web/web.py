"""Web-portal route: the admin dashboard shell.

`GET/POST /admin/login` and `POST /admin/logout` are owned by the `auth`
feature (`app.features.auth.web`) — moved there per Task 3 review's required
fix (see `.superpowers/sdd/task-3-report.md`'s fix note) so login/logout's
call into `auth`'s own `login()` flow is a same-module call, not a
cross-feature import. Those two routes stay mounted even when
`DISABLED_FEATURES=web` disables this package: `auth` is a core feature and
mounts independently.

`GET /admin` (the dashboard) is guarded by `require_web_auth`. It's the only
route this package owns, and it's the one that disappears when `web` is
disabled.

No direct database-query calls in this file (see
`tests/architecture/test_thin_wrappers.py`) — thin-wrapper rule; all of
that lives in `app.features.web.service`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.deps import get_db, require_tenant
from app.core.models import Tenant
from app.core.templating import render
from app.core.web_deps import require_web_auth
from app.features.web import service as web_service

router = APIRouter(prefix="/admin", tags=["web"])


@router.get("")
def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    auth: dict = Depends(require_web_auth),
) -> HTMLResponse:
    counts = web_service.get_dashboard_counts(db, tenant)
    current_user = web_service.get_current_user_view(db, auth["party"])
    return render(
        request,
        "admin/dashboard.html",
        {
            "active_nav": "dashboard",
            "page_title": "Dashboard",
            "current_user": current_user,
            "counts": counts,
        },
    )


__all__ = ["router"]
