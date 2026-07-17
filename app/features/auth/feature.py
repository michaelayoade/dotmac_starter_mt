from app.core.features import FeatureManifest
from app.features.auth.router import router

feature = FeatureManifest(name="auth", routers=[router])
