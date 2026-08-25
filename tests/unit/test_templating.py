"""TDD for `dotmac_kernel.templating.render()`'s per-request branding enrichment
(Task 4 / F4 fix).

`render()` reads `request.state.branding` (set by
`dotmac_kernel.branding.get_request_branding`, memoized per request -- see
`tests/unit/test_branding.py` for that function's own coverage) and injects
it into the template context as `brand`, UNLESS the caller's own `context`
already defines a `brand` key -- see `dotmac_kernel.templating`'s module
docstring for the full precedence rule. These tests exercise `render()`
directly with a minimal request-like stand-in (only `.state` is touched by
`render()` and by the templates under test, so a duck-typed object is
sufficient -- no real ASGI scope needed), rendering the real
`templates/auth/login.html` template, which shows `brand.name` directly in
its body (unlike the `errors/*.html` templates, which only reference
`brand.name` in a `{% block title %}` most of them override).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from dotmac_kernel.branding import get_brand
from dotmac_kernel.templating import compose_templates, render, templates


def _fake_request(branding: dict | None = None):
    return SimpleNamespace(state=SimpleNamespace(branding=branding))


def test_render_injects_request_state_branding_as_brand() -> None:
    request = _fake_request(branding={"name": "Tenant Override Brand"})
    response = render(request, "auth/login.html", {"next_url": "/admin", "error": None})
    body = response.body.decode()
    assert "Tenant Override Brand" in body
    assert get_brand()["name"] not in body


def test_render_falls_back_to_static_brand_when_no_request_state_branding() -> None:
    request = _fake_request(branding=None)
    response = render(request, "auth/login.html", {"next_url": "/admin", "error": None})
    body = response.body.decode()
    assert get_brand()["name"] in body


def test_render_context_explicit_brand_overrides_request_state_branding() -> None:
    """A route that passes its own `brand` context key (e.g. the branding
    editor's live preview, `app.features.settings.web`) wins over the
    per-request memoized value -- `render()`'s enrichment must not clobber
    an explicit override."""
    request = _fake_request(branding={"name": "Per-Request Brand"})
    response = render(
        request,
        "auth/login.html",
        {
            "next_url": "/admin",
            "error": None,
            "brand": {"name": "Explicit Route Brand"},
        },
    )
    body = response.body.decode()
    assert "Explicit Route Brand" in body
    assert "Per-Request Brand" not in body


def test_declared_template_namespace_cannot_be_shadowed_by_assembly(
    tmp_path: Path,
) -> None:
    assembly = tmp_path / "assembly"
    package = tmp_path / "package"
    (assembly / "sample").mkdir(parents=True)
    package.mkdir()
    (assembly / "sample" / "page.html").write_text("assembly")
    (package / "page.html").write_text("module")

    compose_templates(
        assembly_dir=assembly,
        namespaced_dirs={"sample": package},
    )

    assert templates.env.get_template("sample/page.html").render() == "module"
