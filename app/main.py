"""FastAPI app entrypoint.

Middleware order (outermost → innermost):
1. ObservabilityMiddleware — request id + structured request logs
2. TrustedHostMiddleware — drops requests to unknown hosts (prod)
3. TenantResolverMiddleware — sets request.state.tenant
4. RateLimitMiddleware — tenant/ip/path keyed budget
5. CSRFMiddleware — double-submit guard for browser-cookie flows
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.core.config import settings, validate_settings
from app.core.errors import register_error_handlers
from app.core.features import FeatureManifest, load_manifests, mount_features
from app.core.logging import setup_logging
from app.core.middleware.csrf import CSRFMiddleware
from app.core.middleware.observability import ObservabilityMiddleware
from app.core.middleware.rate_limit import RateLimitMiddleware
from app.core.middleware.tenant import TenantResolverMiddleware
from app.features import FEATURE_MODULES

logger = logging.getLogger(__name__)

setup_logging()

# Loaded once at import time (also used by `mount_features` below) — a
# feature's own `feature.py` module is always imported here regardless of
# whether it's disabled (see `app.core.features` docstring: fault isolation
# is mount-time only), but nothing about importing a manifest touches the
# DB. Reused by `lifespan`'s seed dispatch so main.py never hard-imports a
# specific feature's seed function (final-review Group 3) — deleting or
# disabling a feature (e.g. `DISABLED_FEATURES=settings`) must not crash
# import or run that feature's seed.
_manifests = load_manifests(FEATURE_MODULES)


def _run_enabled_seeds(manifests: list[FeatureManifest], disabled: set[str]) -> None:
    for manifest in manifests:
        if manifest.name in disabled or not manifest.enabled_by_default:
            continue
        if manifest.seed is not None:
            manifest.seed()


@asynccontextmanager
async def lifespan(_: FastAPI):
    errors = validate_settings(settings)
    for err in errors:
        if settings.is_production:
            raise RuntimeError(f"Configuration error: {err}")
        logger.warning("Config: %s", err)
    if settings.seed_on_startup:
        _run_enabled_seeds(_manifests, settings.disabled_feature_set)
    yield


app = FastAPI(title="dotmac_starter_mt", lifespan=lifespan)

# FastAPI/Starlette runs the last added middleware first.
app.add_middleware(CSRFMiddleware, enabled=settings.csrf_enabled)
app.add_middleware(
    RateLimitMiddleware,
    enabled=settings.rate_limit_enabled,
    requests=settings.rate_limit_requests,
    window_seconds=settings.rate_limit_window_seconds,
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


register_error_handlers(app)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness — does not touch DB."""
    return {"status": "ok"}


mount_features(
    app,
    manifests=_manifests,
    disabled=settings.disabled_feature_set,
)
