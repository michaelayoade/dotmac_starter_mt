"""Settings feature manifest.

The model + resolver live in `dotmac_kernel` (`dotmac_kernel.settings_models`,
`dotmac_kernel.settings_resolver`) because the `custom_fields` feature must
consume them and features may never import each other. This package owns the
spec declarations, seed data, and the tenant admin API (Task 5): `GET
/settings/{domain}` (list every registered spec merged with the tenant's
effective values) and `PUT /settings/{domain}/{key}` (write the tenant
override), both guarded by `require_tenant` + `require_role("admin")` — see
`router.py`.
"""

from dotmac_kernel.features import FeatureManifest, NavItem

from app.features.settings.router import router
from app.features.settings.seed import seed_platform_defaults
from app.features.settings.web import router as web_router

feature = FeatureManifest(
    name="settings",
    routers=[router],
    web_routers=[web_router],
    nav=[NavItem("Settings", "/admin/settings")],
    seed=seed_platform_defaults,
    # One action for both surfaces: the JSON `PUT /settings/{domain}/{key}` and
    # the `/admin/settings` screens write the SAME `settings.update` action, so
    # the trail reads identically however the change arrived (module
    # control-plane directive step 3).
    audit_actions=["settings.update"],
)
