"""FastAPI app entrypoint.

Middleware order (outermost → innermost):
1. SecurityHeadersMiddleware — security headers + CSP on EVERY response
2. ObservabilityMiddleware — request id + structured request logs
3. TrustedHostMiddleware — drops requests to unknown hosts (prod)
4. TenantResolverMiddleware — sets request.state.tenant
5. RateLimitMiddleware — tenant/ip/route-template keyed budget (bounded store)
6. CSRFMiddleware — double-submit guard for browser-cookie flows
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from dotmac_kernel.config import settings, validate_settings
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.features import FeatureManifest, load_manifests, mount_features
from dotmac_kernel.logging import setup_logging
from dotmac_kernel.middleware.csrf import CSRFMiddleware
from dotmac_kernel.middleware.observability import ObservabilityMiddleware
from dotmac_kernel.middleware.rate_limit import RateLimitMiddleware
from dotmac_kernel.middleware.security_headers import SecurityHeadersMiddleware
from dotmac_kernel.middleware.tenant import TenantResolverMiddleware
from dotmac_kernel.platform_auth import platform_auth_router
from dotmac_kernel.templating import install_surface_globals
from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from app.features import FEATURE_MODULES

logger = logging.getLogger(__name__)

setup_logging()

# Loaded once at import time (also used by `mount_features` below) — a
# feature's own `feature.py` module is always imported here regardless of
# whether it's disabled (see `dotmac_kernel.features` docstring: fault isolation
# is mount-time only), but nothing about importing a manifest touches the
# DB. Reused by `lifespan`'s seed dispatch so main.py never hard-imports a
# specific feature's seed function (final-review Group 3) — deleting or
# disabling a feature (e.g. `DISABLED_FEATURES=settings`) must not crash
# import or run that feature's seed.
_manifests = load_manifests(FEATURE_MODULES)

# Process-static `enabled_features`/`nav_items` Jinja globals (F1/F5
# capability model) — the manifests are the single source of truth for both
# the sidebar and any template's optional-slot conditional
# (`{% if 'x' in enabled_features %}`). Must run before any template ever
# renders, so it happens here at import time, right after `_manifests` is
# built — see dotmac_kernel.templating.install_surface_globals's docstring.
install_surface_globals(_manifests, settings.disabled_feature_set, settings.web_enabled)


async def _run_enabled_seeds(
    manifests: list[FeatureManifest], disabled: set[str]
) -> None:
    """Run each enabled manifest's seed hook off the event loop.

    DEFERRED, NON-FATAL: seeds are idempotent (e.g. the settings feature's
    own seed hook never overwrites an existing row), so a failed seed (e.g.
    an unreachable DATABASE_URL) must never prevent the app from serving —
    the liveness contract (`/health` never touches the DB, CI's docker-build
    job health-gates a container booted against a deliberately unreachable
    DB) must hold even when the seed step fails. The next boot retries.
    `manifest.seed()` does sync DB I/O, so it's dispatched via
    `asyncio.to_thread` rather than blocking the event loop.
    """
    for manifest in manifests:
        if manifest.name in disabled or not manifest.enabled_by_default:
            continue
        if manifest.seed is not None:
            try:
                await asyncio.to_thread(manifest.seed)
            except Exception as exc:
                # seed failure must be swallowed here so it can never take
                # down startup; see docstring above.
                logger.warning("Feature %s seed skipped: %s", manifest.name, exc)


@asynccontextmanager
async def lifespan(_: FastAPI):
    errors = validate_settings(settings)
    for err in errors:
        if settings.is_production:
            raise RuntimeError(f"Configuration error: {err}")
        logger.warning("Config: %s", err)
    if settings.seed_on_startup:
        await _run_enabled_seeds(_manifests, settings.disabled_feature_set)
    yield


app = FastAPI(title="dotmac_starter_mt", lifespan=lifespan)

# FastAPI/Starlette runs the last added middleware first.
app.add_middleware(CSRFMiddleware, enabled=settings.csrf_enabled)
app.add_middleware(
    RateLimitMiddleware,
    enabled=settings.rate_limit_enabled,
    requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_seconds,
    max_keys=settings.rate_limit_max_keys,
)

app.add_middleware(TenantResolverMiddleware)

# Trusted hosts — only enable in prod with explicit list.
if settings.trusted_hosts:
    hosts = [h.strip() for h in settings.trusted_hosts.split(",") if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

app.add_middleware(
    ObservabilityMiddleware,
    trust_inbound_request_id=settings.trust_inbound_request_id,
)

# OUTERMOST (added last → runs first): every response — including middleware
# short-circuits (tenant 404s, 429s) and error pages — carries the security
# headers + CSP. See app/core/middleware/security_headers.py.
app.add_middleware(
    SecurityHeadersMiddleware,
    enabled=settings.security_headers_enabled,
    content_security_policy=settings.content_security_policy,
)


register_error_handlers(app)

# Serves templates/base.html's <link>/<script> asset URLs (built by
# `npm run css:build`; see dotmac_kernel.templating.static_asset_url). No guard
# needed — static assets are public by nature and StaticFiles mounts a
# Starlette `Mount`, not an `APIRoute`, so it's outside
# tests/architecture/test_route_guards.py's route-guard sweep. Gated on
# `web_enabled` (F1): there is no HTML UI to serve these assets to in
# API-only mode, so the mount simply doesn't exist — WEB_ENABLED=false pins
# zero /static routes, not just zero /admin routes.
if settings.web_enabled:
    app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness — does not touch DB."""
    return {"status": "ok"}


# Platform auth mounts DIRECTLY — not via a feature manifest. The manifest/
# capability model is for tenant capabilities; the platform control plane
# must exist even with every feature disabled (see dotmac_kernel.platform_auth's
# module docstring and ADR-0004).
app.include_router(platform_auth_router)

mount_features(
    app,
    manifests=_manifests,
    disabled=settings.disabled_feature_set,
    web_enabled=settings.web_enabled,
)
