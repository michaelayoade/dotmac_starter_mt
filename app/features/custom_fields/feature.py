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

from app.core.features import FeatureManifest
from app.features.custom_fields.router import router
from app.features.custom_fields.web import router as web_router

feature = FeatureManifest(name="custom_fields", routers=[router, web_router])
