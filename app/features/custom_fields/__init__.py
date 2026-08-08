# Importing the package registers this feature's setting specs, the same
# way `app.features.settings` does. A spec that is never imported is never
# registered, and its reader would resolve the spec default forever.
from app.features.custom_fields import spec  # noqa: F401
