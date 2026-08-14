"""Installable module declaration for the approval-state owner.

`mod_approvals` / prefix `ap` / branch label `approvals` are allocated in
`dotmac_kernel.namespaces.MIGRATION_OWNER_LEDGER` (`APPROVALS_MIGRATION_OWNER`,
kernel `0.1.0a59`), and this manifest must match that row exactly or the module
cannot register at all.

Both plane tuples are populated. That is the ADR-0023 case this module exists to
demonstrate honestly: approvals are a real tenant capability in ERP AND a real
control-plane capability in the vendor control plane, so both planes are
DECLARED rather than one being inferred from a missing column.
"""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    MODULE_DATABASE_ROLES_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_approvals.models import PLATFORM_TABLES, TENANT_TABLES

module = ModuleManifest(
    code="approvals",
    version="0.1.0a1",
    core=False,
    short_code="approvals",
    migration_prefix="ap",
    migration_branch="approvals",
    tables=TENANT_TABLES,
    platform_tables=PLATFORM_TABLES,
    # An approval needs a tenant to hang a foreign key on and roles to grant to.
    # Nothing else — deliberately not an identity or RBAC estate: role
    # membership arrives on the `Actor` value at the call site, so this module
    # installs beside a product whose RBAC lives somewhere the kernel has never
    # seen.
    requires=(TENANT_SCOPE_CATALOG_V1.name, MODULE_DATABASE_ROLES_V1.name),
)

__all__ = ["module"]
