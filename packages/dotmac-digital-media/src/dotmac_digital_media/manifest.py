"""Tenant-only module declaration for Digital Media."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_digital_media.models import TABLES

module = ModuleManifest(
    code="digital_media",
    version="0.1.0a1",
    core=False,
    short_code="digitalmedia",
    migration_prefix="dm",
    migration_branch="digital_media",
    tables=TABLES,
    requires=(
        TENANT_SCOPE_CATALOG_V1.name,
        MODULE_DATABASE_ROLES_V1.name,
    ),
)

__all__ = ["module"]
