from app.core.features import FeatureManifest
from app.features.persons.router import router

feature = FeatureManifest(name="persons", routers=[router])
