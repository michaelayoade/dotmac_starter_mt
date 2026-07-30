from dotmac_kernel.features import FeatureManifest, NavItem

from app.features.rbac.router import router
from app.features.rbac.web import router as web_router

feature = FeatureManifest(
    name="rbac",
    routers=[router],
    web_routers=[web_router],
    nav=[
        NavItem("Roles", "/admin/roles"),
        NavItem("Role Grants", "/admin/role-grants"),
        NavItem("Audit Log", "/admin/audit"),
    ],
)
