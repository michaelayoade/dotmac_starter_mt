"""Tenant-only module declaration for the Dotmac domain-service owner."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    IDEMPOTENCY_LEDGER_V1,
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    TENANT_AUDIT_LOG_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_domains.models import TABLES
from dotmac_domains.service import (
    HOLD_AUDIT_ACTION,
    PUBLIC_EVENT_TYPES,
    TRANSFER_OUT_AUDIT_ACTION,
)

module = ModuleManifest(
    code="domains",
    version="0.1.0a1",
    core=False,
    short_code="domains",
    migration_prefix="do",
    migration_branch="domains",
    tables=TABLES,
    platform_tables=(),
    audit_actions=(
        TRANSFER_OUT_AUDIT_ACTION,
        HOLD_AUDIT_ACTION,
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
