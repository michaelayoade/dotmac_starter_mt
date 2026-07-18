"""Auth feature manifest.

`web_router` (`GET/POST /admin/login`, `GET /admin/logout`) is entirely a
`web_routers` entry, not `routers` — cookie-based login/logout has no
meaning without an HTML surface to authenticate INTO (API-only mode uses
bearer tokens via `router`'s `/auth/*` JSON endpoints only, never cookies).
When `WEB_ENABLED=false`, this router simply doesn't mount, same as every
other feature's `web_routers` (see `app.core.features`'s module docstring).
No `nav` — a login page is pre-auth by definition and never belongs in the
authenticated sidebar.
"""

from app.core.features import FeatureManifest
from app.features.auth.router import router
from app.features.auth.web import router as web_router

feature = FeatureManifest(name="auth", routers=[router], web_routers=[web_router])
