from app.core.features import FeatureManifest
from app.features.parties.router import router
from app.features.parties.web import router as web_router

feature = FeatureManifest(name="parties", routers=[router, web_router])
