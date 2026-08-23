"""Tenant-only module declaration for the Dotmac hosting-service owner."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    IDEMPOTENCY_LEDGER_V1,
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    TENANT_AUDIT_LOG_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_hosting.models import TABLES
from dotmac_hosting.service import (
    PUBLIC_EVENT_TYPES,
    PACKAGE_AUDIT_ACTION,
    REPAIR_AUDIT_ACTION,
    RETENTION_AUDIT_ACTION,
    SUSPENSION_AUDIT_ACTION,
    TERMINATION_AUDIT_ACTION,
)

module = ModuleManifest(
    code="hosting",
    version="0.1.0a1",
    core=False,
    short_code="hosting",
    migration_prefix="ho",
    migration_branch="hosting",
    tables=TABLES,
    platform_tables=(),
    audit_actions=(
        RETENTION_AUDIT_ACTION,
        PACKAGE_AUDIT_ACTION,
        SUSPENSION_AUDIT_ACTION,
        TERMINATION_AUDIT_ACTION,
        REPAIR_AUDIT_ACTION,
    ),
    outbox_event_types=PUBLIC_EVENT_TYPES,
    requires=(
        MODULE_DATABASE_ROLES_V1.name,
        IDEMPOTENCY_LEDGER_V1.name,
        OUTBOX_RELAY_V1.name,
    ),
    tenant_requires=(
        TENANT_SCOPE_CATALOG_V1.name,
        TENANT_AUDIT_LOG_V1.name,
    ),
)

__all__ = ["module"]
