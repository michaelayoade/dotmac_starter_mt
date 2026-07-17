from app.core.features import FeatureManifest
from app.features.rbac.router import router

feature = FeatureManifest(name="rbac", routers=[router])
