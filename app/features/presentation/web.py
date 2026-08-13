"""Public, tenant-scoped runtime brand stylesheet.

The route is intentionally pre-auth: the tenant login page loads the same
brand as the authenticated portal. ``require_brand_scope`` accepts either a
resolved tenant or the EXACT platform root host; the latter gets an empty
stylesheet so the shared platform layout does not advertise a guaranteed 404.
Unknown hosts fail closed. It is mounted only with the assembly's HTML surface
through ``FeatureManifest.web_routers``.

This adapter performs no branding decision and no database query itself. The
kernel resolver owns the data, ``service.project_brand_stylesheet`` composes it
with the UI generator, and this route applies the HTTP response policy.
"""

from __future__ import annotations

from dotmac_kernel.config import settings
from dotmac_kernel.deps import get_db
from dotmac_kernel.models import Tenant
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.features.presentation import service
from app.features.presentation.contract import BRAND_STYLESHEET_URL

router = APIRouter(tags=["branding"])


def require_brand_scope(request: Request) -> Tenant | None:
    """A tenant brand, or the exact platform host's package-default scope."""
    tenant = getattr(request.state, "tenant", None)
    if isinstance(tenant, Tenant):
        return tenant
    host = (request.headers.get("host") or "").split(":", 1)[0].lower()
    root = settings.platform_root_domain.lower().lstrip(".")
    if host == root:
        return None
    raise HTTPException(status_code=404, detail="Brand scope not found")


@router.get(BRAND_STYLESHEET_URL)
def brand_stylesheet(
    db: Session = Depends(get_db),
    tenant: Tenant | None = Depends(require_brand_scope),
) -> Response:
    projection = service.project_brand_stylesheet(db, tenant)
    return Response(
        content=projection.css,
        media_type="text/css",
        headers={
            "Cache-Control": "private, no-store",
            "Vary": "Host",
            "X-Dotmac-Brand-Projection": projection.source,
        },
    )


__all__ = ["require_brand_scope", "router"]
