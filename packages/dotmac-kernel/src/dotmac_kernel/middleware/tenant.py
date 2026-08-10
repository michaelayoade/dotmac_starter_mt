"""Tenant resolver middleware.

Resolves a Tenant from the incoming Host header and attaches it to
`request.state.tenant`. Routes that require a tenant use `Depends(require_tenant)`;
platform routes assert `request.state.tenant is None`.

Resolution order:
1. Custom domain match in `tenant_domains.verified_at IS NOT NULL`
2. Subdomain extraction against PLATFORM_ROOT_DOMAIN
3. Host == PLATFORM_ROOT_DOMAIN → no tenant (platform routes only)
4. Otherwise: 404

Two path-prefix bypasses run BEFORE resolution (no DB query at all): exact
`_HEALTH_PATHS` members (see that constant), and anything under `/static/`
(`path == "/static"` or `path.startswith("/static/")`, plain string checks —
no regex, so `/staticevil` and `/static2/x` do NOT match; see
`tests/unit/test_tenant_middleware.py` for the near-miss coverage). Static
assets (`app.mount("/static", ...)` in app/main.py) are public by
construction and served with no tenant context, so resolving a tenant for
them is both wasted work and a correctness bug: before this bypass, a
`/static/css/main.css` request with the DB unreachable 500'd instead of
serving the asset — `TenantResolverMiddleware.dispatch` opened a `SessionLocal()`
for every static request and had nothing to catch that exception. See
`docs/ARCHITECTURE.md`'s "Health bypass" section for the same story applied
to `/health`.
"""

from __future__ import annotations

import logging

from fastapi.responses import JSONResponse
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from dotmac_kernel.config import settings
from dotmac_kernel.db import resolver_session
from dotmac_kernel.errors import envelope
from dotmac_kernel.models import Tenant, TenantDomain
from dotmac_kernel.tenancy import single_tenant_binding

logger = logging.getLogger(__name__)

_HEALTH_PATHS = frozenset({"/health", "/health/ready"})
_STATIC_PATH = "/static"
_STATIC_PATH_PREFIX = "/static/"


def _is_static_path(path: str) -> bool:
    """Plain string checks only (no regex — see module docstring): exact
    `/static` or anything under `/static/`. `/staticevil` and `/static2/x`
    must NOT match — `startswith("/static")` alone would wrongly catch both."""
    return path == _STATIC_PATH or path.startswith(_STATIC_PATH_PREFIX)


class TenantResolverMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self._root = settings.platform_root_domain.lower().lstrip(".")

    async def dispatch(self, request: Request, call_next):
        host = (request.headers.get("host") or "").split(":")[0].lower()

        # Liveness/readiness checks must not touch the DB (they run before a
        # DB may even be reachable — container smoke tests, orchestrator
        # health probes). Short-circuit before resolution, per the /health
        # route's "does not touch DB" contract in app/main.py.
        if request.url.path in _HEALTH_PATHS:
            request.state.tenant = None
            return await call_next(request)

        # Static assets (app.mount("/static", ...) in app/main.py) are
        # public and tenant-agnostic — short-circuit before resolution for
        # the same reason as health paths above (see module docstring: this
        # was a real 500 when the DB was unreachable).
        if _is_static_path(request.url.path):
            request.state.tenant = None
            return await call_next(request)

        request.state.tenant = self._allow(self._resolve(host))

        # Platform paths are allowed without a tenant.
        if request.state.tenant is None and not _is_platform_path(
            request.url.path,
            host,
            self._root,
        ):
            return JSONResponse(
                status_code=404,
                content=envelope("tenant_not_found", "Tenant not found"),
            )
        return await call_next(request)

    def _resolve(self, host: str) -> Tenant | None:
        if not host:
            return None
        with resolver_session() as db:
            # 1. Custom domain
            tenant = db.scalars(
                select(Tenant)
                .join(TenantDomain, TenantDomain.tenant_id == Tenant.id)
                .where(TenantDomain.domain == host)
                .where(TenantDomain.verified_at.is_not(None))
                .where(Tenant.is_active.is_(True))
                .where(Tenant.deleted_at.is_(None))
                .limit(1)
            ).first()
            if tenant is not None:
                return tenant

            # 2. Subdomain on platform_root_domain
            suffix = "." + self._root
            if host.endswith(suffix):
                slug = host[: -len(suffix)]
                if slug and "." not in slug:  # reject nested subdomains
                    return db.scalars(
                        select(Tenant)
                        .where(Tenant.slug == slug)
                        .where(Tenant.is_active.is_(True))
                        .where(Tenant.deleted_at.is_(None))
                        .limit(1)
                    ).first()

            # 3. Host == root domain → platform context
            if host == self._root:
                return None

            # 4. Unknown host → caller decides (will 404)
            return None

    def _allow(self, tenant: Tenant | None) -> Tenant | None:
        """Refuse a tenant this deployment is not bound to.

        The primary control for single-tenancy is the startup assertion in
        `create_app`'s lifespan: under `TENANCY=single` the kernel refuses to
        boot unless exactly one tenant row exists. That catches the real hazard
        — a restored backup, a migration rehearsal, a shared database someone
        meant to split — at deploy time, loudly, rather than waiting for someone
        to guess a hostname.

        This is the second half: a tenant created *after* startup would satisfy
        no assertion until the next restart, and would otherwise be served to
        anyone who knew its host. The binding comes from that startup check, so
        the identity is whatever the database actually held — it is never
        configured, and so cannot drift from it.

        Refuses rather than substitutes: a host resolving to the wrong tenant is
        a misconfiguration, and quietly serving the right one would hide it.
        """
        bound = single_tenant_binding()
        if tenant is None or bound is None:
            return tenant
        if tenant.slug.lower() != bound:
            logger.warning(
                "rejected host for tenant slug=%s on a deployment bound to slug=%s",
                tenant.slug,
                bound,
            )
            return None
        return tenant
        if tenant.slug.lower() != self._single_tenant:
            logger.warning(
                "rejected host for tenant slug=%s on a deployment locked to slug=%s",
                tenant.slug,
                self._single_tenant,
            )
            return None
        return tenant


def _is_platform_path(path: str, host: str, root: str) -> bool:
    """Requests that may proceed without a resolved tenant: ANY path on the
    platform root host (platform routes; unresolved paths there 404 in
    routing), plus the DB-free health paths on any host.

    Host-exact by design (control-plane security Task 1): a `/platform/*`
    request on a tenant or unknown host is NOT platform-valid — it 404s like
    any other unresolved path. The previous `startswith("/platform/")`
    branch passed `/platform/*` on ANY host, which let unknown hosts reach
    platform routes; `require_platform_admin` re-checks the host as
    defense-in-depth, but the middleware must not forward those requests in
    the first place. See `PLATFORM_ROOT_DOMAIN` in `.env.example`.
    """
    return host == root or path in _HEALTH_PATHS


__all__ = [
    "TenantResolverMiddleware",
]
