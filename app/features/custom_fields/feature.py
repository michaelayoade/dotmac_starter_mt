"""Custom fields feature manifest.

The definitions model (`CustomFieldDefinition`) and the entity `registry.py`
landed in Task 8; the router lands here in Task 10 — definitions + values
routes, both under router-level `require_tenant` (see `router.py`).
"""

from app.core.features import FeatureManifest
from app.features.custom_fields.router import router

feature = FeatureManifest(name="custom_fields", routers=[router])
