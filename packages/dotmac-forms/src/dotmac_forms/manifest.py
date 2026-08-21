"""Installable declaration for the tenant Forms owner."""

from dotmac_forms.models import TENANT_TABLES
from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import MODULE_DATABASE_ROLES_V1, TENANT_SCOPE_CATALOG_V1

module = ModuleManifest(
    code="forms",
    version="0.1.0a1",
    core=False,
    short_code="forms",
    migration_prefix="fm",
    migration_branch="forms",
    tables=TENANT_TABLES,
    platform_tables=(),
    requires=(TENANT_SCOPE_CATALOG_V1.name, MODULE_DATABASE_ROLES_V1.name),
)

__all__ = ["module"]
