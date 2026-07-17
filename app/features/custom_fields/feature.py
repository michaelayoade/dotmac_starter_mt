"""Custom fields feature manifest.

The definitions model (`CustomFieldDefinition`) and the entity `registry.py`
land in Task 8; the router lands in Task 10 — this manifest carries an empty
`routers` tuple until then.
"""

from app.core.features import FeatureManifest

feature = FeatureManifest(name="custom_fields", routers=[])
