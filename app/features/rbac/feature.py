from app.core.features import FeatureManifest
from app.features.rbac.router import router
from app.features.rbac.web import router as web_router

feature = FeatureManifest(name="rbac", routers=[router, web_router])
