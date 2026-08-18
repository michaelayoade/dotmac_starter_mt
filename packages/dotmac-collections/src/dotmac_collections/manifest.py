"""Installable declaration for the tenant-only Collections owner."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_collections.models import PLATFORM_TABLES, TENANT_TABLES

module = ModuleManifest(
    code="collections",
    version="0.1.0a1",
    core=False,
    short_code="coll",
    migration_prefix="cl",
    migration_branch="collections",
    tables=TENANT_TABLES,
    platform_tables=PLATFORM_TABLES,
    requires=(MODULE_DATABASE_ROLES_V1.name, OUTBOX_RELAY_V1.name),
    tenant_requires=(TENANT_SCOPE_CATALOG_V1.name,),
    platform_requires=(),
    supported_plane_sets=(),
)

__all__ = ["module"]
