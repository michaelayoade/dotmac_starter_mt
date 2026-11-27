"""The conformance kit must bite: each planted defect is reported."""

from __future__ import annotations

from pathlib import Path

import pytest
from dotmac_kernel import (
    LocalizedText,
    ModuleManifest,
    PermissionSpec,
    TemplatePackage,
    WebNavItem,
    WebRouteRef,
    WebSurfaceContribution,
)
from dotmac_kernel.deps import require_permission
from dotmac_kernel.route_metadata import PERMISSION_CODE_ATTR
from dotmac_kernel.testing.surface_conformance import (
    SurfaceConformanceError,
    assert_surface_conformance,
    check_surface_conformance,
)
from fastapi import APIRouter, Depends

_PERM = PermissionSpec(code="plant.read", description="Read the planted surface")


def _module(
    tmp_path: Path,
    *,
    guarded: bool = True,
    html: str = "<p>ok</p>",
    nav_route: str = "index",
    post: bool = False,
    spoof: bool = False,
    api: str | None = None,
) -> ModuleManifest:
    root = tmp_path / "tpl"
    root.mkdir(exist_ok=True)
    (root / "index.html").write_text(html)
    router = APIRouter()
    deps = [Depends(require_permission("plant.read"))] if guarded else []
    if spoof:

        def require_permission_lookalike() -> None:
            return None

        require_permission_lookalike.__name__ = "require_permission"
        setattr(require_permission_lookalike, PERMISSION_CODE_ATTR, "plant.read")
        deps = [Depends(require_permission_lookalike)]

    @router.get("/plant", name="index", dependencies=deps)
    def index() -> dict[str, bool]:
        return {"ok": True}

    if post:

        @router.post("/plant/save", name="save", dependencies=deps)
        def save() -> dict[str, bool]:
            return {"ok": True}

    api_routers: list[APIRouter] = []
    if api is not None:
        api_router = APIRouter()
        api_deps = (
            [Depends(require_permission("plant.read"))] if api == "guarded" else []
        )

        @api_router.get("/plant-api", name="plant_api", dependencies=api_deps)
        def plant_api() -> dict[str, bool]:
            return {"ok": True}

        api_routers.append(api_router)
    return ModuleManifest(
        code="plant",
        version="1.0.0",
        api_routers=tuple(api_routers),
        permissions=(_PERM,),
        web_surfaces=(
            WebSurfaceContribution(
                code="staff",
                facet="staff_admin",
                routers=(router,),
                navigation=(
                    WebNavItem(
                        code="plant.nav",
                        region="primary",
                        label=LocalizedText("plant.nav", "Plant"),
                        route_name=nav_route,
                    ),
                ),
                templates=TemplatePackage(namespace="plant", root=root),
                supported_ui_contract_versions=frozenset({1}),
            ),
        ),
    )


def test_a_conforming_module_passes_under_both_prefixes(tmp_path: Path) -> None:
    report = assert_surface_conformance(_module(tmp_path, post=True))
    assert report.routes_checked == 4  # 2 routes x 2 prefixes
    assert report.mutations_checked == 2
    assert report.nav_items_checked == 2
    assert report.templates_checked == 1


def test_unguarded_route_is_reported(tmp_path: Path) -> None:
    report = check_surface_conformance(_module(tmp_path, guarded=False))
    assert any("no kernel permission guard" in v for v in report.violations)


def test_absolute_url_in_a_template_is_reported(tmp_path: Path) -> None:
    html = '<a href="/admin/plant">x</a><form action="/plant/save"></form>'
    report = check_surface_conformance(_module(tmp_path, html=html))
    assert any("hard-coded absolute URL" in v for v in report.violations)


def test_static_assets_and_named_routes_are_allowed(tmp_path: Path) -> None:
    html = '<link href="/static/x.css"><a href="{{ url_for(\'x\') }}">x</a>'
    assert_surface_conformance(_module(tmp_path, html=html))


def test_nav_naming_a_route_the_surface_does_not_own_is_reported(
    tmp_path: Path,
) -> None:
    # The kernel registry may refuse first; either way it is a finding.
    report = check_surface_conformance(_module(tmp_path, nav_route="ghost"))
    assert report.violations


def test_unqualified_reference_to_own_template_is_reported(tmp_path: Path) -> None:
    root = tmp_path / "tpl"
    root.mkdir()
    (root / "partial.html").write_text("<p>p</p>")
    html = '{% include "partial.html" %}'
    report = check_surface_conformance(_module(tmp_path, html=html))
    assert any(
        "does not resolve" in v or "without the namespace" in v
        for v in report.violations
    )


def test_module_without_surfaces_is_not_vacuously_green() -> None:
    with pytest.raises(SurfaceConformanceError, match="no web_surfaces"):
        assert_surface_conformance(ModuleManifest(code="headless", version="1"))


def test_a_lookalike_named_require_permission_does_not_pass(tmp_path: Path) -> None:
    report = check_surface_conformance(_module(tmp_path, spoof=True))
    assert any("no kernel permission guard" in v for v in report.violations)


def test_unguarded_api_router_is_reported(tmp_path: Path) -> None:
    report = check_surface_conformance(_module(tmp_path, api="open"))
    assert any("API" in v and "no kernel permission" in v for v in report.violations)


def test_guarded_api_router_is_checked_and_passes(tmp_path: Path) -> None:
    report = assert_surface_conformance(_module(tmp_path, api="guarded"))
    assert report.api_routes_checked == 2  # one route x two prefixes


def test_public_route_must_be_named_and_is_then_exempt(tmp_path: Path) -> None:
    module = _module(tmp_path, guarded=False)
    assert check_surface_conformance(module).violations
    report = assert_surface_conformance(
        module, public_routes=(WebRouteRef("plant", "staff", "index"),)
    )
    assert report.routes_checked == 2


def test_widened_absolute_url_patterns_are_reported(tmp_path: Path) -> None:
    for html in (
        '<button formaction="/x">x</button>',
        '<div hx-push-url="/x"></div>',
        '<img srcset="/img/a.png 1x">',
        '<a href="//evil.example/x">x</a>',
        "<a href=/x>x</a>",
    ):
        report = check_surface_conformance(_module(tmp_path, html=html))
        assert any("absolute" in v for v in report.violations), html
