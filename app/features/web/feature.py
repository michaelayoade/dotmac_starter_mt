"""Web-portal feature manifest.

`core=False`: the admin dashboard shell is deletable/disable-able (template
promise — a project built from this starter that never wants an HTML admin
UI can set `DISABLED_FEATURES=web` and run API-only; `mount_features`
fault-isolates a broken optional feature instead of crashing startup, see
`app.core.features`). With `web` disabled, only `GET /admin` (the dashboard
shell, this package's one route) stops mounting — every other feature's
JSON API keeps working unchanged. `GET/POST /admin/login` and
`GET /admin/logout` are owned by the `auth` feature (`app.features.auth.web`)
and stay mounted regardless of `DISABLED_FEATURES=web`, since `auth` is a
core feature.
"""

from app.core.features import FeatureManifest
from app.features.web.web import router

feature = FeatureManifest(name="web", routers=[router], core=False)
