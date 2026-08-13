"""Installable module declaration for the import-run ledger."""

from dotmac_kernel.modules import ModuleManifest

from dotmac_imports.models import TENANT_TABLES

module = ModuleManifest(
    code="imports",
    version="0.1.0a1",
    core=False,
    short_code="imports",
    migration_prefix="im",
    migration_branch="imports",
    tables=TENANT_TABLES,
)

__all__ = ["module"]
