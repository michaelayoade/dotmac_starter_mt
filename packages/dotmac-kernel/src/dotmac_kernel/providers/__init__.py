"""Kernel provider seams — product-neutral Protocols a product assembly binds
concrete implementations to.

Only ONE seam ships in the kernel alpha: ``provisioning`` (ruling C6 — pulled
forward for the vendor control plane). Every OTHER provider seam stays empty
until workstream 5 (contracts-not-implementations). Concrete providers and
fakes live OUTSIDE the kernel; this package owns only the shape of the
contract.

The canonical import is the submodule
(``from dotmac_kernel.providers.provisioning import ProvisioningProvider``);
the provisioning surface is re-exported here for convenience.
"""

from __future__ import annotations

from dotmac_kernel.providers.provisioning import (
    ApplyResult,
    ObserveResult,
    PlanResult,
    ProvisioningApplyError,
    ProvisioningCancelled,
    ProvisioningError,
    ProvisioningPlanError,
    ProvisioningProvider,
    ProvisioningRequest,
    ProvisioningRetryableError,
    ProvisioningStatus,
    ProvisioningStep,
    ProvisioningTerminalError,
    StepStatus,
)

__all__ = [
    "ProvisioningProvider",
    "ProvisioningRequest",
    "ProvisioningStep",
    "PlanResult",
    "ApplyResult",
    "ObserveResult",
    "ProvisioningStatus",
    "StepStatus",
    "ProvisioningError",
    "ProvisioningRetryableError",
    "ProvisioningTerminalError",
    "ProvisioningPlanError",
    "ProvisioningApplyError",
    "ProvisioningCancelled",
]
