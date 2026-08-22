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
import inspect
import logging
from collections.abc import Mapping, Sequence, Set
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from dotmac_kernel.assembly import ProductAssemblySpec, StartupHook
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
from dotmac_kernel.setting_domains import (
    SettingDomainRegistry,
    install_setting_domains,
)
from dotmac_kernel.setting_scopes import ScopeKindRegistry, install_scope_kinds
from dotmac_kernel.setting_value_types import (
    SettingValueTypeRegistry,
    install_setting_value_types,
)
from dotmac_kernel.settings_models import SettingDomain
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


def _tenancy_errors() -> list[str]:
    """Enforce `TENANCY=single`: exactly one tenant row, and bind to it.

    Imported inside the function for the same reason `_required_setting_errors`
    does it — importing `dotmac_kernel.db` builds the engine from DATABASE_URL,
    and `create_app` must stay importable without a database.

    An unreachable database returns no errors rather than a false one: it says
    nothing about how many tenants exist, and `validate_settings` already covers
    a missing DATABASE_URL.
    """
    from dotmac_kernel.config import settings as _settings

    if _settings.tenancy != "single":
        return []

    from dotmac_kernel.db import resolver_session
    from dotmac_kernel.models import Tenant
    from dotmac_kernel.tenancy import bind_single_tenant

    try:
        with resolver_session() as db:
            slugs = [t.slug for t in db.query(Tenant).order_by(Tenant.slug).all()]
    except Exception as exc:  # unreachable store: not a tenancy verdict
        logger.warning("Tenancy check skipped: %s", exc)
        return []

    if len(slugs) == 1:
        bind_single_tenant(slugs[0])
        return []
    if not slugs:
        return [
            "TENANCY=single but no tenant exists; the deployment has nothing to serve"
        ]
    return [
        "TENANCY=single but this database holds "
        f"{len(slugs)} tenants ({', '.join(slugs)}); "
        "a single-tenant deployment must not carry another tenant's rows"
    ]


def _required_setting_errors() -> list[str]:
    """Required-setting failures, or an empty list when the store is unreachable.

    Imports `dotmac_kernel.db` HERE, not at module scope: importing it builds
    the SQLAlchemy engine from `DATABASE_URL`, and `create_app` must stay
    importable without a database (the same reason those APIs are submodule-only
    in the public surface).

    A store this cannot reach yields NO errors rather than a false one. Reading
    an unreachable database says nothing about whether a setting is configured,
    and reporting "not configured" for a connection failure would be fatal in
    production for the wrong reason — `validate_settings` already covers a
    missing `DATABASE_URL` itself, and `/health` stays DB-free by design.
    """
    from dotmac_kernel.db import platform_session
    from dotmac_kernel.settings_resolver import (
        seed_settings_from_env,
        validate_required_settings,
    )

    try:
        with platform_session() as db:
            # Bootstrap first: a setting configured by environment variable is
            # turned into a real row here, so the check below sees it as
            # configured — and so it behaves like every other value from then
            # on. Env is never consulted at read time.
            seed_settings_from_env(db)
            db.commit()
            return validate_required_settings(db)
    except Exception as exc:  # unreachable store: see docstring
        logger.warning("Required-setting validation skipped: %s", exc)
        return []


def _effective_route_contexts(app: FastAPI):
    """Yield route-like objects with their effective dependency trees.

    FastAPI through 0.115 materializes included ``APIRoute`` instances in
    ``app.routes``. FastAPI 0.140 stores an included router lazily and exposes
    its flattened, prefix-aware routes through ``effective_route_contexts``.
    Duck typing keeps the kernel independent of FastAPI's private wrapper class
    while supporting both representations across the declared version range.
    """
    for route in app.routes:
        contexts = getattr(route, "effective_route_contexts", None)
        if callable(contexts):
            yield from contexts()
        else:
            yield route


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
    for route in _effective_route_contexts(app):
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


def _product_startup_errors(spec: ProductAssemblySpec) -> list[str]:
    errors: list[str] = []
    for check in spec.startup_checks:
        errors.extend(check())
    return errors


async def _run_product_startup_hooks(hooks: Sequence[StartupHook]) -> None:
    for hook in hooks:
        result = hook()
        if inspect.isawaitable(result):
            await result


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


def _install_profile_defaults(defaults: Mapping[str, object]) -> None:
    """Validate and install the assembly's declared setting defaults.

    Fails the boot rather than degrading. A deployment that declares a default
    is stating intent, and silently ignoring an unusable one would leave the
    settings screen showing a value nothing resolves to.
    """
    from dotmac_kernel.settings_resolver import (
        _check_against_spec,
        get_spec,
        install_setting_defaults,
    )

    for composite, value in defaults.items():
        domain, _, key = composite.partition("/")
        if not domain or not key:
            raise ValueError(
                f"setting default {composite!r} must be keyed '<domain>/<key>'"
            )
        try:
            spec = get_spec(SettingDomain(domain), key)
        except KeyError:
            raise ValueError(
                f"the deployment declares a default for {composite!r}, which no "
                "installed module declares as a setting. A profile supplies "
                "ANSWERS; it cannot introduce a question."
            ) from None
        if value is not None:
            _, error = _check_against_spec(spec, value)
            if error is not None:
                raise ValueError(
                    f"the deployment's default for {composite!r} is rejected by "
                    f"its own spec ({error})"
                )
    install_setting_defaults(defaults)


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
    # Setting domains. Same installed-not-enabled rule: a stored setting row
    # references a domain and outlives any deployment's enabled set.
    install_setting_domains(SettingDomainRegistry.from_manifests(manifests))
    # Setting value types: the kernel's built-ins plus any a module declares.
    install_setting_value_types(SettingValueTypeRegistry.from_manifests(manifests))
    # Scope kinds: the kernel's platform/tenant plus any level a module adds.
    install_scope_kinds(ScopeKindRegistry.from_manifests(manifests))
    # The deployment's declared defaults. Validated against the specs BEFORE
    # installation: a default for a key nothing declares is how settings with
    # no reader appear, and a default its own spec rejects would resolve to the
    # module fallback anyway while looking configured on the settings screen.
    _install_profile_defaults(spec.setting_defaults)

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
        for err in _product_startup_errors(spec):
            if settings.is_production:
                raise RuntimeError(f"Product configuration error: {err}")
            logger.warning("Product config: %s", err)
        await _run_product_startup_hooks(spec.startup_hooks)
        if settings.seed_on_startup:
            await _run_enabled_seeds(enabled_manifests, disabled)
        for err in await asyncio.to_thread(_required_setting_errors):
            if settings.is_production:
                raise RuntimeError(f"Configuration error: {err}")
            logger.warning("Config: %s", err)
        # After the seeds, so a deployment that seeds its first tenant on boot
        # is counted once that has happened rather than before it.
        for err in await asyncio.to_thread(_tenancy_errors):
            if settings.is_production:
                raise RuntimeError(f"Tenancy error: {err}")
            logger.warning("Tenancy: %s", err)
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
        content_security_policy=(
            settings.content_security_policy
            or spec.security_policy.content_security_policy
        ),
        cross_origin_opener_policy=(spec.security_policy.cross_origin_opener_policy),
        cross_origin_resource_policy=(
            spec.security_policy.cross_origin_resource_policy
        ),
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
    if spec.platform_surface_enabled:
        app.include_router(platform_auth_router)
    # The platform ADMINISTRATION surface (step 6). Gated on `web_enabled` like
    # every other HTML surface — an API-only deployment serves no portal of
    # either plane — while the platform JSON API above stays mounted regardless.
    if spec.platform_surface_enabled and web_enabled:
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


__all__ = ["LayeredStaticFiles", "create_app"]
