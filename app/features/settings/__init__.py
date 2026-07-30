"""Settings feature package.

Importing this package registers the initial setting specs with the core
registry (`dotmac_kernel.settings_resolver`) as a side effect — see `spec.py`.
Anything that imports `app.features.settings.feature` (e.g.
`dotmac_kernel.features.load_manifests`) triggers this import first, since Python
always imports a parent package before a submodule.
"""

from __future__ import annotations

from app.features.settings import spec  # noqa: F401
