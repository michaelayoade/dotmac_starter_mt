"""Custom fields feature manifest.

The definitions model (`CustomFieldDefinition`) and the entity `registry.py`
landed in Task 8; the JSON router landed in Task 10 (definitions + values
routes, both under router-level `require_tenant` — see `router.py`). Task 7
(phase 2b) adds the admin HTMX surface (`web.py`) — definitions list/create/
edit/deactivate under `/admin/custom-fields`, plus the `/admin/custom-fields/
party/{party_id}/values-panel` fragment route the parties feature's detail
page lazy-loads (cross-feature UI composition via a browser-side `hx-get`,
not a Python import — see `web.py`'s module docstring).
"""

from dotmac_kernel.capabilities import CapabilitySpec
from dotmac_kernel.features import FeatureManifest, NavItem

from app.features.custom_fields.router import router
from app.features.custom_fields.web import router as web_router

feature = FeatureManifest(
    name="custom_fields",
    routers=[router],
    web_routers=[web_router],
    nav=[NavItem("Custom Fields", "/admin/custom-fields")],
    # WS1: this module's licensable capability code (referenced by entitlement
    # grants / licences, e.g. the WS8 receiver in app/features/licensing —
    # declared here because a capability code may never be invented outside
    # its owning module's manifest). ENFORCED since step 4: both routers carry
    # `require_capability("custom_fields.use")`.
    #
    # `default_granted=True` — the reference assembly ships custom fields as a
    # bundled feature, so a newly provisioned tenant has it, matching what
    # migration a004 gave every tenant that predates enforcement. A deployment
    # that SELLS custom fields flips this to False; nothing else changes.
    # This feature declares the `custom_fields` setting DOMAIN because it now
    # owns the spec in that domain (`spec.py`). A declaration follows its
    # declarations' owner — ADR-0008.
    setting_domains=("custom_fields",),
    capabilities=(
        CapabilitySpec(
            code="custom_fields.use",
            description="Define and use custom fields on registered entities.",
            default_granted=True,
        ),
    ),
)
