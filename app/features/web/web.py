"""Web-portal routes: login/logout + the admin dashboard shell.

`GET/POST /admin/login` are deliberately UNGUARDED (pre-auth by definition —
you can't require login to reach the login form) — both are in
`tests/architecture/test_route_guards.py::ALLOWLIST` with a comment.
`GET /admin/logout` is also unguarded: it must clear a stale/expired/garbled
cookie and redirect even when `require_web_auth` would otherwise reject the
request — logout is defined as "always succeeds", not "requires a valid
session" (also allowlisted, with its own comment there).

`GET /admin` (the dashboard) is guarded by `require_web_auth`.

No direct database-query calls in this file (see
`tests/architecture/test_thin_wrappers.py`) — thin-wrapper rule; all of
that lives in `app.features.web.service`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db, require_tenant
from app.core.models import Tenant
from app.core.templating import render
from app.core.web_deps import require_web_auth
from app.features.web import service as web_service

router = APIRouter(prefix="/admin", tags=["web"])

ACCESS_TOKEN_COOKIE = "access_token"  # noqa: S105 # nosec B105 -- cookie name, not a secret


@router.get("/login")
def login_page(
    request: Request,
    next: str = "/admin",
    tenant: Tenant = Depends(require_tenant),
) -> HTMLResponse:
    return render(
        request,
        "auth/login.html",
        {"next_url": web_service.safe_next_url(next), "error": None},
    )


@router.post("/login", response_model=None)
async def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_url = web_service.safe_next_url(str(form.get("next", "/admin")))

    if not username or not password:
        return render(
            request,
            "auth/login.html",
            {
                "next_url": next_url,
                "error": "Username and password are required",
            },
        )

    token = web_service.web_login(db, tenant, username, password)
    if token is None:
        return render(
            request,
            "auth/login.html",
            {"next_url": next_url, "error": "Invalid username or password"},
        )

    response = RedirectResponse(url=next_url, status_code=302)
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        httponly=True,
        secure=web_service.is_secure_request(request),
        samesite="lax",
        path="/",
        max_age=settings.jwt_ttl_seconds,
    )
    # htmx checks for this header on ANY response, before ever consulting
    # hx-target/hx-swap, and does a real `window.location` navigation when
    # present — the login form (templates/auth/login.html) submits via
    # hx-post (required for the CSRF header bridge), so without this header
    # htmx would try to AJAX-swap a 302's followed body into the form
    # instead of actually navigating. A plain (non-htmx) TestClient/browser
    # POST ignores this header entirely and just follows the 302 Location
    # as normal — this is additive, not a behavior change to the redirect
    # itself.
    response.headers["HX-Redirect"] = next_url
    return response


@router.get("/logout")
def logout(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
) -> RedirectResponse:
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    web_service.web_logout(db, tenant, token)
    response = RedirectResponse(url="/admin/login", status_code=302)
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    return response


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
