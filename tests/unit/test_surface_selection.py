"""`ProductAssemblySpec.surface_selection`: fail-closed assembly selection."""

from __future__ import annotations

import pytest
from dotmac_kernel import (
    SELECT_ALL,
    ModuleManifest,
    ProductAssemblySpec,
    SurfaceSelection,
    SurfaceSelectionError,
    TemplateRef,
    WebFacetMount,
    WebSurfaceContribution,
    create_app,
)
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.web_surfaces import WebSurfaceError
from fastapi import APIRouter
from fastapi.routing import APIRoute

_DOCS = api_documentation_policy("development")


def _router(path: str, name: str) -> APIRouter:
    router = APIRouter()

    @router.get(path, name=name)
    def view() -> dict[str, bool]:
        return {"ok": True}

    return router


def _surface(code: str, facet: str = "customer") -> WebSurfaceContribution:
    return WebSurfaceContribution(
        code=code,
        facet=facet,
        routers=(_router(f"/pages/{code}", f"{code}_index"),),
        supported_ui_contract_versions=frozenset({1}),
    )


def _module(*surfaces: WebSurfaceContribution, api: bool = True) -> ModuleManifest:
    return ModuleManifest(
        code="demo",
        version="1.0.0",
        api_routers=(_router("/demo-api/ping", "demo_ping"),) if api else (),
        web_surfaces=surfaces,
    )


def _spec(module: ModuleManifest, **kwargs: object) -> ProductAssemblySpec:
    return ProductAssemblySpec(
        api_documentation=_DOCS,
        name="selection",
        modules=(module,),
        platform_surface_enabled=False,
        ui_contract_version=1,
        web_facets=(
            WebFacetMount(
                code="customer", url_prefix="/portal", shell=TemplateRef("base.html")
            ),
        ),
        **kwargs,  # type: ignore[arg-type]
    )


def _paths(app: object) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, APIRoute)}  # type: ignore[attr-defined]


def test_no_selection_keeps_pre_selection_behaviour() -> None:
    app = create_app(_spec(_module(_surface("portal"), _surface("extra"))))
    assert {"/demo-api/ping", "/portal/pages/portal", "/portal/pages/extra"} <= _paths(
        app
    )


def test_select_all_is_explicit_and_mounts_everything() -> None:
    app = create_app(
        _spec(
            _module(_surface("portal"), _surface("extra")),
            surface_selection=SELECT_ALL,
        )
    )
    assert {"/demo-api/ping", "/portal/pages/portal", "/portal/pages/extra"} <= _paths(
        app
    )


def test_new_surface_is_not_mounted_without_selection() -> None:
    selection = SurfaceSelection(surfaces={("demo", "portal")}, api_routers={"demo"})
    before = create_app(_spec(_module(_surface("portal")), surface_selection=selection))
    # The module's next release adds a surface; the assembly's selection is unchanged.
    after = create_app(
        _spec(
            _module(_surface("portal"), _surface("extra")),
            surface_selection=selection,
        )
    )
    assert _paths(before) >= {"/demo-api/ping", "/portal/pages/portal"}
    assert _paths(after) == _paths(before)
    assert "/portal/pages/extra" not in _paths(after)


def test_unselected_surface_does_not_even_need_a_facet() -> None:
    """A new release targeting a facet this assembly lacks stays inert."""
    module = _module(_surface("portal"), _surface("staffonly", facet="staff_admin"))
    selection = SurfaceSelection(surfaces={("demo", "portal")}, api_routers={"demo"})
    app = create_app(_spec(module, surface_selection=selection))
    assert "/portal/pages/staffonly" not in _paths(app)
    # ...whereas without a selection the unknown facet refuses the boot.
    with pytest.raises(WebSurfaceError):
        create_app(_spec(module))


def test_unselected_api_routers_are_not_mounted() -> None:
    selection = SurfaceSelection(surfaces={("demo", "portal")})
    app = create_app(_spec(_module(_surface("portal")), surface_selection=selection))
    assert "/demo-api/ping" not in _paths(app)
    assert "/portal/pages/portal" in _paths(app)


def test_empty_selection_mounts_nothing_of_the_module() -> None:
    app = create_app(
        _spec(_module(_surface("portal")), surface_selection=SurfaceSelection())
    )
    assert not _paths(app) & {"/demo-api/ping", "/portal/pages/portal"}


@pytest.mark.parametrize(
    "selection",
    [
        SurfaceSelection(surfaces={("ghost", "portal")}),
        SurfaceSelection(surfaces={("demo", "ghost")}),
        SurfaceSelection(api_routers={"ghost"}),
    ],
)
def test_selection_naming_an_absent_declaration_refuses_boot(
    selection: SurfaceSelection,
) -> None:
    with pytest.raises(SurfaceSelectionError, match="absent"):
        create_app(_spec(_module(_surface("portal")), surface_selection=selection))


def test_api_selection_of_a_module_without_routers_is_refused() -> None:
    with pytest.raises(SurfaceSelectionError, match="declares none"):
        create_app(
            _spec(
                _module(_surface("portal"), api=False),
                surface_selection=SurfaceSelection(api_routers={"demo"}),
            )
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"surfaces": [("demo",)]},
        {"surfaces": [("demo", "")]},
        {"surfaces": [("a", "b"), ("a", "b")]},
        {"api_routers": ["a", "a"]},
        {"api_routers": [""]},
        {"select_all": True, "api_routers": ["a"]},
    ],
)
def test_malformed_selection_is_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(SurfaceSelectionError):
        SurfaceSelection(**kwargs)  # type: ignore[arg-type]


def test_spec_rejects_a_non_selection() -> None:
    with pytest.raises(TypeError):
        ProductAssemblySpec(
            api_documentation=_DOCS,
            name="x",
            surface_selection="all",  # type: ignore[arg-type]
        )
