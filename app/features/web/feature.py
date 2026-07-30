"""Web-portal feature manifest.

`core=False`: the admin dashboard shell is deletable/disable-able
(`mount_features` fault-isolates a broken optional feature instead of
crashing startup, see `dotmac_kernel.features`). With `web` disabled via
`DISABLED_FEATURES=web`, only `GET /admin` (the dashboard shell, this
package's one route, now in `web_routers`) stops mounting — every OTHER
feature's own `/admin/*` screens and every feature's JSON API keep working
unchanged. `DISABLED_FEATURES=web` is a single-feature switch, NOT the
API-only switch (that conflation was finding F1) — for an actual pure-JSON
deployment with no `/admin` surface at all across every feature, set
`WEB_ENABLED=false` instead (`dotmac_kernel.config.Settings.web_enabled`, gates
`mount_features`'s `web_routers` mounting and the `/static` mount in
`app/main.py`). `GET/POST /admin/login` and `POST /admin/logout` are owned by
the `auth` feature (`app.features.auth.web`, also in ITS `web_routers` now)
and stay mounted whenever `web_enabled` is True, regardless of
`DISABLED_FEATURES=web` — login/logout are `auth`'s business (a core
feature), not this dashboard shell's.
"""

from dotmac_kernel.features import FeatureManifest, NavItem

from app.features.web.web import router

feature = FeatureManifest(
    name="web",
    web_routers=[router],
    nav=[NavItem("Dashboard", "/admin")],
    core=False,
)
