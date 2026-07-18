"""Cookie-based web auth dependency.

Ported from `ST:app/web/deps.py` (`require_web_auth` + `WebAuthRedirect`
302-on-failure pattern), adapted to this repo's `Party`/`AuthSession` shape
and — the actual point of this module — routed through the SAME shared
validation seam the bearer path uses (`app.core.deps.authenticate_request`),
so token/session/tenant/party_type checking has exactly ONE implementation
for both the API (bearer header) and the web portal (cookie).

`require_web_auth` layers two things `authenticate_request` does not do
(they're web-portal-specific, not part of the generic token/session
contract): (1) reads the token from the `access_token` COOKIE rather than an
`Authorization` header — no header fallback, this is web-only; (2) requires
role `"admin"` on top of `party_type == person` — this app's default-deny
shape for phase 2b (every portal page is admin-only until phase 3 adds
per-portal roles; see the inline comment below).
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.deps import authenticate_request
from app.core.models import PartyRole, Role


def safe_next_url(url: str | None, default: str = "/admin") -> str:
    """Open-redirect guard for any `?next=` query param a web.py route reads.

    Generic HTTP utility (Task 3 review's required fix relocated this from
    `app.features.web.service` — every future feature's `web.py` wants this,
    not just `auth`'s login form). Only a same-origin, absolute path is
    accepted: must start with a single `/` (rejects protocol-relative
    `//evil.example.com`) and must not contain `://` anywhere (rejects
    `/x?u=http://evil.example.com` style smuggling as well as a bare
    `http://evil.example.com` value). Anything else falls back to `default`.
    """
    if not url:
        return default
    if url.startswith("/") and not url.startswith("//") and "://" not in url:
        return url
    return default


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

    Carries 302 semantics — `app.core.errors.register_error_handlers`
    registers a dedicated handler for this exception that issues a real
    `RedirectResponse` to `/admin/login?next=<safe next_url>` (the bare
    `HTTPException(302, ...)` FastAPI would otherwise render as a JSON/HTML
    error envelope, not an actual redirect).
    """

    def __init__(self, next_url: str = "/admin") -> None:
        self.next_url = next_url
        super().__init__(status_code=302, detail="Not authenticated")


def require_web_auth(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    """Cookie-based guard for `/admin/*` portal routes.

    Returns `{"party": Party, "roles": list[str]}` on success. Raises
    `WebAuthRedirect(next_url=request.url.path)` on any failure — missing
    cookie, invalid/expired token, tenant mismatch, non-person party, or
    missing the `"admin"` role. Never a bare 401/403 and never a 500: an
    unauthenticated/unauthorized portal visit is always a redirect to login.

    NOTE (phase 3 TODO): every portal page requires the `"admin"` role today
    — this repo has no other portal-facing role yet. Loosen this per-route
    once non-admin portal surfaces exist (e.g. a self-service party view),
    per the task brief's "sub's default-deny shape" note.
    """
    token = request.cookies.get("access_token")
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
    tenant = request.state.tenant
    roles = list(
        db.scalars(
            select(Role.slug)
            .join(
                PartyRole,
                (PartyRole.role_id == Role.id)
                & (PartyRole.tenant_id == Role.tenant_id),
            )
            .where(Role.tenant_id == tenant.id)
            .where(PartyRole.party_id == party.id)
        ).all()
    )
    if "admin" not in roles:
        raise WebAuthRedirect(next_url=request.url.path)

    return {"party": party, "roles": roles}


__all__ = [
    "WebAuthRedirect",
    "is_secure_request",
    "require_web_auth",
    "safe_next_url",
]
