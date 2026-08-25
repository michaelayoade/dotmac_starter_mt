"""The reference assembly's ProductAssemblySpec (kernel-boundary Task 3A).

This is the one place the reference application declares what it IS: its feature
modules (`FEATURE_MODULES`, assembly-owned), the presentation packages it
composes, plus the deployment-driven surface switches read from the environment.
`app/main.py` builds `app` from this spec via `dotmac_kernel.create_app`.

**`dotmac-ui` adoption (ADR-0006 U1, and the D5 consumer boundary).** The
assembly — not the kernel — composes the shared design system. The assembly
independently imports modules, `dotmac-ui` and the kernel; modules may import the
kernel, while `dotmac-ui` imports none of them. Three declarations
compose it:

- `packaged_static_dirs` layers `dotmac_ui`'s packaged assets into the existing
  `/static` mount (under any assembly-owned file, over the kernel's), so
  `/static/dotmac-ui/dotmac-ui-1.css` resolves from the installed package
  rather than from a vendored copy;
- the first `stylesheets` entry puts the package `<link>` in every page's
  `<head>`, at the URL
  `dotmac_ui.stylesheet_url()` builds, complete with its own content-derived
  cache-busting token;
- the second entry loads the assembly-owned `/branding/theme.css` projection
  after those defaults. The `presentation` feature resolves tenant brand data
  through the kernel and calls the public `dotmac-ui` generator; neither
  package reaches across the boundary itself.

Note what is NOT here: no Tailwind step, no build, no toolchain agreement
(ADR-0006 D3). This assembly compiles its own CSS with Tailwind v4; the design
system arrives already compiled, and a product on Tailwind v3 — or on no
Tailwind at all — consumes the same package and adapter contract.

The spec deliberately leaves `assembly_template_dir` / `assembly_static_dir`
unset — the reference app renders the kernel's packaged templates and serves the
kernel's static assets directly, with no override. A downstream product sets
those to layer its own look over the kernel's.
"""

from __future__ import annotations

import dotmac_template_studio
import dotmac_ticketing
import dotmac_ui
from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.config import settings
from dotmac_kernel.features import load_manifests
from dotmac_kernel.planes import ModulePlane, ModulePlaneSelection
from dotmac_kernel.platform_auth import (
    PLATFORM_COOKIE,
    PLATFORM_COOKIE_AUTHENTICATION,
)
from dotmac_kernel.web_deps import TENANT_COOKIE_AUTHENTICATION
from dotmac_kernel.web_surfaces import (
    AuthenticationProfileBinding,
    BrowserCapabilityProvision,
    BrowserSecurityPlane,
    BrowserSessionPolicy,
    NavigationRegion,
    TemplateRef,
    WebFacetMount,
    WebRouteRef,
)

from app.features import FEATURE_MODULES
from app.features.presentation.contract import BRAND_STYLESHEET_URL


def _staff_landing_route(disabled_modules: frozenset[str]) -> WebRouteRef | None:
    """The staff facet's landing route, or None when its owner is disabled.

    Same shape, and the same reason, as `_presentation_stylesheets` below: a
    facet may only reference a route this exact assembly actually mounts. The
    `web` feature is `core=False`, so `DISABLED_FEATURES=web` removes it from
    the enabled set `create_app` builds the surface registry from — and an
    unconditional `landing_route` pointing at its dashboard would then fail
    route resolution and take the whole boot down, instead of dropping the one
    route the switch is documented to drop
    (`tests/unit/test_web_surface.py::test_disabled_features_web_still_leaves_other_admin_routes_mounted`).
    """
    if "web" in disabled_modules:
        return None
    return WebRouteRef("web", "legacy", "dashboard")


def _presentation_stylesheets(disabled_modules: frozenset[str]) -> tuple[str, ...]:
    """The links whose routes/assets this exact assembly actually mounts."""
    dynamic = () if "presentation" in disabled_modules else (BRAND_STYLESHEET_URL,)
    return (dotmac_ui.stylesheet_url(), *dynamic)


# Template Studio owns the CHECKING of a template's placeholders; the product
# owns the VOCABULARY (ADR-0006 § 5b, ADR-0008 — a vocabulary is a declaration
# registry, never an enum). Registered at import time, exactly like a
# `SettingSpec`, and BEFORE the spec below so the module's admin screens can
# offer it.
#
# This is the reference assembly, so the set is deliberately minimal and
# product-neutral: the values any deployment can supply about the tenant and the
# recipient it is writing to. A real product replaces this with one context per
# send path it actually implements — a context must never declare a variable its
# sender cannot produce, because the whole guarantee is that a saved template is
# renderable.
dotmac_template_studio.register_contexts(
    dotmac_template_studio.RenderContext(
        name="default",
        variables=(
            "recipient_name",
            "recipient_email",
            "tenant_name",
            "portal_url",
        ),
        description="Values any send path in the reference assembly can supply.",
    )
)

_DISABLED_MODULES = frozenset(settings.disabled_feature_set)

assembly = ProductAssemblySpec(
    name="dotmac_starter_mt",
    # The assembly's own features, plus every INSTALLED MODULE it composes.
    #
    # `dotmac_template_studio.module` is a `ModuleManifest`, not a
    # `FeatureManifest`, so it cannot go through `load_manifests` (which
    # isinstance-checks the latter) and must NOT go in `FEATURE_MODULES` —
    # that list is the assembly's own packages, and an architecture test holds
    # it byte-for-byte equal to the features independence contract. The
    # assembly importing a module directly is the legal direction; neither the
    # module nor `dotmac-ui` imports this assembly or one another.
    modules=[
        *load_manifests(FEATURE_MODULES),
        dotmac_template_studio.module,
        dotmac_ticketing.module,
    ],
    # ADR-0028: a selectable module's planes are DECLARED here, never inferred
    # from which prerequisites happen to be bound. This assembly is a tenant
    # product that also keeps control-plane ticket state, so it selects both;
    # the vendor control plane selects PLATFORM alone from the same lineage.
    module_planes=(
        ModulePlaneSelection(
            module="ticketing",
            planes=(ModulePlane.TENANT, ModulePlane.PLATFORM),
        ),
    ),
    web_enabled=settings.web_enabled,
    ui_contract_version=dotmac_ui.UI_CONTRACT_VERSION,
    web_facets=(
        WebFacetMount(
            code="staff_admin",
            url_prefix="/admin",
            # Jinja template declaration, not subprocess shell execution.
            shell=TemplateRef("layouts/admin.html"),  # nosec B604
            authentication_profile="tenant_staff_session",
            admission_permission="web.portal.staff.access",
            navigation_regions=(NavigationRegion("primary"),),
            entry_routes=(
                WebRouteRef("auth", "legacy", "login_page"),
                WebRouteRef("auth", "legacy", "login_submit"),
                WebRouteRef("auth", "legacy", "logout"),
                WebRouteRef("presentation", "legacy", "brand_stylesheet"),
            ),
            login_route=WebRouteRef("auth", "legacy", "login_page"),
            landing_route=_staff_landing_route(_DISABLED_MODULES),
            logout_route=WebRouteRef("auth", "legacy", "logout"),
        ),
        WebFacetMount(
            code="platform_admin",
            url_prefix="/platform",
            # Jinja template declaration, not subprocess shell execution.
            shell=TemplateRef("layouts/platform.html"),  # nosec B604
            authentication_profile="platform_operator_session",
            navigation_regions=(NavigationRegion("primary"),),
            entry_routes=(
                WebRouteRef("kernel_platform", "control_plane", "login_form"),
                WebRouteRef("kernel_platform", "control_plane", "login_submit"),
            ),
            login_route=WebRouteRef("kernel_platform", "control_plane", "login_form"),
            landing_route=WebRouteRef("kernel_platform", "control_plane", "inventory"),
            logout_route=WebRouteRef("kernel_platform", "control_plane", "logout"),
        ),
    ),
    authentication_profiles=(
        AuthenticationProfileBinding(
            code="tenant_staff_session",
            provider=TENANT_COOKIE_AUTHENTICATION,
            session=BrowserSessionPolicy(cookie_name="access_token", cookie_path="/"),
            security_plane=BrowserSecurityPlane.TENANT,
        ),
        AuthenticationProfileBinding(
            code="platform_operator_session",
            provider=PLATFORM_COOKIE_AUTHENTICATION,
            session=BrowserSessionPolicy(
                cookie_name=PLATFORM_COOKIE, cookie_path="/platform"
            ),
            security_plane=BrowserSecurityPlane.PLATFORM,
        ),
    ),
    browser_capabilities=(BrowserCapabilityProvision("htmx", 2),),
    disabled_modules=_DISABLED_MODULES,
    packaged_static_dirs=(dotmac_ui.static_dir(),),
    # Template Studio declares its own namespaced package through its v2 web
    # surface. This legacy presentation-package slot now carries only the design
    # system's inert component data; every component is addressed
    # `dotmac_ui/components/...`.
    packaged_template_dirs=(dotmac_ui.template_dir(),),
    # Fixed cascade: product CSS (base.html), dotmac-ui defaults, then runtime
    # brand data. The dynamic route is assembly-owned and deliberately outside
    # Template Studio: that module authors notification content; it owns no
    # brand precedence, token generation or stylesheet delivery.
    stylesheets=_presentation_stylesheets(_DISABLED_MODULES),
)
