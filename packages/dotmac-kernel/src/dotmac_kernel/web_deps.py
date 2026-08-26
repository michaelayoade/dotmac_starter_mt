"""Cookie-based web auth dependency.

Ported from `ST:app/web/deps.py` (`require_web_auth` + `WebAuthRedirect`
302-on-failure pattern), adapted to this repo's `Party`/`AuthSession` shape
and — the actual point of this module — routed through the SAME shared
validation seam the bearer path uses (`dotmac_kernel.deps.authenticate_request`),
so token/session/tenant/party_type checking has exactly ONE implementation
for both the API (bearer header) and the web portal (cookie).

`require_web_party` adds the browser transport: it reads the assembly-declared
session cookie rather than an `Authorization` header, then decides no
authorization. A `WebFacetMount` owns broad admission through a declared
permission and each v2 route keeps its own granular permission. The historical
`require_web_auth` spelling remains a contract-v1 adapter that returns the
party and role list without hardcoding a role.

It ALSO (Task 4 / F4 fix) is one of the three call sites for
`dotmac_kernel.branding.get_request_branding` — since every authenticated
`/admin/*` route already depends on this function, warming
`request.state.branding` here is the single seam that covers the whole
authenticated portal with zero per-route/per-router changes (see that
module's docstring for the shapes considered and why this one won). The
other two call sites are the pre-auth `GET`/`POST /admin/login` routes
(`app.features.auth.web`), which never reach this function.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from dotmac_kernel.branding import get_request_branding
from dotmac_kernel.deps import authenticate_request, get_db, permission_guard
from dotmac_kernel.display import get_request_display
from dotmac_kernel.models import Party, PartyRoleGrant, Role
from dotmac_kernel.web_surfaces import (
    BrowserAuthenticationProvider,
    BrowserCredentialTransport,
    BrowserSessionPolicy,
    WebSurfaceError,
    current_session_policy,
)


def safe_next_url(
    url: str | None,
    default: str = "/admin",
    *,
    allowed_prefix: str | None = None,
) -> str:
    """Open-redirect guard for any `?next=` query param a web.py route reads.

    Generic HTTP utility (Task 3 review's required fix relocated this from
    `app.features.web.service` — every future feature's `web.py` wants this,
    not just `auth`'s login form). Only a same-origin, absolute path is
    accepted: must start with a single `/` (rejects protocol-relative
    `//evil.example.com`) and must not contain `://` anywhere (rejects
    `/x?u=http://evil.example.com` style smuggling as well as a bare
    `http://evil.example.com` value). A composed login also passes its facet
    prefix, preventing a valid same-origin path from becoming a cross-portal
    post-login destination. Anything else falls back to `default`.
    """
    if not url:
        return default
    if not (url.startswith("/") and not url.startswith("//") and "://" not in url):
        return default
    if allowed_prefix is not None:
        prefix = allowed_prefix.rstrip("/") or "/"
        path = urlsplit(url).path
        if prefix != "/" and path != prefix and not path.startswith(f"{prefix}/"):
            return default
    return url


def is_secure_request(request: Request) -> bool:
    """True if `request` arrived over HTTPS — either directly (`request.url.
    scheme`) or forwarded through a TLS-terminating proxy (`X-Forwarded-
    Proto: https`, the standard header nginx/most LBs set).

    Generic HTTP utility (relocated here alongside `safe_next_url` — see its
    docstring) — drives the `Secure` flag on any cookie a web.py route sets.
    """
    proto = request.headers.get("x-forwarded-proto", "")
    return proto == "https" or request.url.scheme == "https"


class WebAuthRedirect(HTTPException):
    """Raised by `require_web_auth` on ANY auth failure.

    Carries 302 semantics — `dotmac_kernel.errors.register_error_handlers`
    registers a dedicated handler for this exception that issues a real
    `RedirectResponse` to `<login_path>?next=<safe next_url>` (the bare
    `HTTPException(302, ...)` FastAPI would otherwise render as a JSON/HTML
    error envelope, not an actual redirect).

    `login_path` is an explicit compatibility override for a contract-v1
    router. Contract-v2 routes leave it unset; the error handler resolves the
    owning facet's declared `login_route` from the qualified route name. One
    redirect concept therefore supports any assembly-owned prefix without a
    kernel-authored portal path.
    """

    def __init__(
        self, next_url: str = "/admin", *, login_path: str | None = None
    ) -> None:
        self.next_url = next_url
        self.login_path = login_path
        super().__init__(status_code=302, detail="Not authenticated")


def require_web_party(
    request: Request,
    db: Session = Depends(get_db),
) -> Party:
    """Authenticate the tenant browser cookie and decide no authorization.

    Returns the authenticated ``Party`` on success. Raises
    `WebAuthRedirect(next_url=request.url.path)` on any failure — missing
    cookie, invalid/expired token, tenant mismatch, or non-person party. Authorization
    belongs to the facet admission permission and each route's granular guard.
    """
    try:
        session = current_session_policy(request)
    except WebSurfaceError:
        session = BrowserSessionPolicy(cookie_name="access_token")
    token = request.cookies.get(session.cookie_name)
    if not token:
        raise WebAuthRedirect(next_url=request.url.path)

    party = authenticate_request(request, db, token=token)
    if party is None:
        raise WebAuthRedirect(next_url=request.url.path)

    # `authenticate_request` already validated the token's `tenant_id` claim
    # against `request.state.tenant` (set by `TenantResolverMiddleware`
    # before any route runs) — it returns `None`, handled above, if that
    # tenant is missing or the claim doesn't match. So `request.state.tenant`
    # is guaranteed non-None here; re-deriving it via `require_tenant(request)`
    # would just re-check an invariant already proven by the successful
    # `authenticate_request` call above.
    # Call site 1/3 (Task 4 / F4 fix) — warms `request.state.branding` for
    # every authenticated portal page in one place; see this module's and
    # `dotmac_kernel.branding`'s docstrings for the other two (login GET/POST).
    get_request_branding(request, db)

    # Task 2 — warms `request.state.display` (tenant timezone/date/datetime
    # formats) for every authenticated portal page, same seam as branding
    # above. Login/error pages are NOT warmed on purpose (they render no
    # timestamps); the `local_datetime`/`local_date` filter fallback in
    # `dotmac_kernel.templating` covers any future accident.
    get_request_display(request, db)

    return party


def require_web_auth(
    request: Request,
    party: Party = Depends(require_web_party),
    db: Session = Depends(get_db),
) -> dict:
    """Contract-v1 adapter returning the historical party/roles mapping.

    This no longer hardcodes the ``admin`` role. A composed facet owns broad
    portal admission through a declared permission; v2 routes use
    :func:`require_web_permission` for their own decisions.
    """

    tenant = request.state.tenant
    roles = list(
        db.scalars(
            select(Role.slug)
            .join(
                PartyRoleGrant,
                (PartyRoleGrant.role_id == Role.id)
                & (PartyRoleGrant.tenant_id == Role.tenant_id),
            )
            .where(Role.tenant_id == tenant.id)
            .where(PartyRoleGrant.party_id == party.id)
        ).all()
    )
    return {"party": party, "roles": roles}


def require_web_permission(code: str):
    """Cookie-browser binding of the shared permission decision seam."""

    return permission_guard(code, authenticated_party=require_web_party)


class TenantCookieAuthenticationProvider(BrowserAuthenticationProvider):
    """Typed provider selected by an assembly's tenant browser profile."""

    transport = BrowserCredentialTransport.COOKIE_SESSION

    @property
    def dependency(self):
        return require_web_party


TENANT_COOKIE_AUTHENTICATION = TenantCookieAuthenticationProvider()


__all__ = [
    "WebAuthRedirect",
    "TENANT_COOKIE_AUTHENTICATION",
    "TenantCookieAuthenticationProvider",
    "is_secure_request",
    "require_web_auth",
    "require_web_party",
    "require_web_permission",
    "safe_next_url",
]
