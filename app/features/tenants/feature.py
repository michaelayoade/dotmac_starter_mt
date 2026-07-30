from dotmac_kernel.features import FeatureManifest

from app.features.tenants.router import router

feature = FeatureManifest(name="tenants", routers=[router])
