from dotmac_kernel.features import FeatureManifest

from app.features.licensing.router import router

feature = FeatureManifest(
    name="licensing",
    routers=[router],
    # Both WS8 receiver outcomes are declared, not just the happy path: a
    # REJECTED licence or revocation import is exactly the event an operator
    # needs in the trail, so "rejected" is a first-class action, never an
    # undeclared fallback (module control-plane directive step 3).
    audit_actions=[
        "licence.applied",
        "licence.rejected",
        "licence.revocation_imported",
        "licence.revocation_rejected",
    ],
)
