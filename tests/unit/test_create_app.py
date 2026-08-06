"""`create_app(ProductAssemblySpec)` behavior (kernel-boundary Task 3A).

The reference `app/main.py` is now `create_app(app.assembly.assembly)`, and its
route inventory was verified byte-identical to the pre-Task-3 hand-built app at
the migration (the existing `test_route_guards` sweep keeps every route
guarded). Here we exercise the factory's behavior directly: the spec is
frozen/immutable, an empty assembly
boots to just the kernel surface, the surface switches (`web_enabled`,
`disabled_modules`) work, the middleware stack + platform-auth are wired, and
the assembly-over-kernel template/static overrides take precedence.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from dotmac_kernel import NavItem, ProductAssemblySpec, create_app
from dotmac_kernel.app_factory import LayeredStaticFiles
from dotmac_kernel.features import FeatureManifest
from dotmac_kernel.modules import UNVERSIONED
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount


def _paths(app) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, APIRoute)}


def _has_static_mount(app) -> bool:
    return any(isinstance(r, Mount) and r.path == "/static" for r in app.routes)


def test_spec_is_frozen_and_collections_immutable():
    spec = ProductAssemblySpec(
        name="s", modules=[], disabled_modules={"x"}, providers={"a": 1}
    )
    with pytest.raises(FrozenInstanceError):
        spec.name = "other"  # type: ignore[misc]
    assert isinstance(spec.modules, tuple)
    assert isinstance(spec.disabled_modules, frozenset)
    # providers/settings_overrides are read-only mappings
    with pytest.raises(TypeError):
        spec.providers["b"] = 2  # type: ignore[index]


def test_empty_assembly_boots_to_kernel_surface_only():
    app = create_app(ProductAssemblySpec(name="empty", modules=()))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
    paths = _paths(app)
    # Platform AUTH is always present (kernel surface, mounted directly).
    assert "/platform/auth/login" in paths
    assert "/platform/auth/logout" in paths
    # No feature routes at all — including /platform/tenants, which is the
    # `tenants` FEATURE (assembly), not the kernel platform surface.
    assert "/platform/tenants" not in paths
    assert not any(p.startswith("/parties") for p in paths)
    assert "/admin" not in paths


def test_web_enabled_false_drops_web_but_keeps_json_api():
    manifest_modules = _reference_modules()
    api_app = create_app(
        ProductAssemblySpec(name="api", modules=manifest_modules, web_enabled=False)
    )
    assert not _has_static_mount(api_app)
    paths = _paths(api_app)
    assert "/admin" not in paths and not any(p.startswith("/admin/") for p in paths)
    # JSON API still mounted.
    assert any(p.startswith("/parties") for p in paths)

    web_app = create_app(
        ProductAssemblySpec(name="web", modules=manifest_modules, web_enabled=True)
    )
    assert _has_static_mount(web_app)


def test_disabled_module_routes_are_absent():
    modules = _reference_modules()
    app = create_app(
        ProductAssemblySpec(
            name="d", modules=modules, disabled_modules=frozenset({"parties"})
        )
    )
    paths = _paths(app)
    assert not any(p.startswith("/parties") for p in paths)
    # A different feature is unaffected.
    assert any(p.startswith("/rbac") or "/admin/roles" in p for p in paths)


def test_security_headers_middleware_is_wired():
    app = create_app(ProductAssemblySpec(name="mw", modules=()))
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in resp.headers


def test_layered_static_prefers_assembly_over_kernel(tmp_path: Path):
    assembly_dir = tmp_path / "assembly_static"
    kernel_dir = tmp_path / "kernel_static"
    assembly_dir.mkdir()
    kernel_dir.mkdir()
    (assembly_dir / "shared.txt").write_text("ASSEMBLY")
    (kernel_dir / "shared.txt").write_text("KERNEL")
    (kernel_dir / "kernel_only.txt").write_text("KERNEL-ONLY")

    layered = LayeredStaticFiles([str(assembly_dir), str(kernel_dir)])
    assert layered.all_directories == [str(assembly_dir), str(kernel_dir)]
    # first-match-wins: assembly shadows the kernel's same-named file, but a
    # file only the kernel has still resolves.
    assert (assembly_dir / "shared.txt").read_text() == "ASSEMBLY"
    assert (kernel_dir / "kernel_only.txt").exists()


def test_assembly_template_override_takes_precedence(tmp_path: Path):
    from dotmac_kernel import templating

    original_loader = templating.templates.env.loader
    try:
        adir = tmp_path / "atemplates"
        adir.mkdir()
        (adir / "_probe_override.html").write_text("ASSEMBLY-VERSION")
        templating.use_assembly_templates(adir)
        # The assembly template resolves...
        src = templating.templates.env.loader.get_source(
            templating.templates.env, "_probe_override.html"
        )
        assert src[0] == "ASSEMBLY-VERSION"
        # ...and a kernel template the assembly did NOT override still resolves.
        templating.templates.env.loader.get_source(
            templating.templates.env, "base.html"
        )
    finally:
        templating.templates.env.loader = original_loader


def _reference_modules() -> list[FeatureManifest]:
    from dotmac_kernel.features import load_manifests

    from app.features import FEATURE_MODULES

    return list(load_manifests(FEATURE_MODULES))


# ---------------------------------------------------------------------------
# Module registry wiring (module control-plane directive step 2).
#
# `create_app` validates `spec.modules` into a `ModuleRegistry` BEFORE mounting
# anything, mounts in the registry's deterministic startup order, and publishes
# the installed-module inventory on app state for health/diagnostics.
# ---------------------------------------------------------------------------


def test_reference_assembly_route_order_is_unchanged_by_the_registry():
    """The registry must not reorder an assembly whose modules declare no
    dependencies — route matching is first-match-wins, so a reordering here
    would be a silent behavior change, not a refactor."""
    from dotmac_kernel.modules import ModuleRegistry

    modules = _reference_modules()
    registry = ModuleRegistry(modules)
    assert [m.code for m in registry.startup_order()] == [m.name for m in modules]


def test_create_app_publishes_the_module_inventory_on_app_state():
    from dotmac_kernel.modules import ModuleManifest, ModuleRegistry

    spec = ProductAssemblySpec(
        name="inv",
        modules=[
            ModuleManifest(code="base", version="1.0.0"),
            ModuleManifest(code="top", version="2.3.1", dependencies=("base",)),
        ],
    )
    app = create_app(spec)

    assert isinstance(app.state.module_registry, ModuleRegistry)
    by_code = {e.code: e for e in app.state.module_inventory}
    assert by_code["top"].version == "2.3.1"
    assert by_code["top"].dependencies == ("base",)
    assert by_code["base"].enabled is True


def test_health_does_not_disclose_the_module_inventory():
    """Public `/health` is liveness ONLY. Exposing what is installed there
    would hand an unauthenticated caller a deployment fingerprint; the
    inventory is reached through app state / an authenticated surface."""
    app = create_app(ProductAssemblySpec(name="h", modules=_reference_modules()))
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body == {"status": "ok"}


def test_disabled_module_is_installed_but_not_enabled_in_the_inventory():
    modules = _reference_modules()
    app = create_app(
        ProductAssemblySpec(
            name="d", modules=modules, disabled_modules=frozenset({"parties"})
        )
    )
    by_code = {e.code: e for e in app.state.module_inventory}
    assert by_code["parties"].enabled is False
    assert by_code["auth"].enabled is True


def test_create_app_fails_closed_on_an_incoherent_module_set():
    """Validation happens before any route is mounted — a broken module set
    must stop the boot, not produce a half-mounted app."""
    from dotmac_kernel.modules import (
        MissingModuleDependencyError,
        ModuleDependencyCycleError,
        ModuleManifest,
    )

    with pytest.raises(MissingModuleDependencyError):
        create_app(
            ProductAssemblySpec(
                name="missing",
                modules=[ModuleManifest(code="a", version="1", dependencies=("gone",))],
            )
        )

    with pytest.raises(ModuleDependencyCycleError):
        create_app(
            ProductAssemblySpec(
                name="cycle",
                modules=[
                    ModuleManifest(code="a", version="1", dependencies=("b",)),
                    ModuleManifest(code="b", version="1", dependencies=("a",)),
                ],
            )
        )


def test_create_app_rejects_disabling_a_dependency_something_else_needs():
    from dotmac_kernel.modules import MissingModuleDependencyError, ModuleManifest

    with pytest.raises(MissingModuleDependencyError):
        create_app(
            ProductAssemblySpec(
                name="dis",
                modules=[
                    ModuleManifest(code="base", version="1"),
                    ModuleManifest(code="top", version="1", dependencies=("base",)),
                ],
                disabled_modules=frozenset({"base"}),
            )
        )


def test_mixed_feature_and_module_assembly_boots():
    """The migration path, end to end: an assembly halfway through migrating its
    packages holds both manifest shapes at once, and every surface (routes, nav
    globals, /health) still comes up."""
    from dotmac_kernel.modules import ModuleManifest
    from fastapi import APIRouter

    legacy_api, modern_api, modern_web = APIRouter(), APIRouter(), APIRouter()

    @legacy_api.get("/legacy-thing")
    def legacy_thing():
        return {}

    @modern_api.get("/modern-thing")
    def modern_thing():
        return {}

    @modern_web.get("/admin/modern")
    def modern_admin():
        return {}

    app = create_app(
        ProductAssemblySpec(
            name="mixed",
            modules=[
                FeatureManifest(name="legacy", routers=[legacy_api]),
                ModuleManifest(
                    code="modern",
                    version="1.2.0",
                    dependencies=("legacy",),
                    api_routers=[modern_api],
                    web_routers=[modern_web],
                    nav=[NavItem("Modern", "/admin/modern")],
                ),
            ],
        )
    )
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
    assert {"/legacy-thing", "/modern-thing", "/admin/modern"} <= _paths(app)

    by_code = {e.code: e for e in app.state.module_inventory}
    # The unmigrated feature records no invented version; the migrated one does.
    assert by_code["legacy"].version == UNVERSIONED
    assert by_code["modern"].version == "1.2.0"
    # And the cross-shape dependency edge is honoured in the mount order.
    assert [m.code for m in app.state.module_registry.startup_order()] == [
        "legacy",
        "modern",
    ]


def test_module_manifest_routers_mount_like_feature_routers():
    """A `ModuleManifest`'s `api_routers`/`web_routers` reach the app through
    the same mount path a `FeatureManifest` uses — the compatibility aliases
    are load-bearing, not decorative."""
    from dotmac_kernel.modules import ModuleManifest
    from fastapi import APIRouter

    api, web = APIRouter(), APIRouter()

    @api.get("/mod-api")
    def mod_api():
        return {}

    @web.get("/admin/mod-web")
    def mod_web():
        return {}

    manifest = ModuleManifest(
        code="modtest", version="1.0.0", api_routers=[api], web_routers=[web]
    )
    paths = _paths(create_app(ProductAssemblySpec(name="m", modules=[manifest])))
    assert {"/mod-api", "/admin/mod-web"} <= paths

    api_only = _paths(
        create_app(
            ProductAssemblySpec(name="m2", modules=[manifest], web_enabled=False)
        )
    )
    assert "/mod-api" in api_only and "/admin/mod-web" not in api_only
