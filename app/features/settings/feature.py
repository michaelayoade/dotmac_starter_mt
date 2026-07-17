"""Settings feature manifest.

Scaffold only for now (Task 3): the model + resolver live in `app.core`
(`app.core.settings_models`) because the `custom_fields` feature must consume
them and features may never import each other. This package will grow spec
declarations, seed data, router, and schemas in Tasks 4-6 — `routers=[]` is
valid until then.
"""

from app.core.features import FeatureManifest

feature = FeatureManifest(name="settings", routers=[])
