"""Per-service desired access policy."""

from dotmac_service_access_policy.contracts import (
    AccessSignal,
    DesiredAccess,
    RecordAccessInput,
    ResolveDesiredAccess,
)
from dotmac_service_access_policy.manifest import module
from dotmac_service_access_policy.migrations import versions_dir
from dotmac_service_access_policy.models import (
    DesiredAccessDecision,
    ServiceAccessInput,
)
from dotmac_service_access_policy.service import (
    record_access_input,
    resolve_desired_access,
)

__version__ = "0.1.0a1"
__all__ = [
    "AccessSignal",
    "DesiredAccess",
    "DesiredAccessDecision",
    "RecordAccessInput",
    "ResolveDesiredAccess",
    "ServiceAccessInput",
    "__version__",
    "module",
    "record_access_input",
    "resolve_desired_access",
    "versions_dir",
]
