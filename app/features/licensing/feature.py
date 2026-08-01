from dotmac_kernel.features import FeatureManifest

from app.features.licensing.router import router

feature = FeatureManifest(
    name="licensing",
    routers=[router],
)
