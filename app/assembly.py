"""The reference assembly's ProductAssemblySpec (kernel-boundary Task 3A).

This is the one place the reference application declares what it IS: its feature
modules (`FEATURE_MODULES`, assembly-owned), plus the deployment-driven surface
switches read from the environment. `app/main.py` builds `app` from this spec
via `dotmac_kernel.create_app`.

The spec deliberately leaves `assembly_template_dir` / `assembly_static_dir`
unset — the reference app renders and serves the kernel's packaged templates and
static assets directly, with no override (byte-identical to the pre-Task-3
behavior). A downstream product sets those to layer its own look over the
kernel's.
"""

from __future__ import annotations

from dotmac_kernel.assembly import ProductAssemblySpec
from dotmac_kernel.config import settings
from dotmac_kernel.features import load_manifests

from app.features import FEATURE_MODULES

assembly = ProductAssemblySpec(
    name="dotmac_starter_mt",
    modules=load_manifests(FEATURE_MODULES),
    web_enabled=settings.web_enabled,
    disabled_modules=frozenset(settings.disabled_feature_set),
)
