"""RBAC feature manifest: the audit read surface only.

Declares `rbac.audit.read`, the code-authoritative statement that reading the
tenant's audit trail is an authorization decision `rbac` owns; `router.py`
REFERENCES it via `Depends(require_permission(...))` and never invents a code,
and `create_app` refuses to boot if it does. `default_roles=("admin",)` (the
spec default) preserves the previous `require_role("admin")` behavior.

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
