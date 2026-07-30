from dotmac_kernel.features import FeatureManifest, NavItem

from app.features.parties.router import router
from app.features.parties.web import router as web_router

feature = FeatureManifest(
    name="parties",
    routers=[router],
    web_routers=[web_router],
    nav=[NavItem("People", "/admin/parties")],
)
