"""Surface conformance kit for module authors (surface contract 1.2).

Boots a module's web surfaces twice, under a ``/admin`` facet prefix and a
``/`` facet prefix, and reports every way the module is not a well-behaved,
prefix-agnostic surface:

* **boots** under each prefix (no collision, unknown facet, UI contract or
  capability refusal);
* **identical relative routes** under both prefixes (no prefix was authored
  into a route path);
* **a guard on every route** - a ``require_permission`` stamp or an
  authentication-tier ``require_*`` dependency (hiding is never authorization);
* **CSRF on every mutation** (POST/PUT/PATCH/DELETE carry the kernel CSRF
  dependency);
* **no absolute paths in templates** (``href``/``action``/``src``/``hx-*``
  attributes must come from named routes; ``/static/`` is allowed);
* **named-route navigation** - every ``WebNavItem`` names a route its surface
  owns;
* **template namespace isolation** - every package template resolves only
  through ``<namespace>/``, never as an unqualified name, and references to
  unqualified templates do not resolve into the module's own files.

Usage in a module's tests::

    from dotmac_kernel.testing.surface_conformance import assert_surface_conformance

    def test_conforms() -> None:
        assert_surface_conformance(module)

The kit never reads a database or issues a request; it inspects the composed
app.  Checks that could pass over an empty set assert the set first.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from fastapi.routing import APIRoute

from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.app_factory import create_app
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.middleware.csrf import CSRF_PROTECTED_ATTR
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.route_metadata import PERMISSION_CODE_ATTR
from dotmac_kernel.surface_selection import SELECT_ALL
from dotmac_kernel.web_surfaces import (
    BrowserCapabilityProvision,
    NavigationRegion,
    TemplateRef,
    WebFacetMount,
    qualified_route_name,
)

DEFAULT_PREFIXES: Final[tuple[str, ...]] = ("/admin", "/")
AUTH_GUARD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "require_user_auth",
        "require_role",
        "require_permission",
        "require_web_auth",
        "require_platform_admin",
    }
)
_UNSAFE: Final[frozenset[str]] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ABSOLUTE_URL_ATTR = re.compile(
    r"""\b(?:href|action|src|hx-(?:get|post|put|patch|delete))\s*=\s*["']/(?!/|static/)""",
    re.IGNORECASE,
)
_TEMPLATE_REFERENCE = re.compile(
    r"""\{%-?\s*(?:extends|include|import|from)\s+["']([^"']+)["']"""
)


class SurfaceConformanceError(AssertionError):
    """The module's surfaces violate the author contract."""


@dataclass(frozen=True)
class ConformanceReport:
    """What was checked, so a green result is demonstrably not vacuous."""

    prefixes: tuple[str, ...]
    surfaces: tuple[str, ...]
    routes_checked: int = 0
    mutations_checked: int = 0
    templates_checked: int = 0
    nav_items_checked: int = 0
    violations: tuple[str, ...] = field(default_factory=tuple)


def _has_attribute(dependant: object, attribute: str) -> bool:
    if getattr(getattr(dependant, "call", None), attribute, False):
        return True
    return any(
        _has_attribute(child, attribute)
        for child in getattr(dependant, "dependencies", ())
    )


def _guarded(dependant: object) -> bool:
    call = getattr(dependant, "call", None)
    if call is not None and (
        getattr(call, "__name__", "") in AUTH_GUARD_NAMES
        or getattr(call, PERMISSION_CODE_ATTR, None)
    ):
        return True
    return any(_guarded(child) for child in getattr(dependant, "dependencies", ()))


def _build_spec(
    manifest: ModuleManifest,
    extra_modules: Sequence[ModuleManifest],
    prefix: str,
    capability_provisions: Sequence[BrowserCapabilityProvision] | None,
) -> ProductAssemblySpec:
    facets: dict[str, set[str]] = {}
    capabilities: dict[tuple[str, int], BrowserCapabilityProvision] = {}
    for surface in manifest.web_surfaces:
        regions = facets.setdefault(surface.facet, set())
        regions.update(item.region for item in surface.navigation)
        for requirement in surface.browser_capabilities:
            key = (requirement.code, requirement.contract_version)
            capabilities.setdefault(key, BrowserCapabilityProvision(*key))
    if capability_provisions is not None:
        capabilities = {(c.code, c.contract_version): c for c in capability_provisions}
    versions = {
        v
        for surface in manifest.web_surfaces
        for v in surface.supported_ui_contract_versions
    }
    return ProductAssemblySpec(
        name=f"surface-conformance:{manifest.code}",
        modules=(*extra_modules, manifest),
        api_documentation=api_documentation_policy("development"),
        platform_surface_enabled=False,
        ui_contract_version=max(versions) if versions else 1,
        web_facets=tuple(
            WebFacetMount(
                code=code,
                url_prefix=prefix,
                shell=TemplateRef("base.html"),
                navigation_regions=tuple(
                    NavigationRegion(region) for region in sorted(regions)
                ),
            )
            for code, regions in sorted(facets.items())
        ),
        browser_capabilities=tuple(capabilities.values()),
        surface_selection=SELECT_ALL,
    )


def _template_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in {".html", ".jinja", ".j2", ".txt"}
    )


def check_surface_conformance(
    manifest: ModuleManifest,
    *,
    prefixes: Sequence[str] = DEFAULT_PREFIXES,
    extra_modules: Iterable[ModuleManifest] = (),
    capability_provisions: Sequence[BrowserCapabilityProvision] | None = None,
    spec_for_prefix: Callable[[str], ProductAssemblySpec] | None = None,
) -> ConformanceReport:
    """Return the report; ``violations`` is empty when the module conforms.

    ``spec_for_prefix`` lets a caller supply the WHOLE assembly for each facet
    prefix (for example a composition shaped like a real product: its module
    set, auth profile and admission permission).  It must install ``manifest``
    and mount its facets at the given prefix; the kit then runs every check
    against that composition instead of its minimal one.
    """
    extras = tuple(extra_modules)
    violations: list[str] = []
    surfaces = tuple(surface.code for surface in manifest.web_surfaces)
    if not surfaces:
        return ConformanceReport(
            tuple(prefixes),
            (),
            violations=(f"{manifest.code}: declares no web_surfaces to check",),
        )
    routes_checked = mutations_checked = templates_checked = nav_checked = 0
    relative_by_prefix: dict[str, frozenset[tuple[str, str]]] = {}

    for prefix in prefixes:
        try:
            spec = (
                spec_for_prefix(prefix)
                if spec_for_prefix is not None
                else _build_spec(manifest, extras, prefix, capability_provisions)
            )
            app = create_app(spec)
        except Exception as exc:
            violations.append(f"[{prefix}] boot refused: {type(exc).__name__}: {exc}")
            continue
        facet_prefix = prefix.rstrip("/")
        mounted: set[tuple[str, str]] = set()
        for surface in manifest.web_surfaces:
            marker = f"web:{surface.facet}:{manifest.code}:{surface.code}:"
            owned = [
                route
                for route in app.routes
                if isinstance(route, APIRoute) and route.name.startswith(marker)
            ]
            if not owned:
                violations.append(
                    f"[{prefix}] surface {surface.code!r} mounted no routes"
                )
                continue
            local_names = {route.name[len(marker) :] for route in owned}
            for route in owned:
                routes_checked += 1
                label = f"[{prefix}] {sorted(route.methods or set())} {route.path}"
                relative = route.path[len(facet_prefix) :] or "/"
                if facet_prefix and not route.path.startswith(facet_prefix):
                    violations.append(f"{label}: not under the facet prefix")
                if facet_prefix and relative.startswith(facet_prefix + "/"):
                    violations.append(f"{label}: authors the facet prefix in its path")
                for method in route.methods or set():
                    mounted.add((method, relative))
                if not _guarded(route.dependant):
                    violations.append(f"{label}: no permission or authentication guard")
                if (route.methods or set()) & _UNSAFE:
                    mutations_checked += 1
                    if not _has_attribute(route.dependant, CSRF_PROTECTED_ATTR):
                        violations.append(f"{label}: mutation without CSRF protection")
            for item in surface.navigation:
                nav_checked += 1
                if item.route_name not in local_names:
                    violations.append(
                        f"[{prefix}] nav item {item.code!r} names route "
                        f"{item.route_name!r} the surface does not own"
                    )
            if (
                qualified_route_name(surface.facet, manifest.code, surface.code, "x")
                == marker + "x"
                and not local_names
            ):
                violations.append(
                    f"[{prefix}] surface {surface.code!r} has no named routes"
                )
        relative_by_prefix[prefix] = frozenset(mounted)

    distinct = set(relative_by_prefix.values())
    if len(distinct) > 1:
        violations.append(
            "relative route set differs between prefixes "
            f"({', '.join(sorted(relative_by_prefix))}): a prefix was authored "
            "into a route"
        )

    # Templates: one pass per package (prefix independent).
    from dotmac_kernel.templating import templates as _templates

    seen_roots: set[Path] = set()
    for surface in manifest.web_surfaces:
        package = surface.templates
        # The loader is process-wide and only meaningful after a boot succeeded.
        if not relative_by_prefix or package is None or package.root in seen_roots:
            continue
        seen_roots.add(package.root)
        files = _template_files(package.root)
        if not files:
            violations.append(
                f"template package {package.namespace!r} has no templates"
            )
            continue
        env = _templates.env
        for path in files:
            templates_checked += 1
            relative = path.relative_to(package.root).as_posix()
            text = path.read_text(encoding="utf-8")
            if _ABSOLUTE_URL_ATTR.search(text):
                violations.append(
                    f"template {package.namespace}/{relative}: hard-coded absolute "
                    "URL (use a named route)"
                )
            if path.suffix == ".html" and relative.endswith(".html"):
                try:
                    qualified_source = env.loader.get_source(  # type: ignore[union-attr]
                        env, f"{package.namespace}/{relative}"
                    )[1]
                except Exception as exc:
                    if prefixes and relative_by_prefix:
                        violations.append(
                            f"template {package.namespace}/{relative}: not "
                            f"reachable through its namespace ({type(exc).__name__})"
                        )
                    qualified_source = None
                if (
                    qualified_source is not None
                    and Path(qualified_source).resolve() != path.resolve()
                ):
                    violations.append(
                        f"template {package.namespace}/{relative}: resolves to "
                        f"{qualified_source}, not the package file"
                    )
                try:
                    bare = env.loader.get_source(env, relative)[1]  # type: ignore[union-attr]
                except Exception:
                    bare = None
                if bare is not None and Path(bare).resolve() == path.resolve():
                    violations.append(
                        f"template {relative}: also reachable un-namespaced"
                    )
            for reference in _TEMPLATE_REFERENCE.findall(text):
                if reference.startswith(f"{package.namespace}/"):
                    continue
                try:
                    resolved = env.loader.get_source(env, reference)[1]  # type: ignore[union-attr]
                except Exception:
                    violations.append(
                        f"template {package.namespace}/{relative}: references "
                        f"{reference!r}, which does not resolve"
                    )
                    continue
                if resolved and Path(resolved).resolve().is_relative_to(
                    package.root.resolve()
                ):
                    violations.append(
                        f"template {package.namespace}/{relative}: references its "
                        f"own file {reference!r} without the namespace"
                    )

    if not violations:
        # A green report must not be vacuous.
        for label, count in (
            ("routes", routes_checked),
            ("templates", templates_checked),
        ):
            if count == 0:
                violations.append(f"vacuous: no {label} were checked")
    return ConformanceReport(
        prefixes=tuple(prefixes),
        surfaces=surfaces,
        routes_checked=routes_checked,
        mutations_checked=mutations_checked,
        templates_checked=templates_checked,
        nav_items_checked=nav_checked,
        violations=tuple(violations),
    )


def assert_surface_conformance(
    manifest: ModuleManifest,
    *,
    prefixes: Sequence[str] = DEFAULT_PREFIXES,
    extra_modules: Iterable[ModuleManifest] = (),
    capability_provisions: Sequence[BrowserCapabilityProvision] | None = None,
    spec_for_prefix: Callable[[str], ProductAssemblySpec] | None = None,
) -> ConformanceReport:
    """Raise :class:`SurfaceConformanceError` listing every violation."""
    report = check_surface_conformance(
        manifest,
        prefixes=prefixes,
        extra_modules=extra_modules,
        capability_provisions=capability_provisions,
        spec_for_prefix=spec_for_prefix,
    )
    if report.violations:
        raise SurfaceConformanceError(
            f"module {manifest.code!r} violates the surface contract:\n  "
            + "\n  ".join(report.violations)
        )
    return report


__all__ = [
    "AUTH_GUARD_NAMES",
    "DEFAULT_PREFIXES",
    "ConformanceReport",
    "SurfaceConformanceError",
    "assert_surface_conformance",
    "check_surface_conformance",
]
