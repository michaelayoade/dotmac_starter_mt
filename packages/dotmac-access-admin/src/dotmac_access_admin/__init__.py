"""Public surface of ``dotmac-access-admin``.

Users, roles, grants and leaver revoke over the kernel identity tables. The
manifest is imported lazily by assemblies (`dotmac_access_admin.module`).
"""

from typing import Final

from dotmac_access_admin.manifest import module
from dotmac_access_admin.roles import (
    register_sod_pairs,
    registered_sod_pairs,
    validate_composition,
)

__version__: Final[str] = "0.1.0a1"

__all__ = [
    "__version__",
    "module",
    "register_sod_pairs",
    "registered_sod_pairs",
    "validate_composition",
]
