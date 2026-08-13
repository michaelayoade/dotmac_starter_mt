"""The reference assembly really consumes `dotmac-ui` (ADR-0006 D5).

D5 admits the UI package "in the same change that introduces a real consumer".
A package that ships, passes its own tests, and is loaded by nothing is not
adopted — it is a directory. These tests prove the whole path end to end: the
assembly declares the package's static dir and stylesheet URL, the app serves
the file at that URL, and every rendered page carries the `<link>`.

They also pin the two failure modes this seam has:

- **the kernel must not learn what `dotmac-ui` is.** `stylesheets` /
  `packaged_static_dirs` are anonymous slots on `ProductAssemblySpec`, and the
  dependency direction (`assembly → dotmac-ui → dotmac-kernel`) forbids the
  kernel reaching the other way. `tests/architecture/test_ui_public_surface.py`
  and the import-linter contracts hold the boundary; here we only prove the slot
  works without one.
- **API-only mode must stay API-only.** `WEB_ENABLED=false` mounts no HTML
  surface at all, so it must not advertise a stylesheet for a `<head>` that will
  never render.
"""

from __future__ import annotations

import dotmac_ui
import pytest
from dotmac_kernel import ProductAssemblySpec, create_app
from dotmac_kernel.templating import install_stylesheets, templates
from fastapi.testclient import TestClient
from starlette.routing import Mount

from app.assembly import _presentation_stylesheets, assembly
from app.main import app


@pytest.fixture(autouse=True)
def _reference_stylesheets():
    """Re-install the reference assembly's stylesheet globals before each test.

    `extra_stylesheets` is a process-static Jinja global, so any other test that
    calls `create_app` with a different spec (several in `test_create_app.py`
    do) clobbers it for whatever runs next. Exactly the same reason — and
    exactly the same shape — as `tests/unit/conftest.py`'s
    `_default_surface_globals`: establish the baseline per test so assertions
    about rendered output do not depend on collection order.
    """
    install_stylesheets(assembly.stylesheets)


def test_the_reference_assembly_declares_the_design_system() -> None:
    assert assembly.packaged_static_dirs == (dotmac_ui.static_dir(),)
    assert assembly.stylesheets == (
        dotmac_ui.stylesheet_url(),
        "/branding/theme.css",
    )


def test_disabling_presentation_removes_its_dynamic_link_not_ui_defaults() -> None:
    assert _presentation_stylesheets(frozenset({"presentation"})) == (
        dotmac_ui.stylesheet_url(),
    )


def test_the_reference_assembly_declares_the_component_template_dir() -> None:
    assert dotmac_ui.template_dir() in assembly.packaged_template_dirs


def test_the_assembly_loader_resolves_a_packaged_component() -> None:
    """The proof that was missing when `empty_state` first shipped.

    The component-level tests render on a bare `Environment` built directly
    over `template_dir()`, and the wheel test proves the file is packaged.
    Neither exercises the path that actually matters: the SHARED loader behind
    `dotmac_kernel.templating.templates`, composed by `create_app` from the
    assembly's `packaged_template_dirs`.

    Because nothing asserted that, a kernel template importing the macro
    resolved fine in every component test and 500'd with `TemplateNotFound` on
    every `/admin` page that renders an empty state. This test fails in exactly
    that situation.
    """
    template = templates.env.get_template("dotmac_ui/components/empty_state.html")
    rendered = template.module.empty_state(title="Nothing here")

    assert "dmui-empty-state" in str(rendered)


def test_a_kernel_template_can_import_the_packaged_macro() -> None:
    """The real shape of the failure: the IMPORT, from a kernel-owned template.

    `{% from "dotmac_ui/..." import ... %}` is resolved by the importing
    template's environment, so this is the composition that broke — not the
    component rendering in isolation.
    """
    source = (
        '{% from "dotmac_ui/components/empty_state.html" import empty_state %}'
        "{{ empty_state(title='No parties found') }}"
    )
    rendered = templates.env.from_string(source).render()

    assert "dmui-empty-state__message" in rendered
    assert "No parties found" in rendered


def test_the_static_mount_serves_the_packaged_stylesheet() -> None:
    """Served from the INSTALLED package, not a vendored copy — the point of
    layering `packaged_static_dirs` into the existing `/static` mount."""
    assert any(isinstance(r, Mount) and r.path == "/static" for r in app.routes)
    with TestClient(app) as client:
        response = client.get(f"/static/{dotmac_ui.STYLESHEET_RELPATH}")
    assert response.status_code == 200
    assert "css" in response.headers["content-type"]
    assert "--dmui-surface-primary:" in response.text
    assert response.text == dotmac_ui.stylesheet_path().read_text(encoding="utf-8")


def test_the_kernel_static_assets_still_resolve_underneath() -> None:
    """Layering must ADD a directory, not replace the kernel's."""
    with TestClient(app) as client:
        assert client.get("/static/js/csrf.js").status_code == 200


def test_every_page_links_the_design_system_stylesheet() -> None:
    """Rendered from the real base layout every admin and auth page extends, so
    this covers the whole HTML surface rather than one screen."""
    rendered = templates.env.get_template("base.html").render()
    assert f'<link rel="stylesheet" href="{dotmac_ui.stylesheet_url()}">' in rendered
    assert '<link rel="stylesheet" href="/branding/theme.css">' in rendered


def test_the_design_system_loads_after_the_assembly_stylesheet() -> None:
    """Cascade order is the contract (`install_stylesheets`' docstring): the
    design system's tokens and its focus-indicator rule must win over the
    compiled utility CSS on equal specificity, so its link comes last."""
    rendered = templates.env.get_template("base.html").render()
    assert rendered.index("/static/css/main.css") < rendered.index(
        dotmac_ui.STYLESHEET_RELPATH
    )


def test_runtime_brand_css_loads_after_design_system_defaults() -> None:
    """Fixed cascade: assembly CSS, UI defaults, then resolved brand data."""
    rendered = templates.env.get_template("base.html").render()
    assert rendered.index(dotmac_ui.STYLESHEET_RELPATH) < rendered.index(
        "/branding/theme.css"
    )


def test_api_only_deployments_advertise_no_stylesheet() -> None:
    create_app(
        ProductAssemblySpec(
            name="api-only-probe",
            modules=(),
            web_enabled=False,
            stylesheets=("/static/should-not-be-installed.css",),
            packaged_static_dirs=(dotmac_ui.static_dir(),),
        )
    )
    assert templates.env.globals["extra_stylesheets"] == ()


def test_the_stylesheet_url_is_same_origin_and_cache_busted() -> None:
    """No CDN (fleet standard, and ADR-0006 D7's deny-by-default CSP), and a
    content-derived version so a token change cannot be served from a stale
    browser cache."""
    url = dotmac_ui.stylesheet_url()
    assert url.startswith("/static/")
    assert f"?v={dotmac_ui.asset_digest()}" in url


@pytest.mark.parametrize("spec_field", ["packaged_static_dirs", "stylesheets"])
def test_the_new_spec_slots_are_normalised_to_tuples(spec_field: str) -> None:
    spec = ProductAssemblySpec(
        name="probe",
        modules=(),
        packaged_static_dirs=[dotmac_ui.static_dir()],
        stylesheets=["/a.css"],
    )
    assert isinstance(getattr(spec, spec_field), tuple)
