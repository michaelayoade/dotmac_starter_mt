"""RBAC feature manifest.

First feature to declare `permissions` (module control-plane directive step 3).
Each `PermissionSpec` below is the code-authoritative statement that an
authorization decision exists and that `rbac` owns it; `router.py` REFERENCES
those codes via `Depends(require_permission(...))` and never invents one, and
`create_app` refuses to boot if it does. `default_roles=("admin",)` (the spec
default) on every entry preserves the previous `require_role("admin")` behavior
exactly — this change moves where the decision is DECLARED, not who passes it.

Role and grant administration (and the `role.create`/`role.grant` audit
actions) moved to `dotmac-access-admin`; this feature keeps only the audit
read endpoint and screen, and the `audit` setting domain.
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
        NavItem("Audit Log", "/admin/audit"),
    ],
    permissions=[
        PermissionSpec(
            code="rbac.audit.read",
            description="Read the tenant's audit trail.",
        ),
    ],
    audit_actions=[],
)
