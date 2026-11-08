"""Per-service desired access policy."""

from dotmac_service_access_policy.contracts import (
    AccessInputDecision,
    AccessSignal,
    DesiredAccess,
    RecordAccessInput,
    RecordAccessInputV2,
    ResolveDesiredAccess,
)
from dotmac_service_access_policy.manifest import module
from dotmac_service_access_policy.migrations import versions_dir
from dotmac_service_access_policy.models import (
    DesiredAccessDecision,
    ServiceAccessInput,
)
from dotmac_service_access_policy.service import (
    AccessInputRefused,
    RecordAccessResultV2,
    record_access_input,
    record_access_input_v2,
    resolve_desired_access,
)
from dotmac_service_access_policy.versioning import (
    StoredAccessStamp,
    decide_access_input,
    decide_unversioned_write,
)

__version__ = "0.1.0a2"
__all__ = [
    "AccessInputDecision",
    "AccessInputRefused",
    "AccessSignal",
    "DesiredAccess",
    "DesiredAccessDecision",
    "RecordAccessInput",
    "RecordAccessInputV2",
    "RecordAccessResultV2",
    "ResolveDesiredAccess",
    "ServiceAccessInput",
    "StoredAccessStamp",
    "__version__",
    "decide_access_input",
    "decide_unversioned_write",
    "module",
    "record_access_input",
    "record_access_input_v2",
    "resolve_desired_access",
    "versions_dir",
]
