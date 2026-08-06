"""`create_app(spec)` — build a running FastAPI app from a ProductAssemblySpec
(kernel-boundary Task 3A).

This is everything the reference `app/main.py` used to do at module scope, moved
into the kernel and driven by the spec instead of module-level imports of the
assembly's features: logging, module-registry validation, surface globals, the
lifespan (config validation + module seeds), the middleware stack, error
handlers, the platform-auth surface, the static mount (with
assembly-over-kernel override), and module mounting. A product's `main.py`
shrinks to building one spec and calling this.

Module validation happens FIRST and fails closed: `ModuleRegistry(spec.modules)`
(see `dotmac_kernel.modules`) proves the installed set is coherent and yields
the deterministic startup order every later step consumes — surface globals,
mounting, and seeds all walk that same order, so there is one answer to "what
runs, in what sequence".

Re-exported as `dotmac_kernel.create_app`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence, Set
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.config import settings, validate_settings
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.features import mount_features
from dotmac_kernel.logging import setup_logging
from dotmac_kernel.middleware.csrf import CSRFMiddleware
from dotmac_kernel.middleware.observability import ObservabilityMiddleware
from dotmac_kernel.middleware.rate_limit import RateLimitMiddleware
from dotmac_kernel.middleware.security_headers import SecurityHeadersMiddleware
from dotmac_kernel.middleware.tenant import TenantResolverMiddleware
from dotmac_kernel.modules import AnyManifest, ModuleRegistry
from dotmac_kernel.platform_auth import platform_auth_router
from dotmac_kernel.templating import (
    install_surface_globals,
    static_dir,
    use_assembly_templates,
)

logger = logging.getLogger(__name__)


class LayeredStaticFiles(StaticFiles):
    """Serve from an ordered list of directories, first match wins — so an
    assembly's own static file shadows the kernel's same-named one while
    un-overridden assets still come from the packaged kernel static."""

    def __init__(self, directories: Sequence[str]) -> None:
        super().__init__(directory=directories[0], check_dir=False)
        self.all_directories = list(directories)


async def _run_enabled_seeds(
    manifests: Sequence[AnyManifest], disabled: Set[str]
) -> None:
    """Run each enabled manifest's seed hook off the event loop. DEFERRED,
    NON-FATAL: a failed seed (e.g. an unreachable DATABASE_URL) must never stop
    the app from serving — `/health` stays DB-free and the next boot retries."""
    for manifest in manifests:
        if manifest.name in disabled or not manifest.enabled_by_default:
            continue
        if manifest.seed is not None:
            try:
                await asyncio.to_thread(manifest.seed)
            except Exception as exc:  # swallow: seed failure never downs startup
                logger.warning("Feature %s seed skipped: %s", manifest.name, exc)


def create_app(spec: ProductAssemblySpec) -> FastAPI:
    """Compose a FastAPI application for `spec`. Behavior is identical to the
    pre-Task-3 `app/main.py` when given the reference spec; the spec's
    `assembly_template_dir`/`assembly_static_dir` add assembly-over-kernel
    overrides, and `disabled_modules`/`web_enabled` drive the surface.

    `spec.modules` is validated into a `ModuleRegistry` first and mounted in its
    deterministic startup order. For modules declaring no dependencies (every
    `FeatureManifest` today) that order is exactly the declaration order, so
    adding the registry does not move a single route.

    Raises `dotmac_kernel.modules.ModuleRegistryError` (a `ValueError`) if the
    module set is incoherent."""
    setup_logging()

    disabled = set(spec.disabled_modules)
    web_enabled = spec.web_enabled

    # VALIDATE BEFORE ANYTHING IS MOUNTED (module control-plane directive step
    # 2). Construction of the registry IS the validation — unique codes,
    # supported contract versions, installed dependencies, no cycles — and
    # `enabled_order` additionally proves every enabled module's dependencies
    # are themselves enabled in THIS deployment. Any of those failing raises a
    # `ModuleRegistryError` here, before a single route exists, rather than
    # surfacing as a mystery 500 later.
    registry = ModuleRegistry(spec.modules)
    manifests = list(registry.startup_order())
    enabled_manifests = list(registry.enabled_order(disabled))
    logger.info(
        "Module startup order (%d of %d enabled): %s",
        len(enabled_manifests),
        len(manifests),
        ", ".join(f"{m.code}@{m.version}" for m in enabled_manifests) or "(none)",
    )

    # Process-static Jinja globals (enabled_features / nav_items) — must be set
    # before any template renders. Fed the FULL installed set in startup order
    # (it applies the same enabled rule itself), so the sidebar order matches
    # the mount order instead of being derived separately.
    install_surface_globals(manifests, disabled, web_enabled)

    # Assembly-over-kernel template precedence (ChoiceLoader), if the assembly
    # ships its own templates.
    if spec.assembly_template_dir is not None:
        use_assembly_templates(spec.assembly_template_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for err in validate_settings(settings):
            if settings.is_production:
                raise RuntimeError(f"Configuration error: {err}")
            logger.warning("Config: %s", err)
        if settings.seed_on_startup:
            await _run_enabled_seeds(enabled_manifests, disabled)
        yield

    app = FastAPI(title=spec.name, lifespan=lifespan)

    # Installed-module inventory for health/diagnostics consumers. The kernel
    # exposes the CONTRACT on app state, not an endpoint: public `/health` below
    # is liveness only and must disclose nothing about what is installed, and an
    # authenticated platform diagnostics surface is the control plane's own
    # step. A product composes `inventory_payload()` into whichever surface its
    # deployment profile permits.
    app.state.module_registry = registry
    app.state.module_inventory = registry.inventory(disabled)

    # FastAPI/Starlette runs the LAST added middleware first — order preserved
    # from the reference app (innermost CSRF → outermost SecurityHeaders).
    app.add_middleware(CSRFMiddleware, enabled=settings.csrf_enabled)
    app.add_middleware(
        RateLimitMiddleware,
        enabled=settings.rate_limit_enabled,
        requests=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
        max_keys=settings.rate_limit_max_keys,
    )
    app.add_middleware(TenantResolverMiddleware)
    if settings.trusted_hosts:
        hosts = [h.strip() for h in settings.trusted_hosts.split(",") if h.strip()]
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.add_middleware(
        ObservabilityMiddleware,
        trust_inbound_request_id=settings.trust_inbound_request_id,
    )
    # OUTERMOST: security headers + CSP on every response.
    app.add_middleware(
        SecurityHeadersMiddleware,
        enabled=settings.security_headers_enabled,
        content_security_policy=settings.content_security_policy,
    )

    register_error_handlers(app)

    # Static mount — the kernel's packaged static, with the assembly's own dir
    # layered on top when provided (Task 3A static override). Absent in API-only
    # mode (web_enabled=False), exactly like the reference app.
    if web_enabled:
        static: StaticFiles
        if spec.assembly_static_dir is not None:
            static = LayeredStaticFiles(
                [str(spec.assembly_static_dir), str(static_dir())]
            )
        else:
            static = StaticFiles(directory=str(static_dir()))
        app.mount("/static", static, name="static")

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness — does not touch DB."""
        return {"status": "ok"}

    # Platform auth mounts DIRECTLY (not a feature manifest) — the platform
    # control plane must exist even with every feature disabled.
    app.include_router(platform_auth_router)

    mount_features(
        app,
        manifests=enabled_manifests,
        disabled=disabled,
        web_enabled=web_enabled,
    )
    return app


__all__ = ["create_app", "LayeredStaticFiles"]
