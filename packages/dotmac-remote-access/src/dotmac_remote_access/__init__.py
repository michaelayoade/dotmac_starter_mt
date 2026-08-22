"""Public surface for ``dotmac-remote-access``."""

from dotmac_remote_access.contracts import RemoteAccessIntent, RemoteAccessRequestInput
from dotmac_remote_access.manifest import module
from dotmac_remote_access.migrations import versions_dir
from dotmac_remote_access.service import (
    AccessRefused,
    admit_request,
    create_request,
    expire_grants,
    record_observation,
    revoke_grant,
)

__version__ = "0.1.0a1"

__all__ = [
    "__version__",
    "AccessRefused",
    "RemoteAccessIntent",
    "RemoteAccessRequestInput",
    "admit_request",
    "create_request",
    "expire_grants",
    "module",
    "record_observation",
    "revoke_grant",
    "versions_dir",
]
