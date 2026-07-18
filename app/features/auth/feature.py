from app.core.features import FeatureManifest
from app.features.auth.router import router
from app.features.auth.web import router as web_router

feature = FeatureManifest(name="auth", routers=[router, web_router])
