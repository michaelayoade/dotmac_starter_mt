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
from dotmac_kernel.audit_actions import AuditActionRegistry, install_audit_actions
from dotmac_kernel.capabilities import (
    CAPABILITY_CODE_ATTR,
    CapabilityCatalogue,
    UndeclaredCapabilityError,
    install_capabilities,
)
from dotmac_kernel.config import settings, validate_settings
from dotmac_kernel.errors import register_error_handlers
from dotmac_kernel.features import mount_features
from dotmac_kernel.flags import FlagCatalogue, install_flags
from dotmac_kernel.logging import setup_logging
from dotmac_kernel.middleware.csrf import CSRFMiddleware
from dotmac_kernel.middleware.observability import ObservabilityMiddleware
from dotmac_kernel.middleware.rate_limit import RateLimitMiddleware
from dotmac_kernel.middleware.security_headers import SecurityHeadersMiddleware
from dotmac_kernel.middleware.tenant import TenantResolverMiddleware
from dotmac_kernel.modules import AnyManifest, ModuleRegistry
from dotmac_kernel.permissions import (
    PERMISSION_CODE_ATTR,
    PermissionCatalogue,
    UndeclaredPermissionError,
    install_permissions,
)
from dotmac_kernel.platform_auth import platform_auth_router
from dotmac_kernel.platform_web import router as platform_web_router
from dotmac_kernel.templating import (
    compose_templates,
    install_stylesheets,
    install_surface_globals,
    static_dir,
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


def _referenced_codes(app: FastAPI, attr: str) -> list[tuple[str, str]]:
    """(route label, declared code) for every code a MOUNTED route references
    through a guard that stamps `attr` on its dependency callable.

    ONE walker for both declaration kinds — `PERMISSION_CODE_ATTR` (who may act)
    and `CAPABILITY_CODE_ATTR` (whether the tenant has the feature). The two are
    different decisions but the same discovery problem, and a second copy of
    this traversal would be one more place for the dependency-tree walk to drift.

    Scoped to this app's routes, not a process-wide tally of every guard ever
    imported: an assembly that imports a module's routers without mounting them
    must not be failed for a code it never exposes.
    """
    found: list[tuple[str, str]] = []
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        codes: set[str] = set()
        stack = list(dependant.dependencies)
        while stack:
            dep = stack.pop()
            code = getattr(dep.call, attr, None)
            if isinstance(code, str):
                codes.add(code)
            stack.extend(dep.dependencies)
        methods = sorted(getattr(route, "methods", None) or {"?"})
        label = f"{'/'.join(methods)} {getattr(route, 'path', '?')}"
        found.extend((label, code) for code in sorted(codes))
    return found


def _referenced_permissions(app: FastAPI) -> list[tuple[str, str]]:
    """Permission codes referenced by this app's mounted routes."""
    return _referenced_codes(app, PERMISSION_CODE_ATTR)


def _referenced_capabilities(app: FastAPI) -> list[tuple[str, str]]:
    """Capability codes referenced by this app's mounted routes."""
    return _referenced_codes(app, CAPABILITY_CODE_ATTR)


def _validate_referenced_permissions(
    app: FastAPI, catalogue: PermissionCatalogue
) -> None:
    """Fail the BOOT when a mounted route requires a permission no installed
    module declares. Without this a typo'd code is invisible until the first
    request reaches that route — and then denies it, which reads as a
    permissions bug rather than a declaration bug."""
    undeclared = [
        (label, code)
        for label, code in _referenced_permissions(app)
        if not catalogue.is_declared(code)
    ]
    if undeclared:
        listed = ", ".join(f"{label} -> {code!r}" for label, code in undeclared)
        raise UndeclaredPermissionError(
            f"route(s) require a permission code no installed module declares: "
            f"{listed} — declare it on the owning module's manifest "
            "(`permissions=(PermissionSpec(...),)`)"
        )


def _validate_referenced_capabilities(
    app: FastAPI, catalogue: CapabilityCatalogue
) -> None:
    """Fail the BOOT when a mounted route requires a capability no installed
    module declares (module control-plane directive step 4).

    Worse than the permission case if it were left to request time: an
    undeclared capability code makes `require_capability` raise for EVERY
    tenant, forever, on a route that looks correctly wired. That reads as "no
    one is entitled" — an operations problem someone would go looking for in the
    grant table — when it is really a typo in a declaration.
    """
    undeclared = [
        (label, code)
        for label, code in _referenced_capabilities(app)
        if not catalogue.is_declared(code)
    ]
    if undeclared:
        listed = ", ".join(f"{label} -> {code!r}" for label, code in undeclared)
        raise UndeclaredCapabilityError(
            f"route(s) require a capability code no installed module declares: "
            f"{listed} — declare it on the owning module's manifest "
            "(`capabilities=(...,)`)"
        )


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

    # Process-active declaration catalogues (module control-plane step 3), built
    # from the INSTALLED set — not the enabled subset: disabling a module must
    # not turn a real permission code or audit action into an undeclared one for
    # whatever is still running. Installed BEFORE anything is mounted, because
    # both are read at request/write time by code the mount produces.
    permission_catalogue = PermissionCatalogue.from_manifests(manifests)
    install_permissions(permission_catalogue)
    install_audit_actions(AuditActionRegistry.from_manifests(manifests))
    # Capabilities join them (step 4). Same installed-not-enabled rule, and the
    # same reason it matters more here: a tenant's entitlement GRANT references a
    # capability code and outlives any deployment's enabled set, so a disabled
    # module must not make an existing grant unexplainable.
    capability_catalogue = CapabilityCatalogue.from_manifests(manifests)
    install_capabilities(capability_catalogue)
    # Feature flags (step 5). Same installed-not-enabled rule: an override row
    # references a flag code and outlives any deployment's enabled set.
    install_flags(FlagCatalogue.from_manifests(manifests))

    # Process-static Jinja globals (enabled_features / nav_items) — must be set
    # before any template renders. Fed the FULL installed set in startup order
    # (it applies the same enabled rule itself), so the sidebar order matches
    # the mount order instead of being derived separately.
    install_surface_globals(manifests, disabled, web_enabled)

    # Extra stylesheet links for every page's <head> (installed presentation
    # packages' compiled CSS — see `install_stylesheets`). Empty in API-only
    # mode: there is no <head> to add them to.
    install_stylesheets(spec.stylesheets if web_enabled else ())

    # Template precedence, most specific first: the assembly's own directory,
    # then installed packages' (an installable module's admin screens, a
    # packaged theme), then the kernel's. Called UNCONDITIONALLY — passing an
    # empty composition resets to kernel-only, so a second `create_app` in one
    # process cannot inherit a previous spec's override.
    compose_templates(
        assembly_dir=spec.assembly_template_dir,
        packaged_dirs=spec.packaged_template_dirs,
    )

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

    # Static mount — first match wins, most specific authority first: the
    # assembly's own dir (Task 3A static override), then any installed
    # presentation package's packaged static (U1), then the kernel's. A product
    # can therefore shadow one file from a shipped design system without
    # vendoring the rest of it. Absent in API-only mode (web_enabled=False),
    # exactly like the reference app.
    if web_enabled:
        static: StaticFiles
        layers = [
            str(directory)
            for directory in (
                spec.assembly_static_dir,
                *spec.packaged_static_dirs,
            )
            if directory is not None
        ]
        if layers:
            static = LayeredStaticFiles([*layers, str(static_dir())])
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
    # The platform ADMINISTRATION surface (step 6). Gated on `web_enabled` like
    # every other HTML surface — an API-only deployment serves no portal of
    # either plane — while the platform JSON API above stays mounted regardless.
    if web_enabled:
        app.include_router(platform_web_router)

    mount_features(
        app,
        manifests=enabled_manifests,
        disabled=disabled,
        web_enabled=web_enabled,
    )

    # AFTER mounting: every route that now exists must reference only declared
    # permission codes. Fails the boot, before the app is ever returned.
    _validate_referenced_permissions(app, permission_catalogue)
    _validate_referenced_capabilities(app, capability_catalogue)
    return app


__all__ = ["create_app", "LayeredStaticFiles"]
