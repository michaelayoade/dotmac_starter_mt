"""Runtime presentation projection manifest.

Stateless and assembly-owned: this is neither a Template Studio responsibility
nor a kernel facility. It mounts only the HTML-side stylesheet route and adds no
navigation, persistence, setting declaration, or business capability.
"""

from dotmac_kernel.features import FeatureManifest

from app.features.presentation.web import router

feature = FeatureManifest(
    name="presentation",
    web_routers=(router,),
    core=True,
)
