"""Operator surface for the consent ledger and delivery receipts (K-7).

`/admin/comms/suppressions` lists the tenant's suppressions (filter by channel,
scope, address); `/admin/comms/receipts` shows what providers said about one
address (by suppression id from the list, or a free address lookup);
`POST /admin/comms/suppressions/{id}/restore` restores transactional mail for
one suppression with a recorded reason (scope `all` -> `marketing`; the row is
kept and marketing stays blocked). Same shape as the other admin screens: thin
wrappers over `service.py`, `render()` for HTML, the htmx-header CSRF contract,
"re-render at 200, don't redirect" on a refused input.

Links and redirects carry the suppression id, not the address, so the address
stays out of access logs except on the free lookup form.

Permissions are declared by this feature (`feature.py`) and checked per route:
read for the lists, `comms_operations.suppressions.manage` for the mutation.
"""

from __future__ import annotations

from uuid import UUID

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
RESTORE_PATH = "/admin/comms/suppressions/{suppression_id}/restore"
#: Paths handed to the templates, so no template authors a facet prefix.
_URLS = {
    "suppressions": LIST_PATH,
    "receipts": RECEIPTS_PATH,
    "restore_prefix": LIST_PATH + "/",
}
_RESTORED_NOTICES = {
    "1": (
        "Transactional mail restored; marketing stays blocked. Messages that "
        "were suppressed earlier are not re-sent: ask the customer to request "
        "again."
    ),
    "0": "No such suppression; nothing was changed.",
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
    restored: str = "",
    page: int = Query(default=1, ge=1),
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(
        require_web_permission("comms_operations.suppressions.read")
    ),
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
            notice=_RESTORED_NOTICES.get(restored),
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
    notice: str | None = None,
    error: str | None = None,
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
            "notice": notice,
            "error": error,
            "urls": _URLS,
        },
    )


@router.get("/receipts", name="receipts")
def receipts_index(
    request: Request,
    channel: str = "email",
    address: str = "",
    suppression_id: UUID | None = None,
    restored: str = "",
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(require_web_permission("comms_operations.receipts.read")),
) -> HTMLResponse:
    channel = channel.strip() or "email"
    address = address.strip()
    if suppression_id is not None:
        # By id: the address never rides in the URL. Tenant-scoped; an unknown
        # or foreign id is simply "no such address".
        row = service.get_suppression_by_id(db, tenant.id, suppression_id)
        if row is not None:
            channel, address = row.channel, row.address
    return _render_receipts(
        request,
        db,
        tenant,
        channel=channel,
        address=address,
        notice=_RESTORED_NOTICES.get(restored),
    )


@router.post(
    "/suppressions/{suppression_id}/restore",
    response_model=None,
    name="restore_suppression",
)
async def suppression_restore(
    suppression_id: UUID,
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
    party: Party = Depends(
        require_web_permission("comms_operations.suppressions.manage")
    ),
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    reason = str(form.get("reason", "")).strip()
    back = str(form.get("back", "")).strip()
    try:
        restored = service.restore_transactional_mail(
            db,
            tenant.id,
            suppression_id=suppression_id,
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
    flag = 1 if restored else 0
    if back == "receipts":
        target = f"{RECEIPTS_PATH}?suppression_id={suppression_id}&restored={flag}"
    else:
        target = f"{LIST_PATH}?restored={flag}"
    response = RedirectResponse(url=target, status_code=302)
    response.headers["HX-Redirect"] = target
    return response
