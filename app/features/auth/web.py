"""Auth's web (cookie) surface: `GET/POST /admin/login` + `POST /admin/logout`.

Relocated here from `app.features.web.web` per Task 3 review's required fix
(see `.superpowers/sdd/task-3-report.md`'s fix note) — login/logout are
auth's business, not the dashboard shell's, and moving them here eliminates
the cross-feature `app.features.web.service -> app.features.auth.service`
import entirely (the `pyproject.toml` `ignore_imports` carve-out it required
is deleted along with it).

`GET/POST /admin/login` are deliberately UNGUARDED (pre-auth by definition —
you can't require login to reach the login form) — both are in
`tests/architecture/test_route_guards.py::ALLOWLIST` with a comment.

`POST /admin/logout` (F7 fix — was `GET /admin/logout`, a CSRF-exempt safe
method that let a third-party page force a victim's logout by merely
embedding an `<img src="/admin/logout">`; see
`docs/superpowers/plans/2026-07-18-phase2b1-sot-composability.md` Task 5).
Making it a POST puts it back under `CSRFMiddleware`'s double-submit check
like every other mutation. It still carries only `Depends(require_tenant)`,
no `require_web_auth` — session self-termination must not require a
role/auth-tier check: revoking YOUR OWN session is always allowed for any
authenticated cookie (admin or not), the same way you don't need to already
be "authorized" to hit "log out". CSRF protection (not a role check) is
what stops a FORCED logout now. `tests/architecture/test_route_guards.py`'s
`MUTATING_ALLOWLIST` carries this route with the same "matches
`POST /admin/login`'s reasoning" comment; the non-admin route sweep
(`tests/unit/test_admin_route_sweep.py`) explicitly skips it for the
identical reason — a non-admin cookie logging itself out is success, not a
guard failure, so the sweep's "must redirect because the guard rejected it"
assertion would be testing the wrong thing here.

No direct database-query calls in this file (see
`tests/architecture/test_thin_wrappers.py`) — thin-wrapper rule; all of
that lives in `app.features.auth.service`.
"""

from __future__ import annotations

from urllib.parse import quote

from dotmac_kernel.branding import get_request_branding
from dotmac_kernel.config import settings
from dotmac_kernel.deps import get_db, require_tenant
from dotmac_kernel.models import Tenant
from dotmac_kernel.templating import render
from dotmac_kernel.web_deps import is_secure_request, safe_next_url
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.features.auth import service as auth_service

router = APIRouter(prefix="/admin", tags=["web"])

ACCESS_TOKEN_COOKIE = "access_token"  # noqa: S105 # nosec B105 -- cookie name, not a secret


@router.get("/login")
def login_page(
    request: Request,
    next: str = "/admin",
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
) -> HTMLResponse:
    # Call site 2/3 (Task 4 / F4 fix) — the login page is pre-auth, so it
    # never goes through `require_web_auth` (call site 1/3); tenant is
    # already host-resolved (`require_tenant` above) so the tenant's saved
    # branding shows on ITS OWN login page even before anyone signs in. See
    # `dotmac_kernel.branding`'s module docstring for the full seam decision.
    get_request_branding(request, db)
    return render(
        request,
        "auth/login.html",
        {"next_url": safe_next_url(next), "error": None},
    )


@router.post("/login", response_model=None)
async def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
) -> HTMLResponse | RedirectResponse:
    # Call site 3/3 (Task 4 / F4 fix) — a failed submit re-renders
    # `auth/login.html` below without a redirect, so it needs the same
    # branding the GET above would have shown; see that route's comment.
    get_request_branding(request, db)
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_url = safe_next_url(str(form.get("next", "/admin")))

    if not username or not password:
        return render(
            request,
            "auth/login.html",
            {
                "next_url": next_url,
                "error": "Username and password are required",
            },
        )

    token = auth_service.web_login(db, tenant, username, password)
    if token is None:
        return render(
            request,
            "auth/login.html",
            {"next_url": next_url, "error": "Invalid username or password"},
        )

    # `quote()` unconditionally, matching the `WebAuthRedirect` handler's
    # practice (`dotmac_kernel.errors`) — defense-in-depth even though
    # `safe_next_url` already constrains `next_url` to a same-origin path.
    response = RedirectResponse(url=quote(next_url, safe="/?=&"), status_code=302)
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE,
        value=token,
        httponly=True,
        secure=is_secure_request(request),
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


@router.post("/logout", response_model=None)
def logout(
    request: Request,
    db: Session = Depends(get_db),
    tenant: Tenant = Depends(require_tenant),
) -> RedirectResponse:
    # F7 fix: POST, not GET — see module docstring. `require_tenant` only
    # (no `require_web_auth`): a missing/expired/garbled/foreign-tenant
    # cookie must still redirect to login cleanly, not 401 — logout is
    # defined as "always succeeds", not "requires a valid session".
    token = request.cookies.get(ACCESS_TOKEN_COOKIE)
    auth_service.web_logout(db, tenant, token)
    # `quote()` unconditionally, matching the `WebAuthRedirect` handler's
    # practice (`dotmac_kernel.errors`) — this Location is a hardcoded literal
    # today, but quoting keeps both `RedirectResponse` call sites in this
    # module consistent (defense-in-depth) rather than relying on the
    # literal never changing.
    response = RedirectResponse(url=quote("/admin/login", safe="/?=&"), status_code=302)
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    # htmx: the topbar's Sign Out control is `hx-post` (required for the
    # CSRF header bridge — see CLAUDE.md's "CSRF header-bridge contract").
    # Without this header htmx would try to swap the redirect's followed
    # body into the topbar instead of actually navigating — same reasoning
    # as `login_submit`'s identical header above.
    response.headers["HX-Redirect"] = "/admin/login"
    return response


__all__ = ["router"]
