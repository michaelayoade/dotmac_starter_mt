"""RBAC feature manifest.

First feature to declare `permissions` (module control-plane directive step 3).
Each `PermissionSpec` below is the code-authoritative statement that an
authorization decision exists and that `rbac` owns it; `router.py` REFERENCES
those codes via `Depends(require_permission(...))` and never invents one, and
`create_app` refuses to boot if it does. `default_roles=("admin",)` (the spec
default) on every entry preserves the previous `require_role("admin")` behavior
exactly — this change moves where the decision is DECLARED, not who passes it.

`audit_actions` does the same for the trail: these are the two actions this
feature's routes and screens write via `write_audit_event`, and the kernel
rejects any other action written anywhere.
"""

from dotmac_kernel.features import FeatureManifest, NavItem
from dotmac_kernel.permissions import PermissionSpec

from app.features.rbac.router import router
from app.features.rbac.web import router as web_router

feature = FeatureManifest(
    # Owns the `audit` setting domain: its spec lives in this feature
    # (`spec.py`), and a declaration follows its owner — ADR-0008.
    setting_domains=("audit",),
    name="rbac",
    routers=[router],
    web_routers=[web_router],
    nav=[
        NavItem("Roles", "/admin/roles"),
        NavItem("Role Grants", "/admin/role-grants"),
        NavItem("Audit Log", "/admin/audit"),
    ],
    permissions=[
        PermissionSpec(
            code="rbac.roles.read",
            description="List the tenant's roles.",
        ),
        PermissionSpec(
            code="rbac.roles.manage",
            description="Create and modify the tenant's roles.",
        ),
        PermissionSpec(
            code="rbac.grants.manage",
            description="Grant a role to a party.",
        ),
        PermissionSpec(
            code="rbac.audit.read",
            description="Read the tenant's audit trail.",
        ),
    ],
    audit_actions=["role.create", "role.grant"],
)
