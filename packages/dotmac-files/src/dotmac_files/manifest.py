"""Installable module declaration for the stored-file owner."""

from dotmac_kernel.modules import ModuleManifest

from dotmac_files.models import PLATFORM_TABLES, TENANT_TABLES

module = ModuleManifest(
    code="files",
    version="0.1.0a1",
    core=False,
    short_code="files",
    migration_prefix="fi",
    migration_branch="files",
    tables=TENANT_TABLES,
    platform_tables=PLATFORM_TABLES,
)

__all__ = ["module"]
