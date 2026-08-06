from dotmac_kernel.features import FeatureManifest

from app.features.tenants.router import router

feature = FeatureManifest(
    name="tenants",
    routers=[router],
    # Written by `service.provision_tenant` — the platform-initiated tenant
    # creation trail. Declared here because the kernel rejects any audit action
    # no installed module owns (module control-plane directive step 3).
    audit_actions=["platform.tenant.create", "platform.tenant.owner_provision"],
)
