from app.core.features import FeatureManifest
from app.features.parties.router import router

feature = FeatureManifest(name="parties", routers=[router])
