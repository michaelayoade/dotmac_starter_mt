"""Comms operations feature manifest (K-7: consent/receipt operator surface).

Declares and OWNS the vocabulary this surface uses: three permissions (read
suppressions, read receipts, restore transactional mail) and one audit action,
and owns the whole `comms_operations.` permission namespace. Restoring is an
operator decision on a real business case with a recorded reason (scope `all`
-> `marketing`; the row is kept); the surface adds no queue and no second
approver.
"""

from dotmac_kernel.features import FeatureManifest, NavItem
from dotmac_kernel.permissions import PermissionSpec

from app.features.comms_operations.web import router as web_router

feature = FeatureManifest(
    name="comms_operations",
    web_routers=[web_router],
    nav=[NavItem("Suppressions", "/admin/comms/suppressions")],
    permissions=[
        PermissionSpec(
            code="comms_operations.suppressions.read",
            description="List the tenant's communication suppressions.",
        ),
        PermissionSpec(
            code="comms_operations.receipts.read",
            description="Read the delivery receipts recorded for an address.",
        ),
        PermissionSpec(
            code="comms_operations.suppressions.manage",
            description=(
                "Restore transactional mail to a suppressed address (scope "
                "all -> marketing; the row is kept, marketing stays blocked) "
                "with a recorded reason. Erasure rows and marketing opt-outs "
                "cannot be restored."
            ),
        ),
    ],
    audit_actions=["communication.suppression.transactional_restored"],
)
