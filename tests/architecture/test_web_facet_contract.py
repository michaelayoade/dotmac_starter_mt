"""Architecture guards for the browser-facet runtime (ADR-0006 amendment)."""

from __future__ import annotations

import inspect
from pathlib import Path

from dotmac_kernel.middleware.csrf import CSRF_PROTECTED_ATTR
from fastapi.routing import APIRoute

from app.main import app

ROOT = Path(__file__).resolve().parents[2]
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _dependency_has_attribute(dependant: object, attribute: str) -> bool:
    call = getattr(dependant, "call", None)
    if getattr(call, attribute, False):
        return True
    return any(
        _dependency_has_attribute(child, attribute)
        for child in getattr(dependant, "dependencies", ())
    )


def test_every_composed_unsafe_browser_route_has_explicit_csrf_dependency() -> None:
    missing: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.name.startswith("web:"):
            continue
        if not MUTATING.intersection(route.methods or set()):
            continue
        if not _dependency_has_attribute(route.dependant, CSRF_PROTECTED_ATTR):
            missing.append(f"{sorted(route.methods or set())} {route.path}")
    assert not missing, "Composed unsafe routes without CSRF: " + ", ".join(missing)


def test_create_app_does_not_install_an_assembly_as_process_global_ui_state() -> None:
    from dotmac_kernel import app_factory

    source = inspect.getsource(app_factory.create_app)
    assert "install_surface_globals(manifests" not in source
    assert "install_stylesheets(spec.stylesheets" not in source


def test_template_studio_v2_surface_never_authors_the_staff_prefix() -> None:
    paths = (
        ROOT / "packages" / "dotmac-template-studio" / "src" / "dotmac_template_studio"
    )
    offenders: list[str] = []
    for path in (paths / "web.py", *sorted((paths / "templates").rglob("*.html"))):
        if "/admin/templates" in path.read_text():
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, "v2 surface hardcodes its assembly prefix: " + ", ".join(
        offenders
    )


def test_template_studio_full_pages_extend_the_facet_shell() -> None:
    root = (
        ROOT
        / "packages"
        / "dotmac-template-studio"
        / "src"
        / "dotmac_template_studio"
        / "templates"
        / "admin"
        / "template_studio"
    )
    for name in ("index.html", "create.html", "detail.html"):
        source = (root / name).read_text()
        assert "{% extends surface.shell" in source


def test_template_studio_projects_orm_rows_to_typed_render_models() -> None:
    source = (
        ROOT
        / "packages"
        / "dotmac-template-studio"
        / "src"
        / "dotmac_template_studio"
        / "web.py"
    ).read_text()
    assert "TemplateRead.model_validate" in source
    assert "VersionRead.model_validate" in source
    assert '"templates": service.list_templates' not in source
    assert '"versions": service.list_versions' not in source


def test_shared_facet_shells_have_skip_links_and_main_landmarks() -> None:
    root = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/templates/layouts"
    for name in ("admin.html", "platform.html"):
        source = (root / name).read_text()
        assert 'href="#main-content"' in source
        assert 'id="main-content"' in source


def test_mobile_navigation_does_not_reuse_the_desktop_hidden_state() -> None:
    root = ROOT / "packages/dotmac-kernel/src/dotmac_kernel/templates"
    layout = (root / "layouts/admin.html").read_text()
    sidebar = (root / "components/sidebar.html").read_text()
    topbar = (root / "components/topbar.html").read_text()
    components = (
        ROOT / "packages/dotmac-kernel/src/dotmac_kernel/static/js/components.js"
    ).read_text()

    assert "mobile_sidebar = true" in layout
    assert "mobile_sidebar is defined and mobile_sidebar" in sidebar
    assert 'aria-controls="mobile-navigation"' in topbar
    assert 'aria-expanded="false"' in topbar
    assert 'x-on:keydown.tab="trapFocus($event)"' in layout
    assert "trapFocus: function" in components
