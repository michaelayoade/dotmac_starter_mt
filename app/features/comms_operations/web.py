"""Operator surface for the consent ledger and delivery receipts (K-7).

`/admin/comms/suppressions` lists the tenant's suppressions (filter by channel,
scope, address); `/admin/comms/receipts` shows what providers said about one
address; `POST /admin/comms/suppressions/clear` clears one suppression with a
recorded reason. Same shape as the other admin screens: thin wrappers over
`service.py`, `render()` for HTML, the hidden-field/htmx-header CSRF contract
(htmx `hx-post` here), "re-render at 200, don't redirect" on a refused input.

Permissions are declared by this feature (`feature.py`) and checked per route:
read for the lists, `comms.suppressions.manage` for the one mutation.
"""

from __future__ import annotations

from urllib.parse import urlencode

from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.exceptions import BadRequestError
from dotmac_kernel.models import Party, Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import require_web_permission
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.features.comms_operations import service

router = APIRouter(prefix="/admin/comms", tags=["web"])

PAGE_SIZE = 20
LIST_PATH = "/admin/comms/suppressions"
RECEIPTS_PATH = "/admin/comms/receipts"
CLEAR_PATH = "/admin/comms/suppressions/clear"
#: Paths handed to the templates, so no template authors a facet prefix.
_URLS = {"suppressions": LIST_PATH, "receipts": RECEIPTS_PATH, "clear": CLEAR_PATH}
_CLEARED_NOTICES = {
    "1": "Suppression cleared.",
    "0": "No such suppression; nothing was cleared.",
}


def _render_suppressions(
    request: Request,
    db: Session,
    tenant: Tenant,
    *,
    channel: str,
    scope: str,
    address: str,
    page: int,
    error: str | None = None,
    notice: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    filters = {
        "channel": channel or None,
        "scope": scope or None,
        "address": address or None,
    }
    rows = service.list_suppressions(
        db, tenant.id, limit=PAGE_SIZE, offset=(page - 1) * PAGE_SIZE, **filters
    )
    total = service.count_suppressions(db, tenant.id, **filters)
    return render(
        request,
        "admin/comms/suppressions.html",
        {
            "active_nav": "comms",
            "page_title": "Suppressions",
            "rows": rows,
            "page": page,
            "total": total,
            "total_pages": max(1, -(-total // PAGE_SIZE)),
            "channel": channel,
            "scope": scope,
            "address": address,
            "error": error,
            "notice": notice,
            "urls": _URLS,
        },
        status_code=status_code,
    )


@router.get("/suppressions", name="suppressions")
def suppressions_index(
    request: Request,
    channel: str = "",
    scope: str = "",
    address: str = "",
    cleared: str = "",
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("comms.suppressions.read")),
) -> HTMLResponse:
    try:
        return _render_suppressions(
            request,
            db,
            tenant,
            channel=channel.strip(),
            scope=scope.strip(),
            address=address.strip(),
            page=page,
            notice=_CLEARED_NOTICES.get(cleared),
        )
    except BadRequestError as exc:
        return _render_suppressions(
            request,
            db,
            tenant,
            channel="",
            scope="",
            address="",
            page=1,
            error=str(exc),
            status_code=200,
        )


def _render_receipts(
    request: Request,
    db: Session,
    tenant: Tenant,
    *,
    channel: str,
    address: str,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    receipts: tuple[service.ReceiptRow, ...] = ()
    suppression = None
    if address:
        receipts = tuple(
            service.list_receipts(db, tenant.id, channel=channel, address=address)
        )
        suppression = service.get_suppression(
            db, tenant.id, channel=channel, address=address
        )
    return render(
        request,
        "admin/comms/receipts.html",
        {
            "active_nav": "comms",
            "page_title": "Delivery receipts",
            "channel": channel,
            "address": address,
            "receipts": receipts,
            "suppression": suppression,
            "error": error,
            "urls": _URLS,
        },
        status_code=status_code,
    )


@router.get("/receipts", name="receipts")
def receipts_index(
    request: Request,
    channel: str = "email",
    address: str = "",
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("comms.receipts.read")),
) -> HTMLResponse:
    return _render_receipts(
        request, db, tenant, channel=channel.strip() or "email", address=address.strip()
    )


@router.post("/suppressions/clear", response_model=None, name="clear_suppression")
async def suppression_clear(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("comms.suppressions.manage")),
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    channel = str(form.get("channel", "")).strip()
    address = str(form.get("address", "")).strip()
    reason = str(form.get("reason", "")).strip()
    back = str(form.get("back", "")).strip()
    try:
        cleared = service.clear_suppression(
            db,
            tenant.id,
            channel=channel,
            address=address,
            reason=reason,
            actor_party_id=party.id,
        )
    except BadRequestError as exc:
        return _render_suppressions(
            request,
            db,
            tenant,
            channel="",
            scope="",
            address="",
            page=1,
            error=str(exc),
        )
    if back == "receipts":
        query = urlencode({"channel": channel, "address": address})
        target = f"{RECEIPTS_PATH}?{query}"
    else:
        target = f"{LIST_PATH}?cleared={1 if cleared else 0}"
    response = RedirectResponse(url=target, status_code=302)
    response.headers["HX-Redirect"] = target
    return response
