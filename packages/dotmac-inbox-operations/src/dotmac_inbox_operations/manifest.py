"""Manifest for staffed inbox operations."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_inbox_operations.models import TENANT_TABLES

module = ModuleManifest(
    code="inbox_operations",
    version="0.1.0a3",
    core=False,
    short_code="inbox_ops",
    migration_prefix="io",
    migration_branch="inbox_operations",
    tables=TENANT_TABLES,
    platform_tables=(),
    requires=(TENANT_SCOPE_CATALOG_V1.name, MODULE_DATABASE_ROLES_V1.name),
)

__all__ = ["module"]
