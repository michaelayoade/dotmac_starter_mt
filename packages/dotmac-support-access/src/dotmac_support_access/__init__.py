"""Public surface for ``dotmac-support-access``."""

from dotmac_support_access.contracts import (
    AccessMode,
    FiniteGrantDescriptor,
    SupportRequestInput,
)
from dotmac_support_access.manifest import module
from dotmac_support_access.migrations import versions_dir
from dotmac_support_access.service import (
    AccessRefused,
    admit_request,
    create_request,
    expire_grants,
    revoke_grant,
)

__version__ = "0.1.0a1"

__all__ = [
    "__version__",
    "AccessMode",
    "AccessRefused",
    "FiniteGrantDescriptor",
    "SupportRequestInput",
    "admit_request",
    "create_request",
    "expire_grants",
    "module",
    "revoke_grant",
    "versions_dir",
]
