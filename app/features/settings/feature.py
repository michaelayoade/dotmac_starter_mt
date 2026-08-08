"""Settings feature manifest.

The model + resolver live in `dotmac_kernel` (`dotmac_kernel.settings_models`,
`dotmac_kernel.settings_resolver`) because the `custom_fields` feature must
consume them and features may never import each other. This package owns the
spec declarations, seed data, and the tenant admin API: `GET
/settings/{domain}` (list every registered spec merged with the tenant's
effective values) and `PUT /settings/{domain}/{key}` (write the tenant
override), both guarded by `require_tenant` + `require_role("admin")` — see
`router.py`.
"""

from dotmac_kernel.features import FeatureManifest, NavItem
from dotmac_kernel.settings_models import KERNEL_SETTING_DOMAINS

from app.features.settings.router import router
from app.features.settings.seed import seed_platform_defaults
from app.features.settings.web import router as web_router

# Declared by the feature that reads them, not here. Kept as a named set so the
# reason a domain is absent is visible at the place it would otherwise appear.
_DOMAINS_OWNED_BY_OTHER_FEATURES = {"auth", "audit", "custom_fields"}

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
    # This module declares every spec in `spec.py`, so it owns every domain
    # those specs name — declaration and ownership stay in one place, which is
    # the property `SettingDomainRegistry` checks. A feature that declares its
    # own specs declares its own domains alongside them.
    # Every kernel domain EXCEPT those a feature has taken ownership of by
    # declaring their specs itself. This list shrinks as specs move to their
    # readers; a domain declared here with no spec fails CI, and so does a spec
    # in a domain nobody declares.
    setting_domains=[
        str(domain)
        for domain in KERNEL_SETTING_DOMAINS
        if str(domain) not in _DOMAINS_OWNED_BY_OTHER_FEATURES
    ],
)
