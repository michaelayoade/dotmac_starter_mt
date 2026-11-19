"""Comms operations feature manifest (K-7: consent/receipt operator surface).

Declares and OWNS the vocabulary this surface uses: three permissions (read
suppressions, read receipts, clear a suppression) and one audit action. A
suppression is cleared by an operator on a real business case, with a recorded
reason; the surface adds no queue and no second approver.
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
            code="comms.suppressions.read",
            description="List the tenant's communication suppressions.",
        ),
        PermissionSpec(
            code="comms.receipts.read",
            description="Read the delivery receipts recorded for an address.",
        ),
        PermissionSpec(
            code="comms.suppressions.manage",
            description=(
                "Clear a communication suppression (including a bounce or "
                "complaint) with a recorded reason."
            ),
        ),
    ],
    audit_actions=["communication.suppression.cleared"],
)
