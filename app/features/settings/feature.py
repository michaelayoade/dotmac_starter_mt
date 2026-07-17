"""Settings feature manifest.

The model + resolver live in `app.core` (`app.core.settings_models`,
`app.core.settings_resolver`) because the `custom_fields` feature must
consume them and features may never import each other. This package owns the
spec declarations, seed data, and the tenant admin API (Task 5): `GET
/settings/{domain}` (list every registered spec merged with the tenant's
effective values) and `PUT /settings/{domain}/{key}` (write the tenant
override), both guarded by `require_tenant` + `require_role("admin")` — see
`router.py`.
"""

from app.core.features import FeatureManifest
from app.features.settings.router import router
from app.features.settings.seed import seed_platform_defaults

feature = FeatureManifest(
    name="settings", routers=[router], seed=seed_platform_defaults
)
