"""Installable module declaration for the stored-file owner."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_files.models import PLATFORM_TABLES, TENANT_TABLES

module = ModuleManifest(
    code="files",
    version="0.1.0a2",
    core=False,
    short_code="files",
    migration_prefix="fi",
    migration_branch="files",
    tables=TENANT_TABLES,
    platform_tables=PLATFORM_TABLES,
    # Stored bytes need a tenant to hang a foreign key on and roles to grant to
    # — not an identity estate. Naming the effects instead of kernel revision
    # `0001` is what lets this module install into an assembly that supplies
    # them from its own lineage.
    requires=(TENANT_SCOPE_CATALOG_V1.name, MODULE_DATABASE_ROLES_V1.name),
)

__all__ = ["module"]
