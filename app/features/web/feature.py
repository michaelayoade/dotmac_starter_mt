"""Web-portal feature manifest.

`core=False`: the admin portal is deletable/disable-able (template promise —
a project built from this starter that never wants an HTML admin UI can set
`DISABLED_FEATURES=web` and run API-only; `mount_features` fault-isolates a
broken optional feature instead of crashing startup, see
`app.core.features`). With `web` disabled, `/admin/*` simply doesn't mount —
every other feature's JSON API keeps working unchanged.
"""

from app.core.features import FeatureManifest
from app.features.web.web import router

feature = FeatureManifest(name="web", routers=[router], core=False)
