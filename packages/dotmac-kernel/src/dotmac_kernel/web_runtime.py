"""Internal runtime for the typed browser-surface contract.

Declarations live in :mod:`dotmac_kernel.web_surfaces`; this module is the
single adapter that turns them into FastAPI routes and request-scoped template
state.  It deliberately does not own authentication or authorization policy:
the assembly supplies authentication providers, manifests declare permissions
and capabilities, and the existing kernel decision seams evaluate both.
"""

from __future__ import annotations

from copy import copy
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from dotmac_kernel.deps import authorize_party, get_db, require_tenant
from dotmac_kernel.entitlements import is_entitled
from dotmac_kernel.middleware.csrf import require_csrf
from dotmac_kernel.models import Party
from dotmac_kernel.web_surfaces import (
    AuthenticationProfileBinding,
    BrowserSecurityPlane,
    RegisteredWebSurface,
    ResolvedWebNavItem,
    SurfaceContext,
    WebFacetMount,
    WebSurfaceRegistry,
    qualified_route_name,
)


def _resolved_navigation(
    request: Request,
    *,
    registry: WebSurfaceRegistry,
    facet: WebFacetMount,
    party: Party | None = None,
    db: Session | None = None,
) -> tuple[ResolvedWebNavItem, ...]:
    tenant = getattr(request.state, "tenant", None)
    result: list[ResolvedWebNavItem] = []
    for surface in registry.surfaces:
        if surface.contribution.facet != facet.code:
            continue
        for item in surface.navigation:
            if item.required_permissions:
                if party is None or db is None or tenant is None:
                    continue
                if not all(
                    authorize_party(db, tenant=tenant, party=party, code=code)
                    for code in item.required_permissions
                ):
                    continue
            if item.required_capabilities:
                if db is None or tenant is None:
                    continue
                if not all(
                    is_entitled(db, tenant_id=tenant.id, capability_code=code).allowed
                    for code in item.required_capabilities
                ):
                    continue
            href = item.legacy_path or str(request.url_for(item.route_name))
            result.append(
                ResolvedWebNavItem(
                    code=item.code,
                    region=item.region,
                    label=item.label.default,
                    href=href,
                    group=item.group,
                    order=item.order,
                    feature=surface.owner,
                )
            )
    return tuple(
        sorted(result, key=lambda value: (value.region, value.order, value.code))
    )


def _context(
    request: Request,
    *,
    registry: WebSurfaceRegistry,
    facet: WebFacetMount,
    surface: RegisteredWebSurface,
    enabled_modules: frozenset[str],
    stylesheets: tuple[str, ...],
    party: Party | None = None,
    db: Session | None = None,
) -> SurfaceContext:
    value = SurfaceContext(
        facet=facet.code,
        # Jinja template declaration, not subprocess shell execution.
        shell=facet.shell.qualified_name,  # nosec B604
        owner=surface.owner,
        surface=surface.contribution.code,
        enabled_modules=enabled_modules,
        navigation=_resolved_navigation(
            request, registry=registry, facet=facet, party=party, db=db
        ),
        stylesheets=stylesheets,
        ui_contract_version=registry.ui_contract_version,
        login_path=registry.route_path(request, facet.code, facet.login_route),
        landing_path=registry.route_path(request, facet.code, facet.landing_route),
        logout_path=registry.route_path(request, facet.code, facet.logout_route),
        url_prefix=facet.url_prefix,
    )
    request.state.surface_context = value
    return value


def _entry_context_dependency(
    *,
    registry: WebSurfaceRegistry,
    facet: WebFacetMount,
    surface: RegisteredWebSurface,
    enabled_modules: frozenset[str],
    stylesheets: tuple[str, ...],
):
    def dependency(request: Request) -> SurfaceContext:
        return _context(
            request,
            registry=registry,
            facet=facet,
            surface=surface,
            enabled_modules=enabled_modules,
            stylesheets=stylesheets,
        )

    return dependency


def _tenant_context_dependency(
    *,
    registry: WebSurfaceRegistry,
    facet: WebFacetMount,
    surface: RegisteredWebSurface,
    profile: AuthenticationProfileBinding,
    enabled_modules: frozenset[str],
    stylesheets: tuple[str, ...],
):
    provider = profile.provider
    if provider is None:
        raise TypeError(f"tenant authentication profile {profile.code!r} is public")
    authentication = provider.dependency

    def dependency(
        request: Request,
        principal: Any = Depends(authentication),
        db: Session = Depends(get_db),
    ) -> SurfaceContext:
        if not isinstance(principal, Party):
            raise TypeError(
                f"tenant authentication profile {profile.code!r} did not return Party"
            )
        tenant = require_tenant(request)
        if facet.admission_permission and not authorize_party(
            db,
            tenant=tenant,
            party=principal,
            code=facet.admission_permission,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden"
            )
        return _context(
            request,
            registry=registry,
            facet=facet,
            surface=surface,
            enabled_modules=enabled_modules,
            stylesheets=stylesheets,
            party=principal,
            db=db,
        )

    return dependency


def _non_tenant_context_dependency(
    *,
    registry: WebSurfaceRegistry,
    facet: WebFacetMount,
    surface: RegisteredWebSurface,
    profile: AuthenticationProfileBinding | None,
    enabled_modules: frozenset[str],
    stylesheets: tuple[str, ...],
):
    authentication = (
        profile.provider.dependency
        if profile is not None and profile.provider is not None
        else None
    )

    if authentication is None:
        return _entry_context_dependency(
            registry=registry,
            facet=facet,
            surface=surface,
            enabled_modules=enabled_modules,
            stylesheets=stylesheets,
        )

    def dependency(
        request: Request, _principal: Any = Depends(authentication)
    ) -> SurfaceContext:
        return _context(
            request,
            registry=registry,
            facet=facet,
            surface=surface,
            enabled_modules=enabled_modules,
            stylesheets=stylesheets,
        )

    return dependency


def _route_context_dependency(
    *,
    registry: WebSurfaceRegistry,
    facet: WebFacetMount,
    surface: RegisteredWebSurface,
    route: APIRoute,
    enabled_modules: frozenset[str],
    stylesheets: tuple[str, ...],
):
    if registry.is_entry_route(
        facet=facet.code,
        owner=surface.owner,
        surface=surface.contribution.code,
        route_name=route.name,
    ):
        return _entry_context_dependency(
            registry=registry,
            facet=facet,
            surface=surface,
            enabled_modules=enabled_modules,
            stylesheets=stylesheets,
        )

    profile = (
        registry.authentication_profile(facet.authentication_profile)
        if facet.authentication_profile is not None
        else None
    )
    if profile is not None and profile.security_plane is BrowserSecurityPlane.TENANT:
        return _tenant_context_dependency(
            registry=registry,
            facet=facet,
            surface=surface,
            profile=profile,
            enabled_modules=enabled_modules,
            stylesheets=stylesheets,
        )
    return _non_tenant_context_dependency(
        registry=registry,
        facet=facet,
        surface=surface,
        profile=profile,
        enabled_modules=enabled_modules,
        stylesheets=stylesheets,
    )


def mount_web_surfaces(
    app: FastAPI,
    *,
    registry: WebSurfaceRegistry,
    enabled_modules: frozenset[str],
    stylesheets: tuple[str, ...] = (),
) -> None:
    """Mount the validated graph with one qualified name per browser route."""

    app.state.web_surface_registry = registry
    app.state.default_surface_context = SurfaceContext(
        facet="",
        shell="",
        owner="",
        surface="",
        enabled_modules=enabled_modules,
        navigation=(),
        stylesheets=stylesheets,
        ui_contract_version=registry.ui_contract_version,
        login_path=None,
        landing_path=None,
        logout_path=None,
        url_prefix="",
    )
    for surface in registry.surfaces:
        facet = registry.facet(surface.contribution.facet)
        for source_router in surface.contribution.routers:
            for source_route in source_router.routes:
                if not isinstance(source_route, APIRoute):
                    continue
                route = copy(source_route)
                local_name = route.name
                route.name = qualified_route_name(
                    facet.code, surface.owner, surface.contribution.code, local_name
                )
                one = APIRouter()
                one.routes.append(route)
                dependencies = [Depends(require_csrf)]
                dependencies.append(
                    Depends(
                        _route_context_dependency(
                            registry=registry,
                            facet=facet,
                            surface=surface,
                            route=source_route,
                            enabled_modules=enabled_modules,
                            stylesheets=stylesheets,
                        )
                    )
                )
                app.include_router(
                    one,
                    prefix="" if surface.legacy else facet.url_prefix,
                    dependencies=dependencies,
                )


__all__ = ["mount_web_surfaces"]
