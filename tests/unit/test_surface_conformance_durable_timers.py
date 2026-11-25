"""The surface conformance kit, run over the durable-timers staff surface."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from dotmac_durable_timers.manifest import module
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.planes import (
    ModulePlane,
    ModulePlaneSelection,
    install_module_plane_selections,
    installed_module_plane_selections,
)
from dotmac_kernel.surface_selection import SELECT_ALL
from dotmac_kernel.testing.surface_conformance import assert_surface_conformance
from dotmac_kernel.web_surfaces import (
    BrowserCapabilityProvision,
    NavigationRegion,
    TemplateRef,
    WebFacetMount,
)

from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS


def _spec_for_prefix(prefix: str) -> ProductAssemblySpec:
    """The kit's own composition plus the explicit plane selection a dual-plane
    module requires (the kit's builder cannot take one); nothing else added."""
    regions = {item.region for s in module.web_surfaces for item in s.navigation}
    return ProductAssemblySpec(
        name="surface-conformance:durable_timers",
        modules=(module,),
        module_planes=(
            ModulePlaneSelection(module="durable_timers", planes=(ModulePlane.TENANT,)),
        ),
        api_documentation=api_documentation_policy("development"),
        platform_surface_enabled=False,
        ui_contract_version=1,
        web_facets=tuple(
            WebFacetMount(
                code=surface.facet,
                url_prefix=prefix,
                shell=TemplateRef("base.html"),
                navigation_regions=tuple(
                    NavigationRegion(region) for region in sorted(regions)
                ),
            )
            for surface in module.web_surfaces
        ),
        browser_capabilities=(BrowserCapabilityProvision("htmx", 2),),
        surface_selection=SELECT_ALL,
        prerequisite_bindings=ASSEMBLY_PREREQUISITE_BINDINGS,
    )


@pytest.fixture(autouse=True)
def _tenant_plane_installed() -> Iterator[None]:
    """The assembly's plane selection is also process state; put it back after."""
    previous = installed_module_plane_selections()
    install_module_plane_selections(
        (ModulePlaneSelection(module="durable_timers", planes=(ModulePlane.TENANT,)),)
    )
    yield
    install_module_plane_selections(previous)


def test_durable_timers_conforms_under_admin_and_root_prefixes() -> None:
    report = assert_surface_conformance(module, spec_for_prefix=_spec_for_prefix)
    assert report.routes_checked > 0
    assert report.templates_checked > 0
    assert report.nav_items_checked > 0
