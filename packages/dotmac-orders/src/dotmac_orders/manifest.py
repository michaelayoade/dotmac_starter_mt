"""Installable declaration for the tenant-only Orders owner."""

from dotmac_kernel.modules import ModuleManifest
from dotmac_kernel.prerequisites import (
    IDEMPOTENCY_LEDGER_V1,
    MODULE_DATABASE_ROLES_V1,
    OUTBOX_RELAY_V1,
    TENANT_AUDIT_LOG_V1,
    TENANT_SCOPE_CATALOG_V1,
)

from dotmac_orders.models import TENANT_TABLES

module = ModuleManifest(
    code="orders",
    version="0.1.0a1",
    core=False,
    short_code="orders",
    migration_prefix="or",
    migration_branch="orders",
    tables=TENANT_TABLES,
    requires=(
        MODULE_DATABASE_ROLES_V1.name,
        IDEMPOTENCY_LEDGER_V1.name,
        OUTBOX_RELAY_V1.name,
    ),
    tenant_requires=(TENANT_SCOPE_CATALOG_V1.name, TENANT_AUDIT_LOG_V1.name),
    audit_actions=(
        "orders.submitted",
        "orders.accepted",
        "orders.cancelled",
        "orders.cancellation_refused",
        "orders.coverage_observed",
        "orders.fulfillment_acknowledged",
        "orders.fulfillment_reconciled",
    ),
)

__all__ = ["module"]
