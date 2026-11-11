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

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from dotmac_kernel.branding import get_brand
from dotmac_kernel.templating import compose_templates, render, templates
from dotmac_kernel.web_surfaces import SurfaceContext
from jinja2 import (
    FileSystemBytecodeCache,
    TemplateAssertionError,
    TemplateNotFound,
    pass_environment,
)
from starlette.requests import Request


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


def test_render_filters_reach_product_page_include_import_and_kernel_macro(tmp_path):
    (tmp_path / "page.html").write_text(
        '{% from "components/table_macros.html" import table_header %}'
        '{% from "product_macros.html" import decorated %}'
        '{{ table_header([{"label": value|product_label}]) }}'
        '{% include "product_include.html" %}{{ decorated(value) }}'
    )
    (tmp_path / "product_include.html").write_text("include={{ value|product_label }};")
    (tmp_path / "product_macros.html").write_text(
        "{% macro decorated(value) %}macro={{ value|product_label }};{% endmacro %}"
    )
    compose_templates(assembly_dir=tmp_path)
    response = render(
        _fake_request(),
        "page.html",
        {"value": "label"},
        filters={"product_label": lambda value: f"custom-{value}"},
    )
    body = response.body.decode()
    assert '<th scope="col"' in body
    assert body.count("custom-label") == 3
    assert "include=custom-label;macro=custom-label;" in body
    assert "product_label" not in templates.env.filters


@pytest.mark.parametrize("filters", [None, {}])
def test_render_without_filters_keeps_canonical_template_and_body(tmp_path, filters):
    (tmp_path / "plain.html").write_text("{{ value|upper }} {{ csrf_token }}")
    compose_templates(assembly_dir=tmp_path)
    request = _fake_request()
    canonical = render(request, "plain.html", {"value": "plain"})
    response = render(request, "plain.html", {"value": "plain"}, filters=filters)
    assert response.body == canonical.body
    assert response.template is canonical.template
    assert response.template.environment is templates.env
    assert response.context == canonical.context


@pytest.mark.parametrize("first, second", [("one", "two"), ("two", "one")])
def test_render_filters_are_isolated_between_responses_and_canonical_cache(
    tmp_path, first, second
):
    (tmp_path / "filtered.html").write_text("{{ value|product_label }}")
    compose_templates(assembly_dir=tmp_path)
    request = _fake_request()
    for expected in (first, second, first):
        response = render(
            request,
            "filtered.html",
            {"value": "value"},
            filters={"product_label": lambda _value, result=expected: result},
        )
        assert response.body == expected.encode()
        assert response.template.environment is not templates.env
    with pytest.raises(TemplateAssertionError, match="product_label"):
        render(request, "filtered.html", {"value": "value"})
    assert "product_label" not in templates.env.filters


def test_render_filter_snapshot_survives_caller_mapping_mutation(tmp_path):
    (tmp_path / "snapshot.html").write_text(
        '{{ value|product_label }}{% include "snapshot_include.html" %}'
    )
    (tmp_path / "snapshot_include.html").write_text("{{ value|product_label }}")
    compose_templates(assembly_dir=tmp_path)
    filters = {}

    def initial(value):
        filters["product_label"] = lambda _value: "changed"
        return value

    filters["product_label"] = initial
    response = render(
        _fake_request(), "snapshot.html", {"value": "original"}, filters=filters
    )
    assert response.body == b"originaloriginal"
    assert filters["product_label"] is not initial
    assert "product_label" not in templates.env.filters


def test_render_filters_ignore_shared_constant_folded_bytecode(tmp_path, monkeypatch):
    source = tmp_path / "templates"
    source.mkdir()
    cache_dir = tmp_path / "bytecode"
    cache_dir.mkdir()
    (source / "constant.html").write_text('{{ "literal"|product_label }}')
    compose_templates(assembly_dir=source)
    cache = FileSystemBytecodeCache(str(cache_dir))
    monkeypatch.setattr(templates.env, "bytecode_cache", cache)
    # Seed an authentic cache entry folded under a different implementation.
    seed = templates.env.overlay()
    seed.filters = {**templates.env.filters, "product_label": lambda _value: "stale"}
    assert seed.get_template("constant.html").render() == "stale"
    cached = {p.name: p.read_bytes() for p in cache_dir.iterdir()}
    assert cached
    for expected in ("first", "second"):
        response = render(
            _fake_request(),
            "constant.html",
            filters={"product_label": lambda _value, result=expected: result},
        )
        assert response.body == expected.encode()
        assert response.template.environment.bytecode_cache is None
    assert {p.name: p.read_bytes() for p in cache_dir.iterdir()} == cached
    assert templates.env.bytecode_cache is cache


@pytest.mark.parametrize("name", sorted(templates.env.filters))
def test_render_rejects_every_canonical_filter_collision(tmp_path, name):
    (tmp_path / "clean.html").write_text('{{ "value"|upper }}')
    compose_templates(assembly_dir=tmp_path)
    canonical_filters = dict(templates.env.filters)
    with pytest.raises(ValueError, match="reserved"):
        render(_fake_request(), "clean.html", filters={name: lambda value: value})
    assert templates.env.filters == canonical_filters
    assert render(_fake_request(), "clean.html").body == b"VALUE"


@pytest.mark.parametrize(
    "filters, error",
    [
        ([], TypeError),
        ([("custom", str)], TypeError),
        ({1: str}, ValueError),
        ({"": str}, ValueError),
        ({"bad-name": str}, ValueError),
        ({"bad..name": str}, ValueError),
        ({" bad": str}, ValueError),
        ({"2bad": str}, ValueError),
        ({"custom": None}, TypeError),
        ({"valid": str, "invalid": 1}, TypeError),
    ],
)
def test_invalid_render_filter_mapping_leaves_clean_later_state(
    tmp_path, filters, error
):
    (tmp_path / "clean.html").write_text("clean")
    compose_templates(assembly_dir=tmp_path)
    canonical_filters = dict(templates.env.filters)
    with pytest.raises(error):
        render(_fake_request(), "clean.html", filters=filters)
    assert templates.env.filters == canonical_filters
    assert render(_fake_request(), "clean.html").body == b"clean"
    assert (
        render(_fake_request(), "clean.html", filters={"valid": str}).body == b"clean"
    )


def test_render_filters_preserve_context_autoescape_processors_and_metadata(
    tmp_path, monkeypatch
):
    (tmp_path / "metadata.html").write_text(
        "{{ value|product_label }}|{{ csrf_token }}|{{ brand.name }}|"
        '{{ surface.landing_path }}|{{ enabled_features|join(",") }}|'
        '{{ extra_stylesheets|join(",") }}|{{ request.url.path }}|{{ processed }}'
    )
    compose_templates(assembly_dir=tmp_path)
    surface = replace(
        SurfaceContext.empty(),
        landing_path="/landing",
        enabled_modules=frozenset({"parties"}),
        stylesheets=("/product.css",),
    )
    request = Request({"type": "http", "path": "/metadata", "headers": []})
    request.state.surface_context = surface
    request.state.csrf_token = "csrf-value"
    request.state.branding = {"name": "tenant-brand"}
    processors = [lambda req: {"processed": req.url.path}]
    monkeypatch.setattr(templates, "context_processors", processors)
    canonical_globals = dict(templates.env.globals)

    @pass_environment
    def label(env, value):
        env.globals["render_only"] = True
        return value

    context = {"value": "<script>"}
    response = render(
        request,
        "metadata.html",
        context,
        status_code=422,
        filters={"product_label": label},
    )
    assert response.body.decode() == (
        "&lt;script&gt;|csrf-value|tenant-brand|/landing|parties|"
        "/product.css|/metadata|/metadata"
    )
    assert response.status_code == 422
    assert response.template.name == "metadata.html"
    assert response.context["request"] is request
    assert response.context["surface"] is surface
    assert response.context["csrf_token"] == "csrf-value"
    assert response.context["brand"] == {"name": "tenant-brand"}
    assert response.context["processed"] == "/metadata"
    assert context == {"value": "<script>"}
    assert response.template.environment.autoescape == templates.env.autoescape
    assert response.template.environment.loader is templates.env.loader
    assert response.template.environment.globals["render_only"] is True
    assert templates.env.globals == canonical_globals
    assert templates.context_processors is processors


def test_filtered_render_preserves_namespace_protection_and_sequential_reset(tmp_path):
    assembly = tmp_path / "assembly"
    module = tmp_path / "module"
    (assembly / "sample").mkdir(parents=True)
    module.mkdir()
    (assembly / "sample" / "page.html").write_text("assembly")
    (module / "page.html").write_text("{{ value|product_label }}")
    compose_templates(assembly_dir=assembly, namespaced_dirs={"sample": module})
    response = render(
        _fake_request(),
        "sample/page.html",
        {"value": "module"},
        filters={"product_label": str.upper},
    )
    assert response.body == b"MODULE"
    compose_templates()
    with pytest.raises(TemplateNotFound):
        render(
            _fake_request(),
            "sample/page.html",
            filters={"product_label": str.upper},
        )
    assert "product_label" not in templates.env.filters


def test_render_bare_request_without_application_uses_empty_surface(tmp_path):
    (tmp_path / "bare.html").write_text("{{ surface.landing_path }}|{{ csrf_token }}")
    compose_templates(assembly_dir=tmp_path)
    request = Request({"type": "http", "path": "/bare", "headers": []})
    request.state.csrf_token = "bare-csrf"
    response = render(request, "bare.html")
    assert response.body == b"None|bare-csrf"
    assert response.context["surface"] == SurfaceContext.empty()
    assert response.context["request"] is request


def test_render_request_application_still_supplies_default_surface(tmp_path):
    (tmp_path / "default.html").write_text("{{ surface.landing_path }}")
    compose_templates(assembly_dir=tmp_path)
    surface = replace(SurfaceContext.empty(), landing_path="/default")
    request = Request(
        {
            "type": "http",
            "path": "/default",
            "headers": [],
            "app": SimpleNamespace(
                state=SimpleNamespace(default_surface_context=surface)
            ),
        }
    )
    response = render(request, "default.html", filters={"product_label": str})
    assert response.body == b"/default"
    assert response.context["surface"] is surface


@pytest.mark.parametrize("name", ["product_label", "product.label"])
def test_render_accepts_identifier_and_dotted_filter_names(tmp_path, name):
    (tmp_path / "named.html").write_text("{{ value|" + name + " }}")
    compose_templates(assembly_dir=tmp_path)
    response = render(
        _fake_request(),
        "named.html",
        {"value": "valid"},
        filters={name: str.upper},
    )
    assert response.body == b"VALID"


def test_filtered_response_retains_starlette_debug_extension(tmp_path):
    (tmp_path / "debug.html").write_text("{{ value|product_label }}")
    compose_templates(assembly_dir=tmp_path)
    request = Request(
        {
            "type": "http",
            "path": "/debug",
            "headers": [],
            "extensions": {"http.response.debug": {}},
        }
    )
    response = render(
        request,
        "debug.html",
        {"value": "debug"},
        status_code=201,
        filters={"product_label": str.upper},
    )
    messages = []

    async def send(message):
        messages.append(message)

    async def receive():
        return {"type": "http.request", "body": b""}

    asyncio.run(response(request.scope, receive, send))
    assert messages[0] == {
        "type": "http.response.debug",
        "info": {"template": response.template, "context": response.context},
    }
    assert messages[1]["type"] == "http.response.start"
    assert messages[1]["status"] == 201
    assert messages[2]["body"] == b"DEBUG"
