"""ERP-like composition boot (Starter parity programme, PROGRAMME 4.3 item 3).

Boots an assembly shaped like ERP v2 - one ``staff_admin`` facet at ``/`` with a
tenant-cookie authentication profile and an admission permission, the ERP
module set, and an EXPLICIT ``surface_selection`` - and runs the surface
conformance kit over a packaged surface inside that composition.  It proves
wave-end composition on the integration branch before any branch-to-main squash
or ERP pin bump.  No database, no network: `create_app` is built, never served.

The module set mirrors ERP v2's ``assembly.py`` (the product-local features and
readiness are not Starter code and are represented by one probe module that
declares the admission permission).  Template Studio is added as the packaged
web surface under test: ERP v2 does not pin it yet, and this is exactly the
composition a Starter surface module will meet there.
"""

from __future__ import annotations

import functools

from dotmac_kernel import (
    SELECT_ALL,
    ModuleManifest,
    PermissionSpec,
    ProductAssemblySpec,
    SurfaceSelection,
    create_app,
)
from dotmac_kernel.api_documentation import api_documentation_policy
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from dotmac_kernel.testing.surface_conformance import assert_surface_conformance
from dotmac_kernel.web_deps import TENANT_COOKIE_AUTHENTICATION
from dotmac_kernel.web_surfaces import (
    AuthenticationProfileBinding,
    BrowserCapabilityProvision,
    BrowserSecurityPlane,
    BrowserSessionPolicy,
    NavigationRegion,
    TemplateRef,
    WebFacetMount,
)
from dotmac_ui import UI_CONTRACT_VERSION
from fastapi.routing import APIRoute

ADMISSION_PERMISSION = "erp.web.access"

#: Stands in for the product-local ERP features: the one thing the composition
#: needs from them is the facet admission permission being declared.
_ERP_LOCAL_PROBE = ModuleManifest(
    code="erp_local_probe",
    version="0.0.0",
    permissions=(
        PermissionSpec(
            code=ADMISSION_PERMISSION,
            description="Admission to the ERP staff facet (composition probe).",
        ),
    ),
)

_MODULE_PLANES = (
    ModulePlaneSelection(module="approvals", planes=(ModulePlane.TENANT,)),
    ModulePlaneSelection(module="billing", planes=(ModulePlane.TENANT,)),
    ModulePlaneSelection(module="collections", planes=(ModulePlane.TENANT,)),
    ModulePlaneSelection(module="durable_timers", planes=(ModulePlane.TENANT,)),
    ModulePlaneSelection(
        module="files", planes=(ModulePlane.TENANT, ModulePlane.PLATFORM)
    ),
    ModulePlaneSelection(module="numbering", planes=(ModulePlane.TENANT,)),
    ModulePlaneSelection(module="ticketing", planes=(ModulePlane.TENANT,)),
)


@functools.cache
def _erp_modules() -> tuple[ModuleManifest, ...]:
    """Imported lazily: manifests import their models, and tests that measure
    which tables a module adds to `Base.metadata` must not see them pre-loaded
    by collection."""
    from dotmac_accounting.manifest import module as accounting_module
    from dotmac_ai_operations.manifest import module as ai_operations_module
    from dotmac_analytics.manifest import module as analytics_module
    from dotmac_approvals.manifest import module as approvals_module
    from dotmac_assets.manifest import module as assets_module
    from dotmac_banking.manifest import module as banking_module
    from dotmac_billing.manifest import module as billing_module
    from dotmac_collections.manifest import module as collections_module
    from dotmac_commercial_agreements.manifest import module as agreements_module
    from dotmac_customers.manifest import module as customers_module
    from dotmac_documents.manifest import module as documents_module
    from dotmac_durable_timers.manifest import module as durable_timers_module
    from dotmac_expenses.manifest import module as expenses_module
    from dotmac_files.manifest import module as files_module
    from dotmac_finance.manifest import module as finance_module
    from dotmac_forms.manifest import module as forms_module
    from dotmac_imports.manifest import module as imports_module
    from dotmac_inbox.manifest import module as inbox_module
    from dotmac_inventory.manifest import module as inventory_module
    from dotmac_numbering.manifest import module as numbering_module
    from dotmac_party.manifest import module as party_module
    from dotmac_payables.manifest import module as payables_module
    from dotmac_payments.manifest import module as payments_module
    from dotmac_payroll.manifest import module as payroll_module
    from dotmac_people.manifest import module as people_module
    from dotmac_procurement.manifest import module as procurement_module
    from dotmac_projects.manifest import module as projects_module
    from dotmac_sales.manifest import module as sales_module
    from dotmac_tax.manifest import module as tax_module
    from dotmac_ticketing.manifest import module as ticketing_module
    from dotmac_workflow_runtime.manifest import module as workflow_runtime_module

    return (
        accounting_module,
        ai_operations_module,
        analytics_module,
        approvals_module,
        assets_module,
        banking_module,
        billing_module,
        collections_module,
        agreements_module,
        customers_module,
        documents_module,
        durable_timers_module,
        expenses_module,
        files_module,
        finance_module,
        forms_module,
        imports_module,
        inbox_module,
        inventory_module,
        numbering_module,
        party_module,
        payables_module,
        payments_module,
        payroll_module,
        people_module,
        procurement_module,
        projects_module,
        sales_module,
        tax_module,
        ticketing_module,
        workflow_runtime_module,
    )


@functools.cache
def _template_studio() -> ModuleManifest:
    from dotmac_template_studio import module

    return module


def _composed() -> tuple[ModuleManifest, ...]:
    return (_ERP_LOCAL_PROBE, *_erp_modules(), _template_studio())


def _explicit_selection() -> SurfaceSelection:
    """The current set, written as explicit pairs (never ``SELECT_ALL``)."""
    return SurfaceSelection(
        surfaces={
            (m.code, surface.code) for m in _composed() for surface in m.web_surfaces
        },
        api_routers={m.code for m in _composed() if m.api_routers},
    )


def _erp_like_spec(
    prefix: str = "/", *, selection: SurfaceSelection | None = None
) -> ProductAssemblySpec:
    return ProductAssemblySpec(
        name="erp-like-composition",
        modules=_composed(),
        module_planes=_MODULE_PLANES,
        tenancy="multi",
        platform_surface_enabled=False,
        api_documentation=api_documentation_policy("development"),
        ui_contract_version=UI_CONTRACT_VERSION,
        web_facets=(
            WebFacetMount(
                code="staff_admin",
                url_prefix=prefix,
                shell=TemplateRef("base.html"),
                authentication_profile="erp_tenant_session",
                admission_permission=ADMISSION_PERMISSION,
                navigation_regions=(NavigationRegion("primary"),),
            ),
        ),
        authentication_profiles=(
            AuthenticationProfileBinding(
                code="erp_tenant_session",
                provider=TENANT_COOKIE_AUTHENTICATION,
                session=BrowserSessionPolicy(
                    cookie_name="access_token", cookie_path=prefix
                ),
                security_plane=BrowserSecurityPlane.TENANT,
            ),
        ),
        browser_capabilities=(BrowserCapabilityProvision("htmx", 2),),
        surface_selection=selection if selection is not None else _explicit_selection(),
    )


_WEB_PREFIX = "web:staff_admin:template_studio:"


def _routes(app: object) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in app.routes  # type: ignore[attr-defined]
        if isinstance(route, APIRoute)
        for method in route.methods or set()
    }


def _names(app: object) -> set[str]:
    return {
        route.name
        for route in app.routes  # type: ignore[attr-defined]
        if isinstance(route, APIRoute)
    }


def test_erp_like_composition_boots_with_an_explicit_selection() -> None:
    app = create_app(_erp_like_spec())

    assert _routes(app), "the composition mounted nothing"
    # The packaged web surface is served by the ROOT facet, not under a prefix.
    served = {name for name in _names(app) if name.startswith(_WEB_PREFIX)}
    assert served, "the selected packaged surface was not mounted at the root facet"


def test_explicit_selection_equals_the_current_set() -> None:
    explicit = _routes(create_app(_erp_like_spec()))
    everything = _routes(create_app(_erp_like_spec(selection=SELECT_ALL)))

    assert explicit == everything
    assert len(explicit) > 10, "the comparison is vacuous"


def test_a_surface_missing_from_the_selection_is_not_mounted() -> None:
    narrowed = SurfaceSelection(
        surfaces=(),
        api_routers={m.code for m in _composed() if m.api_routers},
    )
    selected = _names(create_app(_erp_like_spec()))
    unselected = _names(create_app(_erp_like_spec(selection=narrowed)))

    assert any(name.startswith(_WEB_PREFIX) for name in selected)
    assert not any(name.startswith(_WEB_PREFIX) for name in unselected)


def test_surface_conformance_holds_inside_the_erp_like_composition() -> None:
    report = assert_surface_conformance(
        _template_studio(),
        prefixes=("/",),
        spec_for_prefix=lambda prefix: _erp_like_spec(prefix),
    )

    assert report.routes_checked > 0
    assert report.templates_checked > 0
