"""Tenant-only declaration for the first canonical learning slice."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    PARTY_PERSON_CATALOG_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_learning.models import TENANT_TABLES

module = ModuleManifest(
    code="learning",
    version="0.1.0a1",
    core=False,
    short_code="learning",
    migration_prefix="lr",
    migration_branch="learning",
    tables=TENANT_TABLES,
    platform_tables=(),
    requires=(
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
        PARTY_PERSON_CATALOG_V1.name,
    ),
)

__all__ = ["module"]
